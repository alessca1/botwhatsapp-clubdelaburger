import asyncio
import hashlib
import hmac
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import Settings
from app.db import Order, Outbox, Conversation, Product, ProductVariant, Insumo, Message, Event
from app.hours import Schedule
from app.main import create_app
from app.menu import parse_items, load_items

TZ = ZoneInfo('America/Argentina/San_Juan')
ROOT = Path(__file__).resolve().parent.parent
NEGOCIO = json.load(open(ROOT / 'content/negocio.json', encoding='utf-8'))
SECRET = 'whsec_test'
ADMIN = 'a' * 40


def ts(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp()


# 2026-11-18 = miércoles, 2026-11-17 = martes, 2026-11-20 = viernes
WED_21 = ts(2026, 11, 18, 21)
TUE_15 = ts(2026, 11, 17, 15)
FRI_12 = ts(2026, 11, 20, 12)
FRI_21 = ts(2026, 11, 20, 21)


# ---------------- hours
@pytest.mark.parametrize('when,mode,service', [
    (ts(2026, 11, 19, 0, 30), 'open', '2026-11-18'),       # jueves 00:30 = servicio del miércoles
    (ts(2026, 11, 19, 0, 50), 'open', '2026-11-18'),        # último pedido 00:50
    (ts(2026, 11, 19, 0, 51), 'closed', None),
    (ts(2026, 11, 19, 1, 30), 'closed', None),
    (ts(2026, 11, 19, 9, 59), 'closed', None),
    (ts(2026, 11, 19, 10, 0), 'preorder', '2026-11-19'),
    (ts(2026, 11, 17, 12, 0), 'closed', None),              # martes cerrado
    (ts(2026, 11, 17, 0, 30), 'open', '2026-11-16'),        # martes 00:30 = servicio del lunes
    (ts(2026, 11, 18, 20, 0), 'open', '2026-11-18'),
])
def test_order_window(when, mode, service):
    s = Schedule(NEGOCIO)
    w = s.order_window(s.local(when))
    assert w.mode == mode
    assert (w.service_date.isoformat() if w.service_date else None) == service


def test_tuesday_message_points_to_wednesday():
    s = Schedule(NEGOCIO)
    w = s.order_window(s.local(ts(2026, 11, 17, 15)))
    assert 'martes' in w.message and 'miércoles' in w.message and '10:00' in w.message


def test_first_slots_per_mode():
    s = Schedule(NEGOCIO)
    d = date(2026, 11, 18)
    now = s.local(ts(2026, 11, 18, 12))
    assert s.slots(d, now, 'takeaway')[0] == '20:00'
    assert s.slots(d, now, 'delivery')[0] == '20:30'


def test_day_resolution():
    s = Schedule(NEGOCIO)
    now = s.local(TUE_15)
    assert s.resolve_day('quiero para el miércoles', now) == date(2026, 11, 18)
    assert s.resolve_day('para el sábado', now) == date(2026, 11, 21)
    assert s.resolve_day('para mañana', now) == date(2026, 11, 18)
    assert s.resolve_day('para el martes', now) == date(2026, 11, 24)


def test_preorder_only_after_ten():
    s = Schedule(NEGOCIO)
    assert not s.can_order_for(date(2026, 11, 19), s.local(ts(2026, 11, 18, 9, 0)))
    assert s.can_order_for(date(2026, 11, 19), s.local(ts(2026, 11, 18, 10, 0)))


# ---------------- app fixture
@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, app_env='local', database_url=f'sqlite:///{tmp_path}/t.db',
                        admin_token=ADMIN, data_encryption_key=Fernet.generate_key().decode(),
                        zernio_api_key='k', zernio_account_id='acc', zernio_webhook_secret=SECRET,
                        whatsapp_phone_number_id='club-2643179759', bot_enabled=False)
    application = create_app(settings, run_worker=False)
    with TestClient(application) as client:
        application.state.client = client
        yield application


def send(app, text, when, sender='5492641111111', mid=[0], payload_id=None):
    mid[0] += 1
    body = {'event': 'message.received', 'account': {'accountId': 'acc', 'platform': 'whatsapp'},
            'conversation': {'id': 'conv_' + sender, 'participantId': sender, 'participantName': 'Juan'},
            'message': {'platformMessageId': f'wamid.{mid[0]}', 'direction': 'incoming', 'text': text,
                        'sentAt': datetime.fromtimestamp(when, TZ).isoformat()},
            'metadata': {'postbackPayload': payload_id} if payload_id else {}}
    raw = json.dumps(body).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    r = app.state.client.post('/webhooks/zernio', content=raw, headers={'x-zernio-signature': sig})
    assert r.status_code == 200, r.text
    bot = app.state.bot
    from app.db import Event
    with app.state.db.session() as s:
        pending = [e.id for e in s.scalars(select(Event).where(Event.status == 'pending'))]
    for event_id in pending:
        asyncio.run(bot.process_event(event_id, now=when))
    return replies(app, sender)


def replies(app, sender='5492641111111'):
    db = app.state.db
    with db.session() as s:
        rows = s.scalars(select(Outbox).where(Outbox.conversation_id == f'club-2643179759:{sender}',
                                              Outbox.status == 'pending').order_by(Outbox.created))
        out = [db.unpack(r.body)['text'] for r in rows]
        for r in s.scalars(select(Outbox).where(Outbox.status == 'pending')):
            r.status = 'sent'
        s.commit()
    return '\n---\n'.join(out)


def place_takeaway(app, text='1 american', when=WED_21, sender='5492641111111'):
    for step in [text, 'listo', 'retiro', 'Juan', '1', 'efectivo', 'si']:
        out = send(app, step, when, sender=sender)
    return out


def enable_mp(app, monkeypatch, url='https://mp.test/pay/pref-1'):
    bot = app.state.bot
    bot.settings.mp_access_token = 'TEST-token'
    bot.settings.mp_webhook_secret = 'mp-secret'
    bot.settings.public_base_url = 'https://bot.test'
    calls = []

    async def fake_preference(**kwargs):
        calls.append(kwargs)
        return {'id': 'pref-1', 'url': url}

    monkeypatch.setattr(bot.mp, 'create_preference', fake_preference)
    return calls


def mp_signature(data_id, request_id='req-1', ts='1700000000', secret='mp-secret'):
    manifest = f'id:{data_id};request-id:{request_id};ts:{ts};'
    signature = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f'ts={ts},v1={signature}'


