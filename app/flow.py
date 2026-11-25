"""Conversation engine: questions 24/7 and orders tied to stock.

`Flow.handle` reads the conversation context, returns replies and mutates the
context. Orders are created inside the caller's DB transaction so stock is
checked and reserved atomically.
"""
import json
import re
from datetime import date
from sqlalchemy import select, func
from app.db import Order, Audit
from app.hours import Schedule
from app.menu import normalized, parse_items, menu_text, cart_text, money
from app import stock as stockmod

STATUS_LABEL = {'nuevo': 'recibido, esperando confirmación', 'confirmado': 'confirmado',
                'en_cocina': 'en preparación 👨‍🍳', 'listo': 'listo', 'en_camino': 'en camino 🛵',
                'entregado': 'entregado', 'cancelado': 'cancelado'}

RE_GREETING = re.compile(r'^(hola+|holis|buenas?|buen dia|buenas (tardes|noches)|hey|que tal|ola)\b')
RE_THANKS = re.compile(r'^(gracias|muchas gracias|genial|perfecto|dale|ok|okey|joya|listo)[ !.]*$')
RE_HUMAN = re.compile(r'\b(humano|persona|encargad[oa]|operador|hablar con (alguien|una persona)|atencion al cliente|reclamo|queja)\b')
RE_CANCEL = re.compile(r'^(cancelar|cancela|cancelo|anular|anulo|olvidalo|no quiero nada|salir)\b')
RE_MENU = re.compile(r'\b(carta|menu|precios?|que (?:tienen|hay|venden)(?:\?|$)|que burgers? (?:tienen|hay))')
RE_ORDER = re.compile(r'\b(pedir|pedido|encargar|quiero|queria|quisiera|me (das|das|mandas|traes)|para llevar|reservar)\b')
RE_HOURS = re.compile(r'\b(horario|hora|abren|abiertos?|cierran|atienden|hasta que hora|que dias|martes|hoy abren|feriado|feriados|ultimo pedido)\b')
RE_ADDRESS = re.compile(r'\b(direccion|donde (estan|queda|quedan)|ubicacion|ubicados|local|salon)\b')
RE_DELIVERY = re.compile(r'\b(delivery|envios?|envian|envias|llevan|llegan|reparto|zona|domicilio|take ?away|retiro|retirar|buscar|demora|tarda)\b')
RE_PAY = re.compile(r'\b(pago|pagar|transferencia|transferir|efectivo|alias|cbu|mercado ?pago|tarjeta|debito|credito|factura|cambio|vuelto)\b')
RE_COMBO = re.compile(r'\b(combos?)\b')
RE_PROMO = re.compile(r'\b(promos?|promociones?|descuentos?)\b')
RE_DRINK = re.compile(r'\b(bebidas?|gaseosas?|cocas?|sprite|agua|cervezas?|birras?|tragos?)\b')
RE_STATUS = re.compile(r'\b(mi pedido|estado|ya sale|donde esta mi)\b')
RE_PEDIDOSYA = re.compile(r'pedidos ?ya')
RE_DONE = re.compile(r'^(listo|eso|eso es todo|nada mas|no|no gracias|nada|seria todo|es todo|ya esta|solo eso|terminar|cart_done)\b')
RE_YES = re.compile(r'^(si|sii+|confirmo|confirmar|dale|ok|okey|de una|correcto|perfecto|confirm_yes)\b')
RE_DAY_WORDS = re.compile(r'\b(para|el|del|este|proximo|hoy|manana|pasado|lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b')


