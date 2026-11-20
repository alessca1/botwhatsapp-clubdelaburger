"""Stock per service night, consumed by insumo recipes.

Each product has a recipe (e.g. Burger del Club = 3 medallones + 1 pan + 1 papas).
An insumo without a loaded quantity is not controlled (unlimited).
"""
import json
import time
from collections import defaultdict
from sqlalchemy import select
from app.db import DailyStock, Insumo


class OutOfStock(Exception):
    def __init__(self, missing):
        super().__init__('out_of_stock')
        self.missing = missing  # {insumo_id: (needed, remaining)}


def consumption(cart, items_by_id):
    need = defaultdict(int)
    for line in cart:
        item = items_by_id[line['id']]
        for insumo, qty in item.recipe_for(line.get('variant_id')).items():
            need[insumo] += int(qty) * int(line['qty'])
    return dict(need)


def _row(s, service_date, insumo, lock=False):
    q = select(DailyStock).where(DailyStock.service_date == service_date,
                                 DailyStock.insumo_id == insumo.id)
    if lock:
        q = q.with_for_update()
    row = s.scalar(q)
    if row is None:
        row = DailyStock(service_date=service_date, insumo_id=insumo.id,
                         initial=insumo.default_daily, used=0)
        s.add(row)
        s.flush()
    return row


def remaining(s, service_date):
    """{insumo_id: remaining or None (no control)}"""
    out = {}
    for insumo in s.scalars(select(Insumo)):
        row = _row(s, service_date, insumo)
        out[insumo.id] = None if row.initial is None else row.initial - row.used
    return out


def max_units(item, stock, recipe=None):
    """How many units of an item the remaining stock allows (None = unlimited)."""
    limits = []
    for insumo, qty in (recipe if recipe is not None else item.recipe).items():
        left = stock.get(insumo)
        if left is not None and int(qty) > 0:
            limits.append(max(0, left) // int(qty))
    return min(limits) if limits else None


def reserve(s, service_date, need):
    """Check and consume in the caller's transaction. Raises OutOfStock."""
    insumos = {i.id: i for i in s.scalars(select(Insumo).where(Insumo.id.in_(list(need))))}
    rows, missing = {}, {}
    for insumo_id, qty in sorted(need.items()):
        insumo = insumos.get(insumo_id)
        if not insumo:
            continue
        row = _row(s, service_date, insumo, lock=True)
        rows[insumo_id] = row
        if row.initial is not None and row.initial - row.used < qty:
            missing[insumo_id] = (qty, row.initial - row.used)
    if missing:
        raise OutOfStock(missing)
    for insumo_id, qty in need.items():
        if insumo_id in rows:
            rows[insumo_id].used += qty
            rows[insumo_id].updated = time.time()


def release(s, service_date, consumption_json):
    need = json.loads(consumption_json or '{}')
    for insumo_id, qty in need.items():
        row = s.scalar(select(DailyStock).where(DailyStock.service_date == service_date,
                                                DailyStock.insumo_id == insumo_id).with_for_update())
        if row:
            row.used = max(0, row.used - int(qty))
            row.updated = time.time()