def mp_post(app, payment_id, signature=None, request_id='req-1'):
    return app.state.client.post(
        '/webhooks/mercadopago',
        json={'type': 'payment', 'data': {'id': str(payment_id)}},
        headers={'x-signature': signature or mp_signature(str(payment_id), request_id),
                 'x-request-id': request_id},
    )


def place_takeaway_mp(app, when=WED_21):
    for step in ['1 american', 'listo', 'retiro', 'Juan', '1', 'mercado pago', 'si']:
        out = send(app, step, when)
    return out


def test_questionnaire_number_and_content():
    assert NEGOCIO['business_number'] == '5492643179759'
    assert NEGOCIO['business_number_display'] == '+54 9 264 317-9759'
    assert NEGOCIO['last_order'] == '00:50'
    assert NEGOCIO['transfer_alias'] == 'Mathias1998'
    assert NEGOCIO['transfer_holder'] == 'Mathias De Dios Juarez Fa.'
    assert 'club-2643179759' in (ROOT / 'render.yaml').read_text(encoding='utf-8')


def test_human_pause_default_is_five_minutes():
    s = Settings(_env_file=None, data_encryption_key=Fernet.generate_key().decode())
    assert s.human_pause_minutes == 5


def test_bad_signature_rejected(app):
    r = app.state.client.post('/webhooks/zernio', content=b'{}', headers={'x-zernio-signature': 'nope'})
    assert r.status_code == 403


def test_cart_message_is_parseable_by_bot(app):
    with app.state.db.session() as s:
        items = load_items(s)
    text = ('Hola! Quiero hacer este pedido:\n'
            '2 x Burger American Doble, sin cebolla.\n'
            '2 x Extra medallón.\n'
            '1 x Papas clásicas.\n'
            '1 x Dip cheddar.')
    got = [(i.id, q, n, v) for i, q, n, v in parse_items(text, items)]
    assert got == [('american', 2, 'sin cebolla', 'american-doble'),
                   ('extra-medallon', 2, '', None),
                   ('papas', 1, '', None),
                   ('dip-cheddar', 1, '', None)]


def test_cart_message_creates_order_with_extras(app):
    cart = ('Hola! Quiero hacer este pedido:\n'
            '2 x Burger American Doble, sin cebolla.\n'
            '2 x Extra medallón.')
    for step in [cart, 'listo', 'retiro', 'Juan', '1', 'efectivo', 'si']:
        out = send(app, step, WED_21)
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.total == 25800
        items = app.state.db.unpack(order.data)['items']
        assert any(i['name'] == 'Burger American Doble' and i['qty'] == 2 for i in items)
        assert any(i['name'] == 'Extra medallón' and i['qty'] == 2 for i in items)


def test_combo_choices_line_is_not_parsed_as_extra_burgers(app):
    with app.state.db.session() as s:
        items = load_items(s)
        combo = next(i for i in items if i.id == 'combo3')
    text = ('Hola! Quiero hacer este pedido:\n'
            '1 x Combo 3 Amistad.\n'
            f'Elecciones del {combo.name}: 2 x Burger American Doble, '
            '2 x Burger Cheese Doble. Dips: Dip cheddar, Dip barbacoa.')
    got = [(i.id, qty, note, variant) for i, qty, note, variant in parse_items(text, items)]
    assert len(got) == 1
    item_id, qty, note, variant = got[0]
    assert item_id == 'combo3' and qty == 1
    assert 'Burger American Doble' in note and 'Dip cheddar' in note


def test_multiple_combo_choices_attach_to_their_combo_in_order(app):
    with app.state.db.session() as s:
        items = load_items(s)
        combo = next(i for i in items if i.id == 'combo3')
    text = ('Hola! Quiero hacer este pedido:\n'
            '1 x Combo 3 Amistad.\n'
            f'Elecciones del {combo.name}: 2 x Burger American Doble, 2 x Burger Cheese Doble. Dips: Dip cheddar, Dip barbacoa.\n'
            '1 x Combo 3 Amistad.\n'
            f'Elecciones del {combo.name}: 2 x Burger Clásica Doble, 2 x Burger Honey Doble. Dips: Dip mayo casera, Dip cheddar.')
    got = [(i.id, qty, note) for i, qty, note, _ in parse_items(text, items)]
    assert len(got) == 2
    assert all(item_id == 'combo3' and qty == 1 for item_id, qty, _ in got)
    assert 'Burger American Doble' in got[0][2] and 'Dip barbacoa' in got[0][2]
    assert 'Burger Clásica Doble' in got[1][2] and 'Dip mayo casera' in got[1][2]


def test_cart_message_with_combo_choices_creates_one_combo(app):
    with app.state.db.session() as s:
        items = load_items(s)
        combo = next(i for i in items if i.id == 'combo3')
    cart = ('Hola! Quiero hacer este pedido:\n'
            '1 x Combo 3 Amistad.\n'
            f'Elecciones del {combo.name}: 2 x Burger American Doble, '
            '2 x Burger Cheese Doble. Dips: Dip cheddar, Dip barbacoa.')
    for step in [cart, 'listo', 'retiro', 'Juan', '1', 'efectivo', 'si']:
        out = send(app, step, FRI_21)
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.total == 37000
        saved = app.state.db.unpack(order.data)['items']
        assert len(saved) == 1
        assert saved[0]['name'] == 'Combo 3 Amistad' and saved[0]['qty'] == 1
        assert 'Burger American Doble' in saved[0]['note'] and 'Dip barbacoa' in saved[0]['note']