class Flow:
    def __init__(self, s, db, negocio, items, now_ts, llm=None, promos=None):
        self.s, self.db, self.negocio, self.items = s, db, negocio, items
        self.by_id = {i.id: i for i in items}
        self.schedule = Schedule(negocio)
        self.now_ts = now_ts
        self.now = self.schedule.local(now_ts)
        self.llm = llm
        self.promos = promos or []
        self.replies = []
        self.flags = set()  # needs_human

    # ---------- helpers
    def say(self, text, buttons=None):
        # Messages with buttons are limited to ~640 characters: send long text plain, buttons after.
        if buttons and len(text) > 600:
            self.replies.append({'text': text, 'buttons': None})
            self.replies.append({'text': '👇 Elegí una opción:', 'buttons': buttons})
        else:
            self.replies.append({'text': text, 'buttons': buttons or None})
        return True

    def human(self, text=None):
        self.flags.add('needs_human')
        if self.schedule.is_open(self.now):
            self.say(text or '¡Listo! Ya le aviso al equipo y te escriben por acá en un ratito 🙌')
        else:
            self.say(text or ('¡Listo! Le dejé tu mensaje al equipo. Ahora estamos cerrados; te responden en el '
                              'horario de atención. Mientras tanto yo sigo tomando pedidos y consultas 🍔'))
        return True

    def _service_date(self, ctx):
        return date.fromisoformat(ctx['service_date']) if ctx.get('service_date') else None

    def _stock(self, service_date):
        return stockmod.remaining(self.s, service_date.isoformat())

    def _promo_for(self, item_id):
        matches = [promo for promo in self.promos if promo.product_id == item_id]
        if not matches:
            return None
        return max(matches, key=lambda promo: (promo.discount_percent, promo.ends_at))

    def _price_for(self, item, variant_id=None):
        base = item.price_for(variant_id)
        promo = self._promo_for(item.id)
        if promo and base is not None:
            return max(0, int(round(base * (100 - promo.discount_percent) / 100)))
        return base

    def _allowed(self, item, variant_id, service_date):
        if self._promo_for(item.id):
            return item.available and item.price_for(variant_id) is not None
        return item.orderable(service_date, variant_id)

    def _day_text(self, d):
        if d == self.now.date():
            return f'hoy {self.schedule.day_label(d)} {d:%d/%m}'
        return f'{self.schedule.day_label(d)} {d:%d/%m}'

    def _is_day_request(self, text):
        if not RE_DAY_WORDS.search(normalized(text)):
            return False
        return self.schedule.resolve_day(text, self.now) is not None

    def _faq_match(self, t):
        best = None
        for entry in self.negocio.get('faq') or []:
            for keyword in entry.get('keywords') or []:
                k = normalized(keyword)
                if not k:
                    continue
                if re.search(r'(?<![a-z0-9])' + re.escape(k) + r'(?![a-z0-9])', t):
                    if best is None or len(k) > best[0]:
                        best = (len(k), entry)
        return best[1] if best else None

    def _faq_answer(self, entry):
        if entry.get('derivar'):
            return self.human(entry['respuesta'])
        return self.say(entry['respuesta'])

    # ---------- entry point
    async def handle(self, ctx, text, kind='text', name=''):
        t = normalized(text)
        if kind == 'audio':
            self.say('No puedo escuchar audios 🙈 ¿Me lo escribís, porfa?')
            return ctx
        if kind in {'image', 'document'}:
            return self._attachment(ctx)
        if not t:
            return ctx
        if RE_CANCEL.match(t):
            if ctx.get('step'):
                self._reset(ctx)
                self.say('Listo, cancelé el pedido que estábamos armando. Cuando quieras, arrancamos de nuevo 🍔')
            else:
                self._cancel_last(ctx)
            return ctx
        if RE_HUMAN.search(t):
            self.human()
            return ctx
        step = ctx.get('step')
        if step:
            handled = await self._step(ctx, step, text, t, name)
            if handled:
                return ctx
        await self._general(ctx, text, t)
        if step and ctx.get('step') == step:
            self.say('👉 Cuando quieras seguimos con tu pedido (o escribí *cancelar*).')
        return ctx

    # ---------- general questions and order start
    async def _general(self, ctx, text, t):
        parsed = parse_items(text, self.items)
        if parsed and self._is_question(text, t):
            # "¿Qué trae la Mexicana?" is a question about a product, not an order.
            if RE_ORDER.search(t) and not re.search(r'\b(trae|tiene|lleva|viene|vienen|pica|es|son|cuanto|cuesta|sale|precio)\b', t):
                return self._start_order(ctx, parsed, text)
            if re.search(r'\b(pica|picante)\b', t):
                entry = self._faq_match(t)
                if entry:
                    return self._faq_answer(entry)
            return self._describe(ctx, [(i, v) for i, _, _, v in parsed])
        if parsed:
            return self._start_order(ctx, parsed, text)
        if t in {'menu', 'carta', 'ver carta'}:
            return self._send_menu(ctx)
        if t in {'pedir', 'hacer pedido', 'quiero pedir'}:
            return self._start_order(ctx, [], text)
        if t == 'horarios':
            return self.say(self._hours_text())
        if RE_GREETING.match(t) and len(t) < 40:
            return self.say(self.negocio['welcome'] + '\n\n' + self._hours_line() + '\n¿Qué querés hacer?',
                            [{'title': 'Ver carta', 'payload': 'menu'},
                             {'title': 'Hacer pedido', 'payload': 'pedir'},
                             {'title': 'Horarios', 'payload': 'horarios'}])
        if RE_THANKS.match(t):
            return self.say('¡De nada! Cualquier cosa escribime 🍔')
        rule = self._rule_answer(ctx, t)
        if rule:
            return rule
        result = await self.llm.interpret(text, self.items, self.negocio, None) if self.llm else None
        if result:
            if result['items']:
                pairs = []
                for x in result['items']:
                    item = self.by_id[x['id']]
                    variant = x.get('variant_id')
                    if variant not in {v.id for v in item.variants}:
                        variant = item.variants[0].id if item.variants else None
                    pairs.append((item, x['qty'], x.get('note', ''), variant))
                return self._start_order(ctx, pairs, text)
            intent = result['intent']
            if intent == 'consulta' and result['answer']:
                return self.say(result['answer'])
            mapped = {'carta': 'menu', 'pedido': 'pedir', 'horarios': 'horarios'}
            if intent in mapped:
                return await self._general(ctx, mapped[intent], mapped[intent])
            if intent == 'humano':
                return self.human()
            if intent == 'saludo':
                return await self._general(ctx, 'hola', 'hola')
            rule = self._rule_answer(ctx, intent)
            if rule:
                return rule
        self.human(self.negocio.get('unknown_reply'))

    def _rule_answer(self, ctx, t):
        if RE_STATUS.search(t) or t == 'estado':
            return self._status(ctx)
        if RE_MENU.search(t) and not RE_COMBO.search(t) and not RE_PROMO.search(t) \
                and not re.search(r'menu (de|para) (chicos|ninos|infantil)', t):
            return self._send_menu(ctx)
        if RE_PROMO.search(t) and not RE_COMBO.search(t):
            entry = self._faq_match(t)
            if entry:
                return self._faq_answer(entry)
        if RE_COMBO.search(t) or t == 'combos':
            return self.say(self._combos_text())
        if RE_PEDIDOSYA.search(t):
            if re.search(r'\b(caro|caros|comision|comisiones|precio|precios)\b', t):
                entry = self._faq_match(t)
                if entry:
                    return self._faq_answer(entry)
            return self.say('También estamos en PedidosYa 🛵, pero si pedís por acá lo hacés directo con nosotros. '
                            '¿Querés ver la carta?', [{'title': 'Ver carta', 'payload': 'menu'}])
        if RE_DELIVERY.search(t) or t == 'delivery':
            entry = self._faq_match(t)
            if entry and re.search(r'envio gratis|pedido minimo|quien reparte|repartidor', t):
                return self._faq_answer(entry)
            parts = []
            if self.negocio.get('delivery_enabled'):
                parts.append('🛵 ' + (
                    f"El envío cuesta {money(self.negocio['delivery_fee'])}." if self.negocio.get('delivery_fee') is not None
                    else self.negocio.get('delivery_note', '')))
            if self.negocio.get('takeaway_enabled'):
                parts.append(f"🥡 Retiro en el local: {self.negocio['address']}. "
                             f"{self.negocio.get('takeaway_note', '')}".strip())
            return self.say('\n'.join(parts) + '\n\n¿Querés hacer un pedido?',
                            [{'title': 'Hacer pedido', 'payload': 'pedir'}])
        if RE_PAY.search(t) or t == 'pagos':
            return self.say(self._payment_text())
        if RE_HOURS.search(t) or t == 'horarios':
            return self.say(self._hours_text())
        if RE_DRINK.search(t):
            drinks = self._drinks_text()
            if drinks:
                return self.say(drinks)
        entry = self._faq_match(t)
        if entry:
            return self._faq_answer(entry)
        if RE_ADDRESS.search(t) or t == 'direccion':
            maps = f"\n📍 {self.negocio['maps_url']}" if self.negocio.get('maps_url') else ''
            return self.say(f"Estamos en *{self.negocio['address']}*.{maps}\n{self._hours_line()}")
        if RE_ORDER.search(t):
            return self._start_order(ctx, [], t)
        return None

    def _hours_line(self):
        return '🕗 ' + self.schedule.hours_text()

    def _hours_text(self):
        state = '🟢 Ahora estamos abiertos y tomando pedidos.' if self.schedule.is_open(self.now) else ''
        window = self.schedule.order_window(self.now)
        if not state and window.mode == 'preorder':
            state = (f'Hoy abrimos a las {self.schedule.open:%H:%M}, pero ya podés dejar tu pedido '
                     'anotado y elegir el horario 🙌')
        elif not state:
            state = window.message
        return (f'{self._hours_line()}\n{state}\n'
                f'Último pedido: {self.schedule.last_order:%H:%M}. Pedidos anticipados desde las '
                f'{self.schedule.intake:%H:%M}. Abrimos feriados; los martes cerramos.').strip()

    def _payment_text(self):
        methods = ', '.join(self.negocio.get('payment_methods') or [])
        lines = [f'Podés pagar con: {methods}.']
        if self.negocio.get('transfer_alias'):
            holder = (f" (titular: {self.negocio['transfer_holder']})"
                      if self.negocio.get('transfer_holder') else '')
            lines.append(f"Alias para transferir: *{self.negocio['transfer_alias']}*{holder}.")
        if self.negocio.get('advance_payment_required'):
            lines.append('Los pedidos anticipados se abonan antes.')
        if self.negocio.get('cash_change'):
            lines.append('El repartidor lleva cambio.')
        if self.negocio.get('invoice') is False:
            lines.append('No hacemos factura.')
        if self.negocio.get('card_surcharge'):
            lines.append('Consultanos por tarjeta: tiene recargo.')
        return '\n'.join(lines)

    def _drinks_text(self):
        drinks = [i for i in self.items if i.kind == 'drink' and i.available and i.price is not None]
        if not drinks:
            return None
        lines = '\n'.join(f'• {i.name} — {money(i.price)}' for i in drinks)
        return f'🥤 *Bebidas:*\n{lines}\n\n¿Querés sumar alguna al pedido?'

    def _combos_text(self):
        combos = [i for i in self.items if i.days and i.price is not None and i.available]
        if not combos:
            return 'Ahora no tenemos combos cargados. ¿Querés ver la carta?'
        lines = [f'{i.code}. *{i.name}* — {money(i.price)}\n   {i.description}' for i in combos]
        days = ', '.join(combos[0].days)
        return ('🔥 *Combos* (válidos ' + days + ', delivery y retiro)\n' + '\n'.join(lines) +
                '\n\nElegís la variedad de cada burger (menos Crispy Chicken) y el dip: mayonesa de ajo, '
                'barbacoa o mostaza miel. No incluyen bebida.')

    def _send_menu(self, ctx):
        sd = self._service_date(ctx)
        if not sd:
            window = self.schedule.order_window(self.now)
            sd = window.service_date
        out = self._sold_out(sd) if sd else []
        url = self.negocio.get('menu_url')
        if url:
            text = f'🍔 *La carta completa, con fotos y precios:*\n{url}'
            if out:
                text += '\n\n❌ Agotado por hoy: ' + ', '.join(out)
            return self.say(text + '\n\n¿Querés que te tome el pedido?',
                            [{'title': 'Hacer pedido', 'payload': 'pedir'},
                             {'title': 'Ver combos', 'payload': 'combos'}])
        text = menu_text(self.items, sd)
        if out:
            text += '\n\n❌ Agotado por hoy: ' + ', '.join(out)
        return self.say(text)

    def _sold_out(self, sd):
        left = self._stock(sd)
        return [i.name for i in self.items if i.available
                and (i.price is not None or any(v.price is not None for v in i.variants))
                and stockmod.max_units(i, left, i.recipe_for()) == 0]

    def _status(self, ctx):
        last = self._last_order(ctx)
        if not last:
            return self.say('No encuentro pedidos tuyos recientes. ¿Querés hacer uno?',
                            [{'title': 'Hacer pedido', 'payload': 'pedir'}])
        when = f' para las {last.slot}' if last.slot and last.slot != 'ASAP' else ''
        return self.say(f'Tu pedido *#{last.number}*{when} está {STATUS_LABEL.get(last.status, last.status)}.')

    def _last_order(self, ctx):
        oid = ctx.get('last_order_id')
        return self.s.get(Order, oid) if oid else None

    def _cancel_last(self, ctx):
        last = self._last_order(ctx)
        if not last:
            return self.say('No tenés ningún pedido en curso. ¿Te ayudo con algo más?')
        if last.status not in {'nuevo', 'confirmado'}:
            if last.status == 'cancelado':
                return self.say('Tu pedido ya está cancelado. ¿Querés hacer otro?')
            return self.human('Ese pedido ya está en preparación. Te paso con el equipo para ver si se puede cancelar 🙌')
        if self.now_ts - last.created <= 10 * 60:
            stockmod.release(self.s, last.service_date, last.consumption)
            last.status, last.updated = 'cancelado', self.now_ts
            self.s.add(Audit(action='order_cancelled_by_customer', target=last.id))
            return self.say(f'Listo, cancelé el pedido *#{last.number}*. No se te cobra nada. '
                            'Si querés hacer otro, escribime 🍔')
        self.human('Pasaron más de 10 minutos desde que recibimos el pedido. '
                   'Te paso con el equipo para ver si se puede cancelar o modificar 🙌')

    def _attachment(self, ctx):
        last = self._last_order(ctx)
        if last and last.payment.lower().startswith('transf') and last.status not in {'entregado', 'cancelado'}:
            last.receipt_received = True
            self.flags.add('needs_human')
            self.say(f'¡Gracias! Recibimos el comprobante del pedido #{last.number}. El equipo lo verifica 🙌')
        else:
            self.human('Recibí tu archivo 👍 Se lo paso al equipo para que lo revise.')
        return ctx

    # ---------- order flow
    def _reset(self, ctx):
        for key in ('step', 'cart', 'mode', 'address', 'slot', 'payment', 'service_date',
                    'slot_options', 'requested_day', 'upsell_sent'):
            ctx.pop(key, None)

    @staticmethod
    def _is_question(text, t):
        return '?' in text or bool(re.match(r'(que|como|cuanto|cual|cuales|tiene|tienen|trae|lleva|viene|vienen|es|son|hay)\b', t))

    def _describe(self, ctx, pairs):
        lines = []
        seen = set()
        for item, variant_id in pairs:
            key = (item.id, variant_id)
            if key in seen:
                continue
            seen.add(key)
            price = item.price_for(variant_id)
            price_text = f' — {money(price)}' if price is not None else ''
            days = f" (solo {', '.join(item.days)})" if item.days else ''
            variants = ''
            if item.variants:
                variants = '\nOpciones: ' + ' · '.join(
                    f'{v.name} {money(v.price)}' for v in item.variants if v.price is not None)
            lines.append(f'*{item.name}*{price_text}{days}\n{item.description}{variants}')
        follow = ('¿Querés agregarla a tu pedido?' if ctx.get('step') == 'cart'
                  else '¿Querés pedirla? Escribí por ejemplo "1 ' + pairs[0][0].name.replace('Burger ', '') + '".')
        return self.say('\n\n'.join(lines) + '\n\n' + follow)

    def _start_order(self, ctx, pairs, text=''):
        requested = text if self._is_day_request(text) else None
        if ctx.get('step') and ctx.get('cart') and ctx.get('service_date'):
            # Already building an order: add instead of starting over.
            if requested:
                target = self.schedule.order_target(self.now, requested)
                if not target.service_date:
                    return self.say('😔 ' + target.message)
                if target.service_date.isoformat() != ctx['service_date']:
                    ctx['service_date'] = target.service_date.isoformat()
                    ctx['step'] = 'cart'
                    self._revalidate_cart(ctx)
                    self.say(f'Listo, cambié el pedido para *{self._day_text(target.service_date)}* '
                             f'({self.schedule.day_label(target.service_date)} {target.service_date:%d/%m}).')
            ctx['step'] = 'cart'
            if pairs:
                self._add(ctx, pairs)
            else:
                self.say('Seguimos con tu pedido. ¿Qué más querés agregar?')
            return True
        window = self.schedule.order_target(self.now, requested)
        if not window.service_date:
            return self.say('😔 ' + window.message)
        ctx['service_date'] = window.service_date.isoformat()
        ctx['requested_day'] = requested or ''
        ctx['cart'] = []
        ctx['step'] = 'cart'
        if window.mode == 'preorder':
            self.say(f'¡Genial! Te tomo el pedido para *{self._day_text(window.service_date)}* '
                     f'(abrimos a las {self.schedule.open:%H:%M}) 🙌')
        if not pairs:
            self._send_menu(ctx)
            return self.say('¿Qué te gustaría pedir? Escribilo con tus palabras 🍔')
        self._add(ctx, pairs)
        return True

    def _revalidate_cart(self, ctx):
        sd = self._service_date(ctx)
        keep = []
        for line in ctx.get('cart') or []:
            item = self.by_id.get(line['id'])
            if item and self._allowed(item, line.get('variant_id'), sd):
                keep.append(line)
            elif item:
                self.say(f'Quité {item.label_for(line.get("variant_id"))} porque no se vende el '
                         f'{self.schedule.day_label(sd)}.')
        ctx['cart'] = keep
        if keep:
            lines, total = cart_text(keep, self.by_id, price_for=self._price_for)
            self.say(f'🛒 *Tu pedido:*\n{lines}\n*Subtotal: {money(total)}*')

    def _add(self, ctx, pairs):
        sd = self._service_date(ctx)
        left = self._stock(sd)
        problems = []
        for item, qty, note, variant_id in pairs:
            promo = self._promo_for(item.id)
            if not item.available or item.price_for(variant_id) is None:
                problems.append(f'La {item.label_for(variant_id)} no está disponible para pedir por acá en este momento.')
                continue
            if not promo and not item.sells_on(sd):
                problems.append(f'El {item.name} es válido solo {", ".join(item.days)} 😉')
                continue
            line = {'id': item.id, 'qty': qty, 'note': note, 'variant_id': variant_id}
            trial = ctx['cart'] + [line]
            need = stockmod.consumption(trial, self.by_id)
            short = [k for k, v in need.items() if left.get(k) is not None and left[k] < v]
            if short:
                base = stockmod.consumption(ctx['cart'], self.by_id)
                spare = {k: (None if left.get(k) is None else left[k] - base.get(k, 0)) for k in left}
                can = stockmod.max_units(item, spare, item.recipe_for(variant_id)) or 0
                if can <= 0:
                    problems.append(f'😔 Se nos agotó la {item.label_for(variant_id)} por hoy.')
                    continue
                problems.append(f'De {item.label_for(variant_id)} solo nos quedan {can}; te agregué {can}.')
                qty = can
            merged = False
            for existing in ctx['cart']:
                if (existing['id'] == item.id and existing.get('note', '') == note
                        and existing.get('variant_id') == variant_id):
                    existing['qty'] = min(20, existing['qty'] + qty)
                    merged = True
            if not merged:
                ctx['cart'].append({'id': item.id, 'qty': qty, 'note': note, 'variant_id': variant_id})
        for p in problems:
            self.say(p)
        if ctx['cart']:
            lines, total = cart_text(ctx['cart'], self.by_id, price_for=self._price_for)
            self.say(f'🛒 *Tu pedido:*\n{lines}\n*Subtotal: {money(total)}*\n\n¿Querés agregar algo más?',
                     [{'title': 'Listo, seguir', 'payload': 'cart_done'},
                      {'title': 'Agregar más', 'payload': 'cart_more'},
                      {'title': 'Cancelar', 'payload': 'cancelar'}])
            upsell = self._upsell_text(ctx)
            if upsell:
                self.say(upsell)
        else:
            self.say('¿Qué otra cosa te gustaría pedir? Si querés ver la carta escribí *carta*.')

    def _upsell_text(self, ctx):
        if ctx.get('upsell_sent'):
            return None
        cart = ctx.get('cart') or []
        if not cart:
            return None
        kinds = {self.by_id[l['id']].kind for l in cart if l['id'] in self.by_id}
        if not kinds & {'burger', 'combo'}:
            return None
        in_cart = {l['id'] for l in cart}
        parts = []
        if 'drink' not in kinds:
            drinks = [i for i in self.items
                      if i.kind == 'drink' and i.available and i.price is not None and i.id not in in_cart]
            if drinks:
                shown = ' · '.join(f'{i.name} {money(i.price)}' for i in drinks[:3])
                parts.append('🥤 ' + shown + ('…' if len(drinks) > 3 else ''))
        if 'dip' not in kinds and 'combo' not in kinds:
            dips = [i for i in self.items
                    if i.kind == 'dip' and i.available and i.price is not None and i.id not in in_cart]
            if dips:
                shown = ' · '.join(f'{i.name} {money(i.price)}' for i in dips[:3])
                parts.append('🍯 ' + shown + ('…' if len(dips) > 3 else ''))
        if not parts:
            return None
        ctx['upsell_sent'] = True
        return ('🍟 *Completá tu combo:*\n' + '\n'.join(parts) +
                '\n\nSumalo por nombre o seguí con *listo*.')

    def _ask_mode(self, ctx):
        options = []
        if self.negocio.get('delivery_enabled'):
            options.append({'title': '🛵 Delivery', 'payload': 'mode_delivery'})
        if self.negocio.get('takeaway_enabled'):
            options.append({'title': '🥡 Retiro en local', 'payload': 'mode_takeaway'})
        ctx['step'] = 'mode'
        self.say('¿Es para delivery o retirás en el local?', options)

    def _ask_slot(self, ctx):
        sd = self._service_date(ctx)
        slots = self._free_slots(sd, ctx.get('mode'))
        ctx['step'] = 'slot'
        current_open = self.schedule.is_open(self.now) and self.schedule.current_service(self.now) == sd
        options = (['Lo antes posible'] if current_open else []) + slots
        if not options:
            ctx['slot'] = 'ASAP'
            return self._ask_payment(ctx)
        ctx['slot_options'] = options[:12]
        verb = 'te lo llevamos' if ctx.get('mode') == 'delivery' else 'lo pasás a buscar'
        listing = '\n'.join(f'{n}. {o}' for n, o in enumerate(ctx['slot_options'], start=1))
        self.say(f'¿A qué hora {verb}? Respondé con el número:\n{listing}')

    def _free_slots(self, sd, mode=None):
        slots = self.schedule.slots(sd, self.now, mode)
        cap = int(self.negocio.get('slot_capacity') or 0)
        if not cap:
            return slots
        rows = self.s.execute(select(Order.slot, func.count()).where(
            Order.service_date == sd.isoformat(), Order.status != 'cancelado').group_by(Order.slot)).all()
        used = dict(rows)
        return [x for x in slots if used.get(x, 0) < cap]

    def _ask_payment(self, ctx):
        methods = self.negocio.get('payment_methods') or ['Efectivo']
        ctx['step'] = 'payment'
        self.say('¿Cómo vas a pagar?', [{'title': m[:20], 'payload': f'pay_{n}'} for n, m in enumerate(methods[:3])])

    def _summary(self, ctx):
        lines, total = cart_text(ctx['cart'], self.by_id, price_for=self._price_for)
        sd = self._service_date(ctx)
        mode = ('🛵 Delivery a: ' + ctx.get('address', '')) if ctx.get('mode') == 'delivery' else '🥡 Retiro en el local'
        slot = 'lo antes posible' if ctx.get('slot') == 'ASAP' else f"a las {ctx.get('slot')}"
        fee = ''
        if ctx.get('mode') == 'delivery':
            if self.negocio.get('delivery_fee') is not None:
                fee = f"\nEnvío: {money(self.negocio['delivery_fee'])}"
                total += int(self.negocio['delivery_fee'])
            else:
                fee = '\nEnvío: a confirmar según zona (hasta 10 km)'
        ctx['step'] = 'confirm'
        self.say(f"📝 *Revisá tu pedido* ({self._day_text(sd)}, {slot})\n{lines}{fee}\n"
                 f"*Total: {money(total)}*\n{mode}\n👤 {ctx.get('name', '')}\n💳 {ctx.get('payment', '')}\n\n¿Lo confirmo?",
                 [{'title': '✅ Confirmar', 'payload': 'confirm_yes'},
                  {'title': '✏️ Cambiar algo', 'payload': 'confirm_edit'},
                  {'title': '❌ Cancelar', 'payload': 'cancelar'}])

    async def _step(self, ctx, step, text, t, name):
        question = self._is_question(text, t) and not t.startswith(('cart_', 'mode_', 'pay_', 'confirm_'))
        if step == 'cart':
            day_changed = False
            if self._is_day_request(text):
                target = self.schedule.order_target(self.now, text)
                if not target.service_date:
                    self.say('😔 ' + target.message)
                    return True
                if target.service_date.isoformat() != ctx.get('service_date'):
                    ctx['service_date'] = target.service_date.isoformat()
                    ctx['requested_day'] = text
                    self._revalidate_cart(ctx)
                    self.say(f'Listo, el pedido va para *{self._day_text(target.service_date)}*.')
                    day_changed = True
            if question:
                return False
            parsed = parse_items(text, self.items)
            if not parsed and not RE_DONE.match(t) and t != 'cart_more' and self.llm and not question:
                result = await self.llm.interpret(text, self.items, self.negocio, 'armando_pedido')
                if result and result['items']:
                    parsed = []
                    for x in result['items']:
                        item = self.by_id[x['id']]
                        variant = x.get('variant_id')
                        if variant not in {v.id for v in item.variants}:
                            variant = item.variants[0].id if item.variants else None
                        parsed.append((item, x['qty'], x.get('note', ''), variant))
            if parsed:
                self._add(ctx, parsed)
                return True
            if day_changed:
                if ctx.get('cart'):
                    self.say('¿Agregamos algo más? Cuando esté, escribí *listo*.')
                else:
                    self._send_menu(ctx)
                    self.say('¿Qué te gustaría pedir?')
                return True
            if t == 'cart_more':
                self.say('¡Dale! Decime qué más querés agregar 🍔')
                return True
            if RE_DONE.match(t):
                if not ctx.get('cart'):
                    self.say('Todavía no agregaste nada 🙂 ¿Qué te gustaría pedir?')
                    return True
                self._ask_mode(ctx)
                return True
            if RE_MENU.search(t):
                self._send_menu(ctx)
                return True
            if question:
                return False
            self.say('No te entendí bien 🙈 Escribí el nombre de la burger (ej: "2 American doble") o *carta* para verla. '
                     'Si ya está, escribí *listo*.')
            return True
        if step == 'mode':
            if t == 'mode_delivery' or re.search(r'\b(delivery|envio|domicilio|traer|traigan|manden|casa)\b', t):
                if not self.negocio.get('delivery_enabled'):
                    self.say('Por ahora no tenemos delivery, solo retiro en el local.')
                    return True
                ctx['mode'] = 'delivery'
                ctx['step'] = 'address'
                self.say('📍 Pasame la dirección completa (calle, número, barrio/departamento y alguna referencia). '
                         'Hacemos delivery hasta 10 km del local.')
                return True
            if t == 'mode_takeaway' or re.search(r'\b(retiro|retirar|buscar|paso|local|take ?away|llevar)\b', t):
                ctx['mode'] = 'takeaway'
                return self._ask_name(ctx, name)
            if question:
                return False
            self._ask_mode(ctx)
            return True
        if step == 'address':
            if question:
                return False
            if len(t) < 6:
                self.say('¿Me pasás la dirección completa? Calle, número y barrio 🙏')
                return True
            ctx['address'] = text.strip()[:200]
            return self._ask_name(ctx, name)
        if step == 'name':
            if question:
                return False
            clean = text.strip()[:60]
            if len(clean) < 2:
                self.say('¿A nombre de quién va el pedido?')
                return True
            ctx['name'] = clean
            self._ask_slot(ctx)
            return True
        if step == 'slot':
            options = ctx.get('slot_options') or []
            choice = None
            m = re.fullmatch(r'\s*(\d{1,2})\s*', t)
            if m and 1 <= int(m.group(1)) <= len(options):
                choice = options[int(m.group(1)) - 1]
            hm = re.search(r'\b(\d{1,2})[:.](\d{2})\b', t)
            if not choice and hm:
                wanted = f'{int(hm.group(1)):02d}:{hm.group(2)}'
                choice = wanted if wanted in options else None
            if not choice and re.search(r'(antes posible|ya|ahora|cuanto antes)', t) and 'Lo antes posible' in options:
                choice = 'Lo antes posible'
            if not choice:
                if question:
                    return False
                self._ask_slot(ctx)
                return True
            ctx['slot'] = 'ASAP' if choice == 'Lo antes posible' else choice
            ctx.pop('slot_options', None)
            self._ask_payment(ctx)
            return True
        if step == 'payment':
            methods = self.negocio.get('payment_methods') or ['Efectivo']
            chosen = None
            m = re.fullmatch(r'pay_(\d)', t)
            if m and int(m.group(1)) < len(methods):
                chosen = methods[int(m.group(1))]
            for method in methods:
                if not chosen and normalized(method)[:6] in t:
                    chosen = method
            if not chosen and 'transf' in t:
                chosen = next((x for x in methods if 'transf' in normalized(x)), None)
            if not chosen:
                if question:
                    return False
                self._ask_payment(ctx)
                return True
            ctx['payment'] = chosen
            self._summary(ctx)
            return True
        if step == 'confirm':
            if RE_YES.match(t):
                self._confirm(ctx)
                return True
            if t == 'confirm_edit' or re.search(r'\b(cambiar|modificar|agregar|sacar|quitar|editar)\b', t):
                ctx['step'] = 'cart'
                ctx['cart'] = []
                self.say('Dale, armemos el pedido de nuevo. ¿Qué querés pedir? 🍔')
                return True
            if question:
                return False
            self._summary(ctx)
            return True
        return False

    def _ask_name(self, ctx, name):
        ctx['step'] = 'name'
        hint = f' (¿va a nombre de *{name}*? Escribí el nombre igual para confirmarlo)' if name else ''
        self.say('¿A nombre de quién va el pedido?' + hint)
        return True

    def _confirm(self, ctx):
        sd = self._service_date(ctx)
        if not self.schedule.can_order_for(sd, self.now):
            self._reset(ctx)
            return self.say('😔 No pude confirmar el pedido: se cerró la toma de pedidos para ese día.')
        for line in ctx['cart']:
            item = self.by_id.get(line['id'])
            if not item or not self._allowed(item, line.get('variant_id'), sd):
                self._reset(ctx)
                return self.say('😔 Uno de los productos dejó de estar disponible. Armemos el pedido de nuevo, escribí *pedir*.')
        if ctx['slot'] != 'ASAP' and ctx['slot'] not in self._free_slots(sd, ctx['mode']):
            self.say('😔 Ese horario se completó recién. Elegí otro:')
            self._ask_slot(ctx)
            return
        need = stockmod.consumption(ctx['cart'], self.by_id)
        try:
            with self.s.begin_nested():
                stockmod.reserve(self.s, sd.isoformat(), need)
        except stockmod.OutOfStock:
            left = self._stock(sd)
            keep, gone = [], []
            for line in ctx['cart']:
                trial = keep + [line]
                need2 = stockmod.consumption(trial, self.by_id)
                if all(left.get(k) is None or left[k] >= v for k, v in need2.items()):
                    keep.append(line)
                else:
                    gone.append(self.by_id[line['id']].label_for(line.get('variant_id')))
            ctx['cart'] = keep
            ctx['step'] = 'cart'
            self.say('😔 Justo se nos agotó: ' + ', '.join(gone) + '. Lo saqué del pedido.')
            if keep:
                lines, total = cart_text(keep, self.by_id, price_for=self._price_for)
                self.say(f'🛒 Queda así:\n{lines}\n*Subtotal: {money(total)}*\n¿Querés agregar otra cosa o seguimos?',
                         [{'title': 'Listo, seguir', 'payload': 'cart_done'},
                          {'title': 'Cancelar', 'payload': 'cancelar'}])
            else:
                self.say('¿Querés elegir otra cosa? Escribí *carta* para ver lo disponible.')
            return
        _, total = cart_text(ctx['cart'], self.by_id, price_for=self._price_for)
        fee = self.negocio.get('delivery_fee') if ctx['mode'] == 'delivery' else None
        number = (self.s.scalar(select(func.max(Order.number)).where(Order.service_date == sd.isoformat())) or 0) + 1
        items = []
        for line in ctx['cart']:
            item = self.by_id[line['id']]
            note = line.get('note', '')
            promo = self._promo_for(item.id)
            if promo:
                promo_note = f'Promo {promo.name} -{promo.discount_percent}%'
                note = f'{promo_note}; {note}' if note else promo_note
            items.append({'id': line['id'], 'name': item.label_for(line.get('variant_id')),
                          'qty': line['qty'], 'note': note,
                          'price': self._price_for(item, line.get('variant_id')),
                          'variant_id': line.get('variant_id')})
        order = Order(number=number, conversation_id=ctx['conversation_id'], service_date=sd.isoformat(),
                      status='nuevo', slot=ctx['slot'], mode=ctx['mode'], payment=ctx['payment'],
                      total=total + (fee or 0), delivery_fee=fee,
                      data=self.db.pack({'name': ctx.get('name', ''), 'address': ctx.get('address', ''),
                                         'phone': ctx.get('wa_id', ''), 'items': items}),
                      consumption=json.dumps(need), created=self.now_ts, updated=self.now_ts)
        self.s.add(order)
        self.s.flush()
        ctx['last_order_id'] = order.id
        self._reset(ctx)
        when = 'lo antes posible' if order.slot == 'ASAP' else f'para las {order.slot}'
        msg = f'✅ ¡Pedido *#{number}* recibido, {when} ({self._day_text(sd)})!\n'
        if order.mode == 'delivery' and fee is None:
            msg += 'Te confirmamos el costo del envío por acá (hasta 10 km). '
        msg += 'Te aviso cuando lo confirmemos y cuando esté en camino.'
        advance = sd > self.now.date() or not self.schedule.is_open(self.now)
        if advance:
            msg += '\n\n💳 Es un pedido anticipado y se abona antes. '
            if self.negocio.get('transfer_alias'):
                holder = (f" (titular: {self.negocio['transfer_holder']})"
                          if self.negocio.get('transfer_holder') else '')
                msg += f"Podés transferir al alias *{self.negocio['transfer_alias']}*{holder} y mandarnos el comprobante."
            else:
                msg += 'El equipo te pasa los datos para abonarlo.'
                self.flags.add('needs_human')
            if 'transf' not in normalized(order.payment):
                self.flags.add('needs_human')
        elif 'transf' in normalized(order.payment):
            if self.negocio.get('transfer_alias'):
                holder = (f" (titular: {self.negocio['transfer_holder']})"
                          if self.negocio.get('transfer_holder') else '')
                msg += (f"\n\n💳 Alias: *{self.negocio['transfer_alias']}*{holder}. "
                        'Mandanos el comprobante por este chat 🙏')
            else:
                msg += '\n\n💳 Te pasamos los datos para transferir por este chat.'
                self.flags.add('needs_human')
        self.say(msg)
        self.flags.add('new_order')
