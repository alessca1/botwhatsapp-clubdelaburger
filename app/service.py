import asyncio
import json
import logging
import re
import time
import uuid
import httpx
from sqlalchemy import select, text as sql_text
from sqlalchemy.exc import IntegrityError
from app.db import Conversation, Event, Outbox, Message, Content, Order, Audit
from app.flow import Flow
from app.llm import LLM
from app.menu import load_items, money

logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    'confirmado': '👍 ¡Tu pedido *#{n}* está confirmado!{fee}',
    'en_cocina': '👨‍🍳 Tu pedido *#{n}* ya está en la plancha.',
    'listo': '✅ Tu pedido *#{n}* está listo para retirar en {address}. ¡Te esperamos!',
    'en_camino': '🛵 Tu pedido *#{n}* va en camino. ¡Ya llega!',
    'entregado': '¡Que lo disfrutes! 🍔 Gracias por elegir El Club de la Burger.',
    'cancelado': 'Tu pedido *#{n}* fue cancelado. Si es un error, escribinos por acá.',
}


class Bot:
    def __init__(self, settings, db, llm=None):
        self.settings, self.db = settings, db
        self.llm = llm or LLM(settings, db)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), follow_redirects=False)
        self.stopping = False

    async def close(self):
        await self.http.aclose()
        await self.llm.close()

    def negocio(self, s):
        return json.loads(s.get(Content, 'negocio').body)

    # ---------- inbound
    def ingest_message(self, sender, name, zernio_id, message_id, text, kind, timestamp, echo=False, retry=True):
        allowed = {p.strip() for p in self.settings.demo_allowlist.split(',') if p.strip()}
        if allowed and sender not in allowed:
            return
        phone = self.settings.whatsapp_phone_number_id
        conversation_id = f'{phone}:{sender}'
        event_id = f'{phone}:{"echo" if echo else "in"}:{message_id}'
        try:
            with self.db.session() as s:
                if s.get(Event, event_id):
                    return
                conv = s.get(Conversation, conversation_id)
                if not conv:
                    conv = Conversation(id=conversation_id, phone_id=phone, wa_id=sender,
                                        context=self.db.pack({}))
                    s.add(conv)
                conv.zernio_id = zernio_id
                if name:
                    conv.name = name
                now = time.time()
                if echo:
                    # Someone answered from the WhatsApp Business app: the bot steps back for a while.
                    conv.state = 'human_active'
                    conv.human_until = now + self.settings.human_pause_minutes * 60
                    self.cancel_bot_drafts(s, conv.id)
                    if text:
                        s.add(Message(conversation_id=conv.id, direction='human', text=self.db.pack(text), created=now))
                else:
                    conv.last_inbound = max(conv.last_inbound or 0, min(timestamp, now))
                    label = text if kind == 'text' else f'[{kind}] {text}'.strip()
                    s.add(Message(conversation_id=conv.id, direction='in', text=self.db.pack(label), created=now))
                conv.updated = now
                s.add(Event(id=event_id, conversation_id=conversation_id,
                            payload=self.db.pack({'text': text, 'kind': kind, 'name': name}),
                            status='done' if echo else 'pending'))
                s.commit()
        except IntegrityError:
            if retry:
                return self.ingest_message(sender, name, zernio_id, message_id, text, kind, timestamp, echo, False)
            raise

    def cancel_bot_drafts(self, s, conv_id):
        for row in s.scalars(select(Outbox).where(Outbox.conversation_id == conv_id,
                                                  Outbox.status == 'pending', Outbox.kind == 'bot')):
            row.status = 'cancelled'

    def enqueue(self, s, conv_id, text, buttons=None, kind='bot'):
        s.add(Outbox(conversation_id=conv_id, kind=kind,
                     body=self.db.pack({'text': text, 'buttons': buttons})))
        s.add(Message(conversation_id=conv_id, direction='human' if kind == 'human' else 'bot',
                      text=self.db.pack(text)))

    async def process_event(self, event_id, now=None):
        now = now or time.time()
        with self.db.session() as s:
            event = s.get(Event, event_id)
            if not event or event.status != 'pending':
                return
            conv = s.get(Conversation, event.conversation_id)
            payload = self.db.unpack(event.payload)
            if conv.state == 'human_active':
                if now < conv.human_until:
                    event.status = 'done'
                    s.commit()
                    return
                conv.state = 'automatic'
            ctx = self.db.unpack(conv.context)
            ctx['conversation_id'], ctx['wa_id'] = conv.id, conv.wa_id
            # A half-built order older than 6 hours is abandoned.
            if ctx.get('step') and now - ctx.get('touched', now) > 6 * 3600:
                for key in ('step', 'cart', 'mode', 'address', 'slot', 'payment', 'service_date', 'slot_options'):
                    ctx.pop(key, None)
            negocio = self.negocio(s)
            flow = Flow(s, self.db, negocio, load_items(s), now, self.llm)
            ctx = await flow.handle(ctx, payload.get('text', ''), payload.get('kind', 'text'),
                                    payload.get('name', ''))
            ctx['touched'] = now
            conv.context = self.db.pack(ctx)
            if 'needs_human' in flow.flags:
                conv.needs_human = True
            for reply in flow.replies:
                self.enqueue(s, conv.id, reply['text'], reply.get('buttons'))
            event.status = 'done'
            s.commit()

    # ---------- staff actions
    def set_order_status(self, order_id, status, delivery_fee=None):
        from app import stock as stockmod
        with self.db.session() as s:
            order = s.get(Order, order_id)
            if not order:
                raise KeyError(order_id)
            if order.status == status and delivery_fee is None:
                return order
            fee_changed = delivery_fee is not None and order.delivery_fee != int(delivery_fee)
            if delivery_fee is not None:
                order.delivery_fee = int(delivery_fee)
                data = self.db.unpack(order.data)
                order.total = sum(i['price'] * i['qty'] for i in data['items']) + order.delivery_fee
            if status == 'cancelado' and order.status != 'cancelado':
                stockmod.release(s, order.service_date, order.consumption)
            elif order.status == 'cancelado' and status != 'cancelado':
                stockmod.reserve(s, order.service_date, json.loads(order.consumption or '{}'))
            previous, order.status, order.updated = order.status, status, time.time()
            negocio = self.negocio(s)
            template = STATUS_MESSAGES.get(status)
            notified = False
            if template and previous != status and not (status == 'listo' and order.mode == 'delivery'):
                notified = status == 'confirmado'
                fee = ''
                if order.mode == 'delivery' and order.delivery_fee is not None:
                    fee = f' Envío: {money(order.delivery_fee)}. Total: {money(order.total)}.'
                self.enqueue(s, order.conversation_id,
                             template.format(n=order.number, fee=fee, address=negocio['address']), kind='status')
            if fee_changed and not notified and status != 'cancelado':
                self.enqueue(s, order.conversation_id,
                             f'🛵 El envío de tu pedido *#{order.number}* es {money(order.delivery_fee)}. '
                             f'Total: {money(order.total)}.', kind='status')
            s.add(Audit(action=f'order_{status}', target=order.id))
            s.commit()
            return order

    def human_reply(self, conversation_id, text):
        with self.db.session() as s:
            conv = s.get(Conversation, conversation_id)
            if not conv:
                raise KeyError(conversation_id)
            conv.state = 'human_active'
            conv.human_until = time.time() + self.settings.human_pause_minutes * 60
            conv.needs_human = False
            self.cancel_bot_drafts(s, conv.id)
            self.enqueue(s, conv.id, text, kind='human')
            s.commit()

    # ---------- outbound
    async def send(self, conv, text, buttons=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', conv.zernio_id or ''):
            raise ValueError('Missing Zernio conversation')
        payload = {'accountId': self.settings.zernio_account_id, 'message': text[:(640 if buttons else 4000)]}
        if buttons:
            payload['buttons'] = [{'type': 'postback', 'title': str(b['title'])[:20],
                                   'payload': str(b['payload'])[:256]} for b in buttons[:3]]
        r = await self.http.post(f'https://zernio.com/api/v1/inbox/conversations/{conv.zernio_id}/messages',
                                 headers={'Authorization': f'Bearer {self.settings.zernio_api_key}'}, json=payload)
        r.raise_for_status()
        body = r.json()
        if not body.get('success') or not body.get('data', {}).get('messageId'):
            raise ValueError('Zernio send not confirmed')
        return body['data']['messageId']

    async def deliver(self, out_id):
        if not self.settings.bot_enabled:
            return
        with self.db.session() as s:
            row = s.get(Outbox, out_id)
            if row is None or row.status != 'pending':
                return
            conv = s.get(Conversation, row.conversation_id)
            body = self.db.unpack(row.body)
            if row.kind == 'bot' and conv.state == 'human_active' and time.time() < conv.human_until:
                row.status = 'cancelled'
                s.commit()
                return
            # WhatsApp only allows free-form messages within 24 h of the customer's last message.
            if time.time() - conv.last_inbound >= 24 * 3600:
                row.status = 'expired_window'
                s.commit()
                return
            row.status = 'sending'
            s.commit()
        try:
            provider_id = await self.send(conv, body['text'], body.get('buttons'))
            with self.db.session() as s:
                row = s.get(Outbox, out_id)
                row.provider_id, row.status = provider_id, 'sent'
                s.commit()
        except Exception as exc:
            # The request may have been accepted; a person checks before retrying.
            with self.db.session() as s:
                row = s.get(Outbox, out_id)
                row.status, row.error = 'uncertain', type(exc).__name__
                s.commit()

    async def run(self):
        lease = None
        try:
            while not self.stopping:
                if self.db.engine.dialect.name == 'postgresql':
                    if lease is None:
                        lease = self.db.engine.connect()
                        if not lease.scalar(sql_text('SELECT pg_try_advisory_lock(918273645)')):
                            lease.close()
                            lease = None
                            await asyncio.sleep(1)
                            continue
                    lease.execute(sql_text('SELECT 1'))
                with self.db.session() as s:
                    event = s.scalar(select(Event).where(Event.status == 'pending', Event.available <= time.time())
                                     .order_by(Event.created).limit(1))
                    out = s.scalar(select(Outbox).where(Outbox.status == 'pending').order_by(Outbox.created).limit(1))
                if event:
                    try:
                        await self.process_event(event.id)
                    except Exception as exc:
                        logger.exception('Event processing failed')
                        with self.db.session() as s:
                            row = s.get(Event, event.id)
                            row.attempts += 1
                            row.available = time.time() + min(300, 2 ** row.attempts)
                            row.error = type(exc).__name__
                            row.status = 'failed' if row.attempts >= 5 else 'pending'
                            s.commit()
                if out:
                    await self.deliver(out.id)
                await asyncio.sleep(0.3)
        finally:
            if lease is not None:
                try:
                    lease.execute(sql_text('SELECT pg_advisory_unlock(918273645)'))
                finally:
                    lease.close()
