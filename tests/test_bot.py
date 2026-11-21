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
from app.db import Order, Outbox, Conversation, Product, ProductVariant, Insumo
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


def test_public_menu_page_and_photo_upload(app):
    import base64
    client = app.state.client
    page = client.get('/carta')
    assert page.status_code == 200
    assert 'El Club de la Burger' in page.text and 'La carta' in page.text
    assert 'Burger Clásica' in page.text
    assert client.get('/carta/carta.css').status_code == 200
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


def test_panel_requires_token(app):
    assert app.state.client.get('/admin/orders').status_code == 401
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
