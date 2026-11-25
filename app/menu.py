"""Catalog matching and formatting. Prices always come from the database."""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from app.hours import DAYS

NUMBER_WORDS = {'un': 1, 'una': 1, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
                'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10, 'doce': 12}
MAX_QTY = 20


def normalized(text):
    text = unicodedata.normalize('NFKD', str(text or '').lower())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'\s+', ' ', text).strip()
    # Button titles may arrive with a leading emoji ("✅ Confirmar").
    return re.sub(r'^[^\w¿?#]+', '', text)


def money(value):
    return '$' + f'{int(value):,}'.replace(',', '.')


def product_kind(category):
    if str(category or '').startswith('Burgers'):
        return 'burger'
    if str(category or '').startswith('Combos'):
        return 'combo'
    if category == 'Dips':
        return 'dip'
    if category == 'Bebidas':
        return 'drink'
    return 'extra'


@dataclass
class Variant:
    id: str
    name: str
    price: int | None
    recipe: dict = field(default_factory=dict)


@dataclass
class Item:
    id: str
    name: str
    category: str
    description: str
    price: int | None
    available: bool
    days: list = field(default_factory=list)
    recipe: dict = field(default_factory=dict)
    aliases: list = field(default_factory=list)
    variants: list = field(default_factory=list)
    code: int = 0

    @property
    def kind(self):
        return product_kind(self.category)

    def sells_on(self, service_date):
        return not self.days or DAYS[service_date.weekday()] in self.days

    def variant_for(self, variant_id):
        if not variant_id:
            return self.variants[0] if self.variants else None
        return next((v for v in self.variants if v.id == variant_id),
                    self.variants[0] if self.variants else None)

    def recipe_for(self, variant_id=None):
        variant = self.variant_for(variant_id) if self.variants else None
        return variant.recipe if variant else self.recipe

    def price_for(self, variant_id=None):
        variant = self.variant_for(variant_id) if self.variants else None
        return variant.price if variant else self.price

    def label_for(self, variant_id=None):
        variant = self.variant_for(variant_id) if self.variants else None
        # The first variant is the default size: no need to repeat "Simple".
        if (variant and variant is not self.variants[0]
                and normalized(variant.name) not in normalized(self.name)):
            return f'{self.name} {variant.name}'
        return self.name

    def orderable(self, service_date=None, variant_id=None):
        variant = self.variant_for(variant_id) if self.variants else None
        price = variant.price if variant else self.price
        if price is None or not self.available:
            return False
        if variant and variant.price is None:
            return False
        return service_date is None or self.sells_on(service_date)


def load_items(session):
    from app.db import Product, ProductVariant
    rows = session.query(Product).order_by(Product.position).all()
    variants = {}
    for v in session.query(ProductVariant).order_by(ProductVariant.position):
        variants.setdefault(v.product_id, []).append(
            Variant(id=v.id, name=v.name, price=v.price, recipe=json.loads(v.recipe or '{}')))
    items = []
    for code, r in enumerate(rows, start=1):
        items.append(Item(id=r.id, name=r.name, category=r.category, description=r.description,
                          price=r.price, available=r.available, days=json.loads(r.days or '[]'),
                          recipe=json.loads(r.recipe or '{}'), aliases=json.loads(r.aliases or '[]'),
                          variants=variants.get(r.id, []), code=code))
    return items


def menu_text(items, service_date=None, day_label=None):
    lines, current = [], None
    for item in items:
        if not item.available or (item.price is None and not any(v.price is not None for v in item.variants)):
            continue
        if item.category != current:
            current = item.category
            lines.append(f'\n*{current}*')
        tag = ''
        if service_date and not item.sells_on(service_date):
            tag = ' _(solo ' + ', '.join(item.days) + ')_'
        if item.variants:
            prices = ' · '.join(f'{v.name} {money(v.price)}' for v in item.variants if v.price is not None)
            lines.append(f'{item.code}. {item.name} — {prices}{tag}\n   {item.description}')
        else:
            lines.append(f'{item.code}. {item.name} — {money(item.price)}{tag}\n   {item.description}')
    if not lines:
        return ('Estamos actualizando la carta y los precios en este momento. '
                'Te paso con alguien del equipo para que te pase la carta 🙌')
    head = '🍔 *Carta de El Club de la Burger* (precios directos por WhatsApp)'
    return head + '\n'.join(lines) + ('\n\nPara pedir, escribí lo que querés, por ejemplo: '
                                       '"2 American doble y 1 Cheese Onion sin cebolla" o el número: "2 de la 8".')


