"""Service nights in San Juan local time.

A service night belongs to the date it opens: 00:30 on Thursday is still
Wednesday's service. Render runs in UTC, so every calculation converts first.
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, date, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

DAYS = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
DAY_LABEL = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def _t(value):
    h, m = str(value).split(':')
    return dtime(int(h), int(m))


def _n(text):
    text = unicodedata.normalize('NFKD', str(text or '').lower())
    return re.sub(r'\s+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).strip()


@dataclass
class Window:
    service_date: date | None
    mode: str           # open | preorder | closed
    message: str = ''


class Schedule:
    def __init__(self, negocio):
        self.tz = ZoneInfo(negocio.get('timezone') or 'America/Argentina/San_Juan')
        self.days = {DAYS.index(d) for d in negocio['service_days']}
        self.open = _t(negocio['service_open'])
        self.close = _t(negocio['service_close'])
        self.last_order = _t(negocio.get('last_order') or negocio['service_close'])
        self.intake = _t(negocio.get('intake_open') or '00:00')
        self.slot_minutes = int(negocio.get('slot_minutes') or 30)
        self.preorder_days = int(negocio.get('preorder_days_ahead') or 30)
        self.first_delivery = _t(negocio.get('first_slot_delivery') or negocio['service_open'])
        self.first_takeaway = _t(negocio.get('first_slot_takeaway') or negocio['service_open'])

    def local(self, ts=None):
        ts = datetime.now(timezone.utc).timestamp() if ts is None else ts
        return datetime.fromtimestamp(ts, self.tz)

    def _crosses(self):
        return self.close <= self.open

    def _start(self, d):
        return datetime.combine(d, self.open, self.tz)

    def _at(self, d, t):
        return datetime.combine(d, t, self.tz)

    def _end(self, d, t):
        return datetime.combine(d + timedelta(days=1) if self._crosses() and t <= self.open else d, t, self.tz)

    def current_service(self, now):
        """Service date whose opening hours contain `now`, or None."""
        for d in (now.date() - timedelta(days=1), now.date()):
            if d.weekday() in self.days and self._start(d) <= now < self._end(d, self.close):
                return d
        return None

    def is_open(self, now):
        return self.current_service(now) is not None

    def next_service(self, now):
        d = now.date()
        for _ in range(8):
            if d.weekday() in self.days and self._start(d) > now:
                return d
            d += timedelta(days=1)
        return None

    def hours_text(self):
        days = [DAY_LABEL[i] for i in sorted(self.days)]
        closed = [DAY_LABEL[i] for i in range(7) if i not in self.days]
        if len(days) == 6 and closed == ['martes']:
            span = 'de miércoles a lunes'
        else:
            span = ', '.join(days)
        text = f'Abrimos {span} de {self.open:%H:%M} a {self.close:%H:%M}'
        if closed:
            text += f' (cerramos los {", ".join(closed)})'
        return text + '.'

    def order_window(self, now):
        """Which service an order placed at `now` belongs to."""
        current = self.current_service(now)
        if current:
            if now <= self._end(current, self.last_order):
                return Window(current, 'open')
            nxt = self.next_service(now)
            return Window(None, 'closed',
                          f'Por esta noche ya no tomamos pedidos (último pedido {self.last_order:%H:%M}). '
                          + self._next_intake_text(nxt))
        today = now.date()
        if today.weekday() in self.days and now.time() >= self.intake and now < self._start(today):
            return Window(today, 'preorder')
        nxt = self.next_service(now)
        if today.weekday() not in self.days:
            msg = f'Hoy {DAY_LABEL[today.weekday()]} no abrimos. ' + self._next_intake_text(nxt)
        else:
            msg = 'Todavía no estamos tomando pedidos para esta noche. ' + self._next_intake_text(nxt)
        return Window(None, 'closed', msg)

    def resolve_day(self, text, now):
        """Turn "hoy", "mañana", "el sábado" or 21/11 into a service date."""
        t = _n(text)
        if not t:
            return None
        current = self.current_service(now)
        if re.search(r'\bhoy\b', t):
            return current or now.date()
        if re.search(r'\bpasado manana\b', t):
            return now.date() + timedelta(days=2)
        if re.search(r'\bmanana\b', t):
            return now.date() + timedelta(days=1)
        m = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', t)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        m = re.search(r'\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b', t)
        if m:
            year = int(m.group(3)) if m.group(3) else now.year
            if year < 100:
                year += 2000
            try:
                return date(year, int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
        for index, day in enumerate(DAYS):
            if re.search(r'\b' + day + r'\b', t):
                if current and current.weekday() == index:
                    return current
                delta = (index - now.date().weekday()) % 7
                candidate = now.date() + timedelta(days=delta)
                # The named day is closed or its service already started and closed.
                if candidate == now.date() and (index not in self.days or
                                                (not self.is_open(now) and now.time() >= self.open)):
                    candidate += timedelta(days=7)
                return candidate
        return None

    def can_order_for(self, service_date, now):
        if service_date.weekday() not in self.days:
            return False
        current = self.current_service(now)
        if current == service_date:
            return now <= self._end(service_date, self.last_order)
        if service_date < now.date():
            return False
        if service_date == now.date():
            return self.intake <= now.time() < self.open
        return now.time() >= self.intake and (service_date - now.date()).days <= self.preorder_days

    def order_target(self, now, requested_day=None):
        """Resolve a free-text day ("el sábado") into a booking window."""
        if requested_day:
            d = self.resolve_day(requested_day, now)
            if not d:
                return Window(None, 'closed', 'No entendí para qué día. Decime, por ejemplo, "para el sábado".')
            if d.weekday() not in self.days:
                return Window(None, 'closed',
                              f'El {DAY_LABEL[d.weekday()]} no abrimos. Elegí otro día, por favor.')
            if self.can_order_for(d, now):
                return Window(d, 'open' if self.current_service(now) == d else 'preorder')
            if self.current_service(now) == d:
                return Window(None, 'closed',
                              f'Por esta noche ya no tomamos pedidos (último pedido {self.last_order:%H:%M}).')
            if d < now.date():
                return Window(None, 'closed', 'Ese día ya pasó. Decime para qué día querés el pedido.')
            if d == now.date() and now.time() >= self.open:
                return Window(None, 'closed',
                              f'Para hoy ya terminó el horario de pedidos. Podés pedir para otro día.')
            return Window(None, 'closed',
                          f'Para el {DAY_LABEL[d.weekday()]} todavía no estamos tomando pedidos. '
                          f'Los pedidos se toman desde las {self.intake:%H:%M}.')
        return self.order_window(now)

    def _next_intake_text(self, d):
        if not d:
            return ''
        return (f'Los pedidos para el {DAY_LABEL[d.weekday()]} se toman desde las {self.intake:%H:%M} '
                f'y abrimos a las {self.open:%H:%M}. ¡Escribinos y te lo dejamos anotado!')

    def slots(self, service_date, now, mode=None):
        """Delivery/pick-up slots for a service night still in the future."""
        out = []
        first = self.first_delivery if mode == 'delivery' else self.first_takeaway
        start = max(self._start(service_date), self._at(service_date, first))
        end = self._end(service_date, self.last_order)
        earliest = now + timedelta(minutes=30)
        while start <= end:
            if start >= earliest:
                out.append(start.strftime('%H:%M'))
            start += timedelta(minutes=self.slot_minutes)
        return out

    def day_label(self, d):
        return DAY_LABEL[d.weekday()]