def test_public_menu_page_and_photo_upload(app):
    import base64
    client = app.state.client
    page = client.get('/carta')
    assert page.status_code == 200
    assert 'El Club de la Burger' in page.text and 'La carta' in page.text
    assert 'Burger Clásica' in page.text
    assert client.get('/carta/carta.css').status_code == 200
    assert client.get('/carta/carta-nocturna.css').status_code == 200
    assert '/carta/carta-nocturna.css' in page.text
    assert client.get('/carta/logo-club.png').status_code == 200
    assert '/carta/logo-club.png' in page.text
    assert client.get('/carta/carta.js').status_code == 200
    menu = client.get('/public/menu').json()
    assert menu['business']['phone'] == '5492643179759'
    assert menu['business']['phone_display'] == '+54 9 264 317-9759'
    assert len(menu['products']) == 21
    american = next(p for p in menu['products'] if p['id'] == 'american')
    assert american['kind'] == 'burger' and american['image_url'] is None
    assert [v['name'] for v in american['variants']] == ['Simple', 'Doble', 'Triple']
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=')
    h = {'Authorization': 'Bearer ' + ADMIN}
    r = client.post('/admin/menu/american/image', files={'file': ('foto.png', png, 'image/png')}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['image_url'].startswith('/menu/image/american')
    image = client.get('/menu/image/american')
    assert image.status_code == 200 and image.content == png
    assert image.headers['content-type'] == 'image/png'
    assert next(p for p in client.get('/public/menu').json()['products'] if p['id'] == 'american')['image_url']
    assert client.delete('/admin/menu/american/image', headers=h).status_code == 200
    assert client.get('/menu/image/american').status_code == 404


def test_bot_sends_menu_link(app):
    out = send(app, 'carta', WED_21)
    assert 'La carta completa' in out and 'https://clubdelaburger-whatsapp.onrender.com/carta' in out


def test_priced_drink_is_public_with_kind_drink(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    r = app.state.client.patch('/admin/menu/bebida-gaseosa', json={'price': 2500}, headers=h)
    assert r.status_code == 200, r.text
    menu = app.state.client.get('/public/menu').json()
    drink = next(p for p in menu['products'] if p['id'] == 'bebida-gaseosa')
    assert drink['kind'] == 'drink' and drink['price'] == 2500


def test_bot_lists_priced_drinks_instead_of_deriving(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    assert app.state.client.patch('/admin/menu/bebida-gaseosa', json={'price': 2500}, headers=h).status_code == 200
    out = send(app, '¿Qué bebidas tienen?', WED_21)
    assert 'Gaseosa cola 500 ml' in out and '$2.500' in out
    assert 'Te paso con el equipo' not in out


def test_bot_offers_combo_upsell_after_burger(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    assert app.state.client.patch('/admin/menu/bebida-gaseosa', json={'price': 2500}, headers=h).status_code == 200
    out = send(app, '1 american', WED_21)
    assert 'Completá tu combo' in out
    assert 'Gaseosa cola 500 ml' in out and '$2.500' in out
    assert 'Dip cheddar' in out and '$1.000' in out


def test_no_combo_upsell_when_cart_has_drink_and_dip(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    assert app.state.client.patch('/admin/menu/bebida-gaseosa', json={'price': 2500}, headers=h).status_code == 200
    out = send(app, '1 american y 1 gaseosa cola y 1 dip cheddar', WED_21)
    assert 'Completá tu combo' not in out
    with app.state.db.session() as s:
        ctx = app.state.db.unpack(s.get(Conversation, 'club-2643179759:5492641111111').context)
    assert {line['id'] for line in ctx['cart']} == {'american', 'bebida-gaseosa', 'dip-cheddar'}


def test_combo_upsell_suggests_drink_but_not_dip(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    assert app.state.client.patch('/admin/menu/bebida-gaseosa', json={'price': 2500}, headers=h).status_code == 200
    out = send(app, '1 combo 1', FRI_12)
    assert 'Gaseosa cola 500 ml' in out and '$2.500' in out
    assert 'Dip cheddar' not in out


def test_parse_items_and_variants(app):
    with app.state.db.session() as s:
        items = load_items(s)
    got = [(i.id, q, n, v) for i, q, n, v in parse_items('quiero 2 american sin cebolla y una cheese onion', items)]
    assert got == [('american', 2, 'sin cebolla', 'american-simple'),
                   ('cheeseonion', 1, '', 'cheeseonion-simple')]
    got = [(i.id, q, v) for i, q, _, v in parse_items('mandame 3 combo 2', items)]
    assert got == [('combo2', 3, None)]
    got = [(i.id, q, v) for i, q, _, v in parse_items('dos cheese', items)]
    assert got == [('cheese', 2, 'cheese-simple')]
    got = [(i.id, q, v) for i, q, _, v in parse_items('2 american doble y 1 clásica triple', items)]
    assert got == [('american', 2, 'american-doble'), ('clasica', 1, 'clasica-triple')]


def test_questionnaire_prices_in_database(app):
    with app.state.db.session() as s:
        assert s.get(Product, 'clasica').price == 8500
        assert s.get(Product, 'delclub').price == 12800
        assert s.get(Product, 'papas').price == 5500
        assert s.get(ProductVariant, 'american-doble').price == 10900
        assert s.get(ProductVariant, 'cheeseonion-triple').price == 12500
        assert s.get(Insumo, 'medallon').default_daily == 200
        assert s.get(Insumo, 'pan').default_daily == 100


def test_full_delivery_order_and_stock(app):
    client = app.state.client
    h = {'Authorization': 'Bearer ' + ADMIN}
    client.put('/admin/stock/medallon?service_date=2026-11-18', json={'remaining': 10}, headers=h)
    out = send(app, 'hola', WED_21)
    assert 'Bienvenido' in out
    out = send(app, '2 american sin cebolla y 1 del club', WED_21)
    assert '2 x Burger American (sin cebolla)' in out and '$32.400' in out
    out = send(app, 'listo', WED_21)
    assert 'delivery' in out.lower()
    send(app, '', WED_21, payload_id='mode_delivery')
    out = send(app, 'Calle Mendoza 123 sur, barrio Centro', WED_21)
    assert 'nombre' in out
    out = send(app, 'Juan Pérez', WED_21)
    assert 'Lo antes posible' in out
    out = send(app, '1', WED_21)
    assert 'pagar' in out
    out = send(app, 'efectivo', WED_21)
    assert 'Revisá tu pedido' in out and 'a confirmar' in out
    out = send(app, 'si', WED_21)
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.total == 32400 and order.mode == 'delivery' and order.service_date == '2026-11-18'
    stock = client.get('/admin/stock?service_date=2026-11-18', headers=h).json()
    med = next(i for i in stock['insumos'] if i['id'] == 'medallon')
    assert med['used'] == 5 and med['remaining'] == 5
    # Staff confirms with delivery fee -> customer is notified with the total.
    r = client.post(f'/admin/orders/{order.id}/status', json={'status': 'confirmado', 'delivery_fee': 1500}, headers=h)
    assert r.status_code == 200
    assert 'Envío: $1.500. Total: $33.900' in replies(app)
    # Cancelling returns the stock.
    client.post(f'/admin/orders/{order.id}/status', json={'status': 'cancelado'}, headers=h)
    med = next(i for i in client.get('/admin/stock?service_date=2026-11-18', headers=h).json()['insumos'] if i['id'] == 'medallon')
    assert med['used'] == 0


def test_variant_order_prices_and_stock(app):
    out = send(app, '1 american doble y 1 clásica triple', WED_21)
    assert 'Burger American Doble' in out and '$10.900' in out
    assert 'Burger Clásica Triple' in out and '$11.500' in out
    for step in ['listo', 'retiro', 'Juan', '1', 'efectivo', 'si']:
        out = send(app, step, WED_21)
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.total == 22400
        assert {i['variant_id'] for i in app.state.db.unpack(order.data)['items']} == {'american-doble', 'clasica-triple'}
        med = s.scalar(select(ProductVariant).where(ProductVariant.id == 'american-doble'))
        assert med.price == 10900


def test_stock_runs_out_mid_order(app):
    client = app.state.client
    h = {'Authorization': 'Bearer ' + ADMIN}
    client.put('/admin/stock/medallon?service_date=2026-11-18', json={'remaining': 3}, headers=h)
    out = send(app, '3 american', WED_21)
    assert '3 x Burger American' in out
    out = send(app, '1 cheese', WED_21)
    assert 'agot' in out.lower()
    # Another customer takes 2 medallones while the first one is still choosing.
    other = '5492642222222'
    place_takeaway(app, '2 cheese', WED_21, other)
    for step in ['listo', 'retiro', 'Juan', '1', 'efectivo']:
        send(app, step, WED_21)
    out = send(app, 'confirmo', WED_21)
    assert 'se nos agotó' in out.lower()
    with app.state.db.session() as s:
        assert s.query(Order).count() == 1


def test_combo_only_weekend(app):
    out = send(app, 'quiero un combo 1', WED_21)
    assert 'solo viernes' in out
    send(app, 'cancelar', WED_21)
    out = send(app, 'quiero un combo 1', FRI_12)
    assert 'hoy viernes' in out and 'Combo 1 Para Compartir' in out and '$18.000' in out


def test_combo_for_future_weekend(app):
    out = send(app, '1 combo 1 para el sábado', FRI_21)
    assert 'sábado 21/11' in out and 'Combo 1 Para Compartir' in out
    for step in ['listo', 'retiro', 'Juan', '1', 'transferencia', 'si']:
        out = send(app, step, FRI_21)
    assert 'Pedido *#1* recibido' in out and 'pedido anticipado' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.service_date == '2026-11-21' and order.slot == '20:00'
        assert order.total == 18000


def test_product_without_price_not_orderable(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    r = app.state.client.patch('/admin/menu/honey', json={'clear_price': True}, headers=h)
    assert r.status_code == 200
    out = send(app, 'una honey', WED_21)
    assert 'no está disponible' in out


def test_admin_edits_variant_price(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    menu = app.state.client.get('/admin/menu', headers=h).json()
    american = next(p for p in menu if p['id'] == 'american')
    assert [v['id'] for v in american['variants']] == ['american-simple', 'american-doble', 'american-triple']
    r = app.state.client.patch('/admin/menu/american', json={'variants': {'american-doble': 11200}}, headers=h)
    assert r.status_code == 200
    with app.state.db.session() as s:
        assert s.get(ProductVariant, 'american-doble').price == 11200


def test_admin_creates_product_and_shows_it_in_public_menu(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    r = app.state.client.post('/admin/menu', json={
        'name': 'Limonada 500 ml', 'category': 'Bebidas', 'description': 'Limonada casera.',
        'price': 1800, 'available': True,
    }, headers=h)
    assert r.status_code == 200, r.text
    product_id = r.json()['id']
    assert product_id == 'limonada-500-ml'
    r2 = app.state.client.post('/admin/menu', json={
        'name': 'Limonada 500 ml', 'category': 'Bebidas', 'price': 1800,
    }, headers=h)
    assert r2.status_code == 200
    assert r2.json()['id'] != product_id
    menu = app.state.client.get('/admin/menu', headers=h).json()
    created = next(p for p in menu if p['id'] == product_id)
    assert created['name'] == 'Limonada 500 ml' and created['price'] == 1800
    public = app.state.client.get('/public/menu').json()['products']
    drink = next(p for p in public if p['id'] == product_id)
    assert drink['kind'] == 'drink' and drink['price'] == 1800


def test_admin_edits_name_category_description_and_price(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    product_id = app.state.client.post('/admin/menu', json={
        'name': 'Limonada 500 ml', 'category': 'Bebidas', 'price': 1800,
    }, headers=h).json()['id']
    r = app.state.client.patch(f'/admin/menu/{product_id}', json={
        'name': 'Limonada casera 500 ml', 'category': 'Papas y extras',
        'description': 'Nueva receta.', 'price': 2000, 'available': False,
        'aliases': ['limonada casera'], 'recipe': {'dip': 1},
    }, headers=h)
    assert r.status_code == 200, r.text
    item = next(p for p in app.state.client.get('/admin/menu', headers=h).json() if p['id'] == product_id)
    assert item['name'] == 'Limonada casera 500 ml'
    assert item['category'] == 'Papas y extras' and item['description'] == 'Nueva receta.'
    assert item['price'] == 2000 and item['available'] is False
    assert item['aliases'] == ['limonada casera'] and item['recipe'] == {'dip': 1}
    public = app.state.client.get('/public/menu').json()['products']
    changed = next(p for p in public if p['id'] == product_id)
    assert changed['kind'] == 'extra'


def test_admin_deletes_product_without_deleting_orders(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    out = place_takeaway(app, '1 american')
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        order_id, order_total = order.id, order.total
        assert s.query(ProductVariant).filter_by(product_id='american').count() == 3
    r = app.state.client.delete('/admin/menu/american', headers=h)
    assert r.status_code == 200 and r.json()['deleted'] is True
    with app.state.db.session() as s:
        assert not s.get(Product, 'american')
        assert s.query(ProductVariant).filter_by(product_id='american').count() == 0
        kept = s.get(Order, order_id)
        assert kept.total == order_total
    assert all(p['id'] != 'american' for p in app.state.client.get('/public/menu').json()['products'])
    assert app.state.client.delete('/admin/menu/american', headers=h).status_code == 404


def test_deleted_seeded_product_stays_deleted_after_restart(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    assert app.state.client.delete('/admin/menu/honey', headers=h).status_code == 200
    app.state.db.initialize()
    with app.state.db.session() as s:
        assert not s.get(Product, 'honey')
        assert s.query(ProductVariant).filter_by(product_id='honey').count() == 0


def test_admin_deletes_product_image(app):
    import base64
    h = {'Authorization': 'Bearer ' + ADMIN}
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=')
    product_id = app.state.client.post('/admin/menu', json={'name': 'Foto test', 'category': 'Bebidas', 'price': 1000}, headers=h).json()['id']
    assert app.state.client.post(f'/admin/menu/{product_id}/image', files={'file': ('foto.png', png, 'image/png')}, headers=h).status_code == 200
    assert app.state.client.get(f'/menu/image/{product_id}').status_code == 200
    assert app.state.client.delete(f'/admin/menu/{product_id}', headers=h).status_code == 200
    assert app.state.client.get(f'/menu/image/{product_id}').status_code == 404


def test_admin_menu_validates_new_product(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    assert app.state.client.post('/admin/menu', json={'name': '   ', 'category': 'Bebidas'}, headers=h).status_code == 400
    assert app.state.client.post('/admin/menu', json={
        'name': 'Producto raro', 'category': 'Bebidas', 'recipe': {'noexiste': 1},
    }, headers=h).status_code == 400


def test_admin_lists_insumos_for_recipe_editor(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    rows = app.state.client.get('/admin/insumos', headers=h).json()
    assert any(i['id'] == 'medallon' and i['name'] for i in rows)


def test_admin_creates_promo_and_public_menu_exposes_it(app):
    import time as _t
    h = {'Authorization': 'Bearer ' + ADMIN}
    now = _t.time()
    r = app.state.client.post('/admin/promos', json={
        'name': 'Promo Test', 'product_id': 'combo3', 'discount_percent': 15,
        'starts_at': now - 60, 'ends_at': now + 3600, 'active': True,
    }, headers=h)
    assert r.status_code == 200, r.text
    promo_id = r.json()['id']
    admin = app.state.client.get('/admin/promos', headers=h).json()
    promo = next(p for p in admin if p['id'] == promo_id)
    assert promo['status'] == 'activa' and promo['price'] == 31450
    public = app.state.client.get('/public/menu').json()['promos']
    current = next(p for p in public if p['id'] == promo_id)
    assert current['product_id'] == 'combo3' and current['price'] == 31450
    assert current['discount_percent'] == 15


def test_admin_edits_and_deletes_promo(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    promo_id = app.state.client.post('/admin/promos', json={
        'name': 'Promo editable', 'product_id': 'combo3', 'discount_percent': 10,
        'starts_at': WED_21 - 60, 'ends_at': WED_21 + 3600, 'active': True,
    }, headers=h).json()['id']
    r = app.state.client.patch(f'/admin/promos/{promo_id}', json={
        'name': 'Promo editada', 'discount_percent': 20, 'active': False,
    }, headers=h)
    assert r.status_code == 200, r.text
    promo = next(p for p in app.state.client.get('/admin/promos', headers=h).json() if p['id'] == promo_id)
    assert promo['name'] == 'Promo editada' and promo['discount_percent'] == 20 and promo['status'] == 'pausada'
    assert app.state.client.delete(f'/admin/promos/{promo_id}', headers=h).status_code == 200
    assert all(p['id'] != promo_id for p in app.state.client.get('/admin/promos', headers=h).json())


def test_promo_allows_combo_outside_weekend_with_discount(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    promo_id = app.state.client.post('/admin/promos', json={
        'name': 'Promo miércoles', 'product_id': 'combo3', 'discount_percent': 15,
        'starts_at': WED_21 - 60, 'ends_at': WED_21 + 3600, 'active': True,
    }, headers=h).json()['id']
    out = send(app, '1 combo 3', WED_21)
    assert 'Combo 3 Amistad' in out and '$31.450' in out
    for step in ['listo', 'retiro', 'Juan', '1', 'efectivo', 'si']:
        out = send(app, step, WED_21)
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.total == 31450
        saved = app.state.db.unpack(order.data)['items']
        assert saved[0]['price'] == 31450
        assert 'Promo miércoles' in saved[0]['note'] and '15%' in saved[0]['note']


def test_expired_promo_does_not_allow_combo(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    app.state.client.post('/admin/promos', json={
        'name': 'Promo vencida', 'product_id': 'combo3', 'discount_percent': 15,
        'starts_at': WED_21 - 7200, 'ends_at': WED_21 - 60, 'active': True,
    }, headers=h)
    out = send(app, '1 combo 3', WED_21)
    assert 'solo viernes' in out
    with app.state.db.session() as s:
        assert s.query(Order).count() == 0


def test_closed_tuesday(app):
    out = send(app, '2 american', TUE_15)
    assert 'Hoy martes no abrimos' in out
    with app.state.db.session() as s:
        assert s.query(Order).count() == 0


def test_preorder_for_another_day(app):
    out = send(app, 'quiero pedir para el miércoles', TUE_15)
    assert 'miércoles 18/11' in out
    out = send(app, '2 american', TUE_15)
    assert '2 x Burger American' in out
    for step in ['listo', 'retiro', 'Juan', '1', 'efectivo', 'si']:
        out = send(app, step, TUE_15)
    assert 'Pedido *#1* recibido' in out and 'pedido anticipado' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.service_date == '2026-11-18' and order.slot == '20:00'
        assert s.get(Conversation, 'club-2643179759:5492641111111').needs_human


def test_change_day_while_building_cart(app):
    send(app, '2 american', WED_21)
    out = send(app, 'para el sábado', WED_21)
    assert 'sábado 21/11' in out and 'No te entendí' not in out
    with app.state.db.session() as s:
        ctx = app.state.db.unpack(s.get(Conversation, 'club-2643179759:5492641111111').context)
    assert ctx['service_date'] == '2026-11-21' and ctx['cart'][0]['qty'] == 2
    for step in ['listo', 'retiro', 'Juan', '1', 'efectivo', 'si']:
        out = send(app, step, WED_21)
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.service_date == '2026-11-21' and order.total == 19600


def test_faq_human_answer_is_single_message(app):
    out = send(app, 'tienen opción vegetariana?', WED_21)
    assert out.count('opción vegetariana') == 1
    assert 'No tengo ese dato' not in out
    with app.state.db.session() as s:
        assert s.get(Conversation, 'club-2643179759:5492641111111').needs_human


def test_human_echo_pauses_and_resumes(app):
    send(app, 'hola', WED_21)
    body = {'event': 'message.sent', 'account': {'accountId': 'acc', 'platform': 'whatsapp'},
            'conversation': {'id': 'conv_5492641111111', 'participantId': '5492641111111'},
            'message': {'platformMessageId': 'wamid.human1', 'text': 'Hola Juan, soy Caro',
                        'sentAt': datetime.fromtimestamp(WED_21, TZ).isoformat()}}
    raw = json.dumps(body).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    app.state.client.post('/webhooks/zernio', content=raw, headers={'x-zernio-signature': sig})
    with app.state.db.session() as s:
        conv = s.get(Conversation, 'club-2643179759:5492641111111')
        conv.human_until = WED_21 + 300
        s.commit()
    assert send(app, 'cuanto sale?', WED_21 + 60) == ''
    assert 'Bienvenido' in send(app, 'hola', WED_21 + 400)


def test_questions(app):
    for question, expected in [('donde estan ubicados?', 'Libertador 1525'),
                               ('que horario tienen?', 'miércoles a lunes'),
                               ('como puedo pagar?', 'Efectivo'),
                               ('hacen envios a rivadavia?', 'delivery'),
                               ('como va mi pedido?', 'No encuentro pedidos')]:
        out = send(app, question, WED_21)
        assert expected in out and 'equipo' not in out, out
    with app.state.db.session() as s:
        assert not s.get(Conversation, 'club-2643179759:5492641111111').needs_human
    out = send(app, 'tienen opcion sin tacc?', WED_21)
    assert 'No tenemos opción sin TACC' in out
    with app.state.db.session() as s:
        assert not s.get(Conversation, 'club-2643179759:5492641111111').needs_human


def test_faq_answers(app):
    for question, expected in [('que juegos tienen?', 'Jenga'),
                               ('hay estacionamiento?', 'Sí, hay estacionamiento'),
                               ('hacen catering?', 'No hacemos catering'),
                               ('cuanto pesa la carne?', '120 g'),
                               ('tienen menu para chicos?', 'No tenemos menú para chicos')]:
        out = send(app, question, WED_21)
        assert expected in out, out
    out = send(app, 'puedo reservar una mesa?', WED_21)
    assert 'por este mismo WhatsApp' in out
    with app.state.db.session() as s:
        assert s.get(Conversation, 'club-2643179759:5492641111111').needs_human


def test_chat_can_be_deleted_from_panel(app):
    from urllib.parse import quote
    h = {'Authorization': 'Bearer ' + ADMIN}
    send(app, 'hola', WED_21)
    cid = 'club-2643179759:5492641111111'
    with app.state.db.session() as s:
        assert s.get(Conversation, cid)
        assert s.query(Message).filter_by(conversation_id=cid).count() >= 2
    r = app.state.client.delete('/admin/conversations/' + quote(cid, safe=''), headers=h)
    assert r.status_code == 200 and r.json()['deleted'] is True
    with app.state.db.session() as s:
        assert not s.get(Conversation, cid)
        assert s.query(Message).filter_by(conversation_id=cid).count() == 0
        assert s.query(Event).filter_by(conversation_id=cid).count() == 0
        assert s.query(Outbox).filter_by(conversation_id=cid).count() == 0
    assert app.state.client.delete('/admin/conversations/' + quote(cid, safe=''), headers=h).status_code == 404


def test_order_keeps_ledger_when_chat_is_deleted(app):
    from urllib.parse import quote
    h = {'Authorization': 'Bearer ' + ADMIN}
    place_takeaway(app, '1 american')
    cid = 'club-2643179759:5492641111111'
    assert app.state.client.delete('/admin/conversations/' + quote(cid, safe=''), headers=h).status_code == 200
    orders = app.state.client.get('/admin/orders?service_date=2026-11-18', headers=h).json()['orders']
    assert len(orders) == 1 and orders[0]['has_chat'] is False


def test_panel_requires_token(app):
    assert app.state.client.get('/admin/orders').status_code == 401
    assert app.state.client.get('/admin/insumos').status_code == 401
    assert app.state.client.get('/admin/promos').status_code == 401
    assert app.state.client.post('/admin/promos', json={
        'name': 'X', 'product_id': 'combo3', 'discount_percent': 10,
        'starts_at': 1, 'ends_at': 2,
    }).status_code == 401
    assert app.state.client.post('/admin/menu', json={'name': 'X', 'category': 'Bebidas'}).status_code == 401
    assert app.state.client.delete('/admin/menu/american').status_code == 401
    assert app.state.client.get('/panel').status_code == 200


def test_deliver_through_zernio_and_24h_window(app):
    import httpx, time as _t
    bot = app.state.bot
    bot.settings.bot_enabled = True
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'success': True, 'data': {'messageId': 'out.1'}})
    bot.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    now = _t.time()
    send(app, 'hola', now)
    db = app.state.db
    with db.session() as s:
        s.query(Outbox).update({Outbox.status: 'pending'})
        out_id = s.scalar(select(Outbox.id))
        s.commit()
    asyncio.run(bot.deliver(out_id))
    assert seen[0][0] == '/api/v1/inbox/conversations/conv_5492641111111/messages'
    assert seen[0][1]['buttons'][0]['payload'] == 'menu'
    with db.session() as s:
        assert s.get(Outbox, out_id).status == 'sent'
        conv = s.get(Conversation, 'club-2643179759:5492641111111')
        conv.last_inbound = now - 25 * 3600
        row = s.get(Outbox, out_id)
        row.status = 'pending'
        s.commit()
    asyncio.run(bot.deliver(out_id))
    with db.session() as s:
        assert s.get(Outbox, out_id).status == 'expired_window'


def test_product_question_is_answered_not_ordered(app):
    out = send(app, '¿qué trae la american?', WED_21)
    assert 'panceta' in out and '$9.800' in out
    with app.state.db.session() as s:
        ctx = app.state.db.unpack(s.get(Conversation, 'club-2643179759:5492641111111').context)
    assert not ctx.get('step')


def test_question_during_order_keeps_cart(app):
    send(app, '2 american', WED_21)
    out = send(app, '¿la cheese onion trae cebolla?', WED_21)
    assert 'aros de cebolla' in out and 'Tu pedido' not in out
    for step in ['listo', 'retiro', 'Juan', '1', 'efectivo']:
        send(app, step, WED_21)
    out = send(app, '¿la american viene con papas?', WED_21)
    assert 'Burger American' in out
    out = send(app, '✅ Confirmar', WED_21)
    assert 'Pedido *#1* recibido' in out
    with app.state.db.session() as s:
        assert s.scalar(select(Order)).total == 19600


def test_fee_after_confirmation_is_sent(app):
    h = {'Authorization': 'Bearer ' + ADMIN}
    place_takeaway(app, '1 american')
    with app.state.db.session() as s:
        oid = s.scalar(select(Order.id))
    app.state.client.post(f'/admin/orders/{oid}/status', json={'status': 'confirmado'}, headers=h)
    assert 'confirmado' in replies(app)
    app.state.client.post(f'/admin/orders/{oid}/status', json={'status': 'confirmado', 'delivery_fee': 2000}, headers=h)
    assert 'envío de tu pedido *#1* es $2.000. Total: $11.800' in replies(app)


def test_cancel_within_ten_minutes_returns_stock(app):
    place_takeaway(app, '2 american')
    out = send(app, 'cancelar', WED_21 + 60)
    assert 'cancelé el pedido' in out
    with app.state.db.session() as s:
        assert s.scalar(select(Order)).status == 'cancelado'
        from app.db import DailyStock
        med = s.scalar(select(DailyStock).where(DailyStock.insumo_id == 'medallon'))
        assert med.used == 0


def test_cancel_after_ten_minutes_derives(app):
    place_takeaway(app, '2 american')
    out = send(app, 'cancelar', WED_21 + 700)
    assert 'más de 10 minutos' in out
    with app.state.db.session() as s:
        assert s.scalar(select(Order)).status == 'nuevo'
        assert s.get(Conversation, 'club-2643179759:5492641111111').needs_human


def test_long_button_message_is_split(app):
    from app.flow import Flow
    with app.state.db.session() as s:
        f = Flow(s, app.state.db, NEGOCIO, load_items(s), WED_21)
        f.say('x' * 700, [{'title': 'A', 'payload': 'a'}])
    assert f.replies[0]['buttons'] is None and f.replies[1]['buttons']


class FakeLLM:
    def __init__(self, result):
        self.result = result

    async def interpret(self, text, items, negocio, step=None):
        return self.result

    async def close(self):
        pass


@pytest.mark.parametrize('result,expected', [
    ({'intent': 'pedido', 'items': [{'id': 'american', 'qty': 2, 'note': ''}], 'answer': None}, '2 x Burger American'),
    ({'intent': 'consulta', 'items': [], 'answer': 'Sí, tenemos salón con tragos y juegos.'}, 'salón con tragos'),
    ({'intent': 'direccion', 'items': [], 'answer': None}, 'Libertador 1525'),
])
def test_deepseek_paths(app, result, expected):
    app.state.bot.llm = FakeLLM(result)
    out = send(app, 'mensaje libre que las reglas no entienden', WED_21)
    assert expected in out and 'equipo' not in out


# ---------------- Mercado Pago Checkout Pro
def test_mercadopago_link_is_sent_automatically(app, monkeypatch):
    calls = enable_mp(app, monkeypatch)
    out = place_takeaway_mp(app)
    assert 'Pedido *#1* recibido' in out
    assert 'Pago online del pedido #1' in out
    assert 'https://mp.test/pay/pref-1' in out
    assert 'https://mp.test/pay/pref-1' in send(app, 'mandame el link de pago', WED_21)
    assert calls and calls[0]['total'] == 9800 and calls[0]['base_url'] == 'https://bot.test'
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.payment_provider == 'mercadopago'
        assert order.payment_status == 'pendiente'
        assert order.payment_preference_id == 'pref-1'
        assert order.payment_url == 'https://mp.test/pay/pref-1'
        assert order.total == 9800


def test_mercadopago_webhook_marks_paid_and_is_idempotent(app, monkeypatch):
    enable_mp(app, monkeypatch)
    place_takeaway_mp(app)
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        order_id, total = order.id, order.total
    payment = {'id': 123, 'status': 'approved', 'external_reference': order_id,
               'transaction_amount': total, 'currency_id': 'ARS', 'preference_id': 'pref-1'}

    async def fake_payment(payment_id):
        return payment

    monkeypatch.setattr(app.state.bot.mp, 'get_payment', fake_payment)
    response = mp_post(app, 123)
    assert response.status_code == 200, response.text
    assert 'Pago acreditado' in replies(app)
    with app.state.db.session() as s:
        order = s.get(Order, order_id)
        assert order.payment_status == 'pagado' and order.payment_id == '123' and order.paid_at
    # Mercado Pago may retry the same notification: it must not charge or notify twice.
    assert mp_post(app, 123).status_code == 200
    assert replies(app) == ''


def test_mercadopago_webhook_rejects_bad_signature(app, monkeypatch):
    enable_mp(app, monkeypatch)
    place_takeaway_mp(app)
    response = mp_post(app, 123, signature='ts=1700000000,v1=bad')
    assert response.status_code == 403
    with app.state.db.session() as s:
        assert s.scalar(select(Order)).payment_status == 'pendiente'


def test_mercadopago_webhook_after_manual_mark_is_not_a_duplicate(app, monkeypatch):
    enable_mp(app, monkeypatch)
    place_takeaway_mp(app)
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        order_id, total = order.id, order.total
    h = {'Authorization': 'Bearer ' + ADMIN}
    assert app.state.client.post(f'/admin/orders/{order_id}/mark-paid', headers=h).status_code == 200
    assert 'Registramos el pago' in replies(app)
    payment = {'id': 125, 'status': 'approved', 'external_reference': order_id,
               'transaction_amount': total, 'currency_id': 'ARS'}

    async def fake_payment(payment_id):
        return payment

    monkeypatch.setattr(app.state.bot.mp, 'get_payment', fake_payment)
    assert mp_post(app, 125).status_code == 200
    assert replies(app) == ''
    with app.state.db.session() as s:
        order = s.get(Order, order_id)
        assert order.payment_id == '125'
        assert not s.get(Conversation, order.conversation_id).needs_human


def test_mercadopago_amount_mismatch_flags_review(app, monkeypatch):
    enable_mp(app, monkeypatch)
    place_takeaway_mp(app)
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        order_id, total = order.id, order.total
    payment = {'id': 124, 'status': 'approved', 'external_reference': order_id,
               'transaction_amount': total + 100, 'currency_id': 'ARS'}

    async def fake_payment(payment_id):
        return payment

    monkeypatch.setattr(app.state.bot.mp, 'get_payment', fake_payment)
    assert mp_post(app, 124).status_code == 200
    with app.state.db.session() as s:
        order = s.get(Order, order_id)
        assert order.payment_status == 'revision'
        assert s.get(Conversation, order.conversation_id).needs_human
    assert 'Pago acreditado' not in replies(app)


def test_mercadopago_delivery_link_waits_for_shipping_fee(app, monkeypatch):
    calls = enable_mp(app, monkeypatch)
    for step in ['1 american', 'listo', 'delivery', 'Calle Mendoza 123', 'Juan', '1', 'mercado pago', 'si']:
        out = send(app, step, WED_21)
    assert 'Cuando confirmemos el costo del envío te mando el link' in out
    assert calls == []
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        order_id = order.id
        assert order.delivery_fee is None and order.payment_url == ''
    h = {'Authorization': 'Bearer ' + ADMIN}
    response = app.state.client.post(
        f'/admin/orders/{order_id}/status',
        json={'status': 'confirmado', 'delivery_fee': 1500}, headers=h)
    assert response.status_code == 200, response.text
    assert 'https://mp.test/pay/pref-1' in replies(app)
    assert calls and calls[0]['total'] == 11300
    with app.state.db.session() as s:
        order = s.get(Order, order_id)
        assert order.delivery_fee == 1500 and order.total == 11300
        assert order.payment_url == 'https://mp.test/pay/pref-1'


def test_admin_marks_alias_transfer_paid(app):
    for step in ['1 american', 'listo', 'retiro', 'Juan', '1', 'transferencia', 'si']:
        out = send(app, step, WED_21)
    assert 'Alias' in out and 'comprobante' in out.lower()
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        order_id = order.id
        assert order.payment_status == 'pendiente' and order.payment_provider == 'transferencia'
    h = {'Authorization': 'Bearer ' + ADMIN}
    response = app.state.client.post(f'/admin/orders/{order_id}/mark-paid', headers=h)
    assert response.status_code == 200 and response.json()['payment_status'] == 'pagado'
    assert 'Registramos el pago' in replies(app)
    with app.state.db.session() as s:
        order = s.get(Order, order_id)
        assert order.payment_status == 'pagado' and order.paid_at
    # Idempotent: pressing the button again does not send another message.
    assert app.state.client.post(f'/admin/orders/{order_id}/mark-paid', headers=h).status_code == 200
    assert replies(app) == ''


def test_mercadopago_link_failure_derives_to_human(app, monkeypatch):
    enable_mp(app, monkeypatch)

    async def broken_preference(**kwargs):
        raise RuntimeError('mp down')

    monkeypatch.setattr(app.state.bot.mp, 'create_preference', broken_preference)
    out = place_takeaway_mp(app)
    assert 'No pudimos generar el link de pago' in out
    with app.state.db.session() as s:
        order = s.scalar(select(Order))
        assert order.payment_status == 'pendiente' and order.payment_url == ''
        assert s.get(Conversation, order.conversation_id).needs_human


def test_mercadopago_not_offered_without_credentials(app):
    out = send(app, 'como puedo pagar?', WED_21)
    assert 'Efectivo' in out and 'Mercado Pago' not in out


def test_mercadopago_offered_with_credentials(app, monkeypatch):
    enable_mp(app, monkeypatch)
    out = send(app, 'como puedo pagar?', WED_21)
    assert 'Mercado Pago' in out and 'se acredita solo' in out


def test_payment_result_page(app):
    assert 'Pago recibido' in app.state.client.get('/pago/ok').text
    assert 'Pago pendiente' in app.state.client.get('/pago/pendiente').text
    assert app.state.client.get('/pago/otro').status_code == 404


def test_mercadopago_preference_payload_excludes_cards():
    import httpx
    from app.payments import MercadoPago
    settings = Settings(_env_file=None, mp_access_token='APP_USR-test', mp_webhook_secret='s',
                        public_base_url='https://bot.test', mp_exclude_cards=True)
    seen = {}

    def handler(request):
        seen['payload'] = json.loads(request.content)
        return httpx.Response(200, json={'id': 'pref-9', 'init_point': 'https://mp.test/pref-9'})

    mp = MercadoPago(settings, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        result = asyncio.run(mp.create_preference(
            order_id='order-1', number=7, total=12300, description='2x Burger', base_url='https://bot.test'))
    finally:
        asyncio.run(mp.close())
    assert result == {'id': 'pref-9', 'url': 'https://mp.test/pref-9'}
    payload = seen['payload']
    assert payload['external_reference'] == 'order-1'
    assert payload['notification_url'] == 'https://bot.test/webhooks/mercadopago'
    assert payload['back_urls']['success'] == 'https://bot.test/pago/ok'
    assert payload['items'][0]['unit_price'] == 12300.0
    assert payload['payment_methods']['excluded_payment_types'] == [
        {'id': 'credit_card'}, {'id': 'debit_card'}]


def test_payment_columns_migrate_existing_orders_table(tmp_path, monkeypatch):
    from sqlalchemy import text
    from app.db import Database
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, app_env='local', database_url=f'sqlite:///{tmp_path}/old.db',
                        data_encryption_key=Fernet.generate_key().decode())
    db = Database(settings)
    with db.engine.begin() as conn:
        conn.execute(text('''CREATE TABLE orders (
            id VARCHAR(36) PRIMARY KEY, number INTEGER, conversation_id VARCHAR(160),
            service_date VARCHAR(10), status VARCHAR(16), slot VARCHAR(20), mode VARCHAR(12),
            payment VARCHAR(30), total INTEGER, delivery_fee INTEGER, data BLOB,
            consumption TEXT, receipt_received BOOLEAN, created FLOAT, updated FLOAT)'''))
        conn.execute(text(
            "INSERT INTO orders (id, number, conversation_id, service_date, status, slot, mode, "
            "payment, total, data, consumption, receipt_received, created, updated) "
            "VALUES ('o1', 1, 'c', '2026-11-18', 'nuevo', '20:00', 'takeaway', 'Efectivo', "
            "10000, X'00', '{}', 0, 0, 0)"))
    db.initialize()
    with db.engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text('PRAGMA table_info(orders)'))}
    assert {'payment_status', 'payment_provider', 'payment_id', 'payment_preference_id',
            'payment_url', 'paid_at'} <= columns
    with db.session() as s:
        order = s.get(Order, 'o1')
        assert order.payment_status == 'a_cobrar'
    db.engine.dispose()