def _alias_table(items):
    table = {}
    for item in items:
        names = {normalized(item.name), normalized(item.name.replace('Burger ', ''))}
        names.update(normalized(a) for a in item.aliases)
        names = {n for n in names if n}
        for name in names:
            table.setdefault(name, (item, None))
        for variant in item.variants:
            variant_name = normalized(variant.name)
            for name in names:
                table[f'{name} {variant_name}'] = (item, variant)
                table[f'{variant_name} {name}'] = (item, variant)
    table = sorted(((name, item, variant) for name, (item, variant) in table.items()),
                   key=lambda x: -len(x[0]))
    return table


def _qty_before(text):
    m = re.search(r'(\d{1,2}|' + '|'.join(NUMBER_WORDS) + r')\s*(?:x\s*)?(?:de (?:la|las|el|los) |burgers? |hamburguesas? |la |las |el |los )?$', text)
    if not m:
        return 1
    token = m.group(1)
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def parse_items(text, items):
    """Deterministic parser: returns [(item, qty, note, variant)] in text order."""
    combo_choices = {}
    clean_lines = []
    for line in str(text or '').splitlines():
        match = re.match(r'^\s*elecciones\s+del\s+combo\s+(.+?)\s*:\s*(.+?)\s*$', line, re.I)
        if match:
            combo_choices.setdefault(normalized(f'combo {match.group(1)}'), []).append(match.group(2).strip())
        else:
            clean_lines.append(line)
    t = normalized('\n'.join(clean_lines))
    found, taken = [], [False] * len(t)
    # "2 de la 8" / "1 de la 3" / "#8"
    by_code = {i.code: i for i in items}
    for m in re.finditer(r'(?:(\d{1,2})\s*(?:x|de (?:la|el))\s*#?(\d{1,2})\b|#(\d{1,2})\b)', t):
        code = int(m.group(2) or m.group(3))
        if code in by_code:
            qty = int(m.group(1) or 1)
            found.append((m.start(), m.end(), by_code[code], qty, None))
            for k in range(m.start(), m.end()):
                taken[k] = True
    for name, item, variant in _alias_table(items):
        for m in re.finditer(r'(?<![a-z0-9])' + re.escape(name) + r'(?![a-z0-9])', t):
            if any(taken[m.start():m.end()]):
                continue
            # A combo code must not be reread as an item code ("combo 2").
            qty = _qty_before(t[max(0, m.start() - 25):m.start()])
            found.append((m.start(), m.end(), item, qty, variant))
            for k in range(m.start(), m.end()):
                taken[k] = True
    found.sort(key=lambda f: f[0])
    result = []
    for index, (start, end, item, qty, variant) in enumerate(found):
        nxt = found[index + 1][0] if index + 1 < len(found) else len(t)
        tail = t[end:nxt]
        note = ''
        m = re.search(r'\b((?:(?:un[oa]?|dos|tres|\d+)\s+)?(?:sin|con|extra|aparte|bien|poco)\b[^,.;]*)', tail)
        if m:
            filler = r'(?:\s+(?:y|e|mas|\d+|' + '|'.join(NUMBER_WORDS) + r'))+\s*$'
            note = re.sub(filler, '', ' ' + m.group(1).strip()).strip()
        result.append((item, max(1, min(MAX_QTY, qty)), note[:240],
                       variant.id if variant else (item.variants[0].id if item.variants else None)))
    # Bare menu number reply: "8" or "8 y 3".
    if not result and re.fullmatch(r'[\d\s,y]+', t):
        for code in re.findall(r'\d{1,2}', t):
            if int(code) in by_code:
                item = by_code[int(code)]
                result.append((item, 1, '', item.variants[0].id if item.variants else None))
    for index, (item, qty, note, variant) in enumerate(result):
        key = normalized(item.name)
        if item.kind != 'combo' or not combo_choices.get(key):
            continue
        choices = combo_choices[key].pop(0)
        result[index] = (item, qty, (f'{note}; {choices}' if note else choices)[:240], variant)
    return result


def cart_text(cart, items_by_id):
    lines, total = [], 0
    for line in cart:
        item = items_by_id.get(line['id'])
        if not item:
            continue
        price = item.price_for(line.get('variant_id'))
        if price is None:
            continue
        subtotal = price * line['qty']
        total += subtotal
        note = f" ({line['note']})" if line.get('note') else ''
        lines.append(f"• {line['qty']} x {item.label_for(line.get('variant_id'))}{note} — {money(subtotal)}")
    return '\n'.join(lines), total
