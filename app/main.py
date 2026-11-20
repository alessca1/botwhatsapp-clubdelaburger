import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from app.config import Settings
from app.db import Database, Conversation, Content, Event, Outbox, Message, Order, Product, ProductVariant, Insumo, DailyStock, Audit
from app.hours import Schedule
from app.service import Bot
from app.zernio import ingest_zernio
from app import stock as stockmod

STATIC = Path(__file__).resolve().parent.parent / 'static'
ORDER_STATUSES = ['nuevo', 'confirmado', 'en_cocina', 'listo', 'en_camino', 'entregado', 'cancelado']


def create_app(settings=None, run_worker=True, llm=None):
    settings = (settings or Settings()).prepare()
    db = Database(settings)
    bot = Bot(settings, db, llm=llm)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        task = asyncio.create_task(bot.run()) if run_worker else None
        app.state.worker = task
        try:
            yield
        finally:
            bot.stopping = True
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await bot.close()
            db.engine.dispose()

    app = FastAPI(title='El Club de la Burger — Bot de WhatsApp', lifespan=lifespan,
                  docs_url='/docs' if settings.app_env == 'local' else None, redoc_url=None,
                  openapi_url='/openapi.json' if settings.app_env == 'local' else None)
    app.state.bot, app.state.db = bot, db
    bearer = HTTPBearer(auto_error=False)

    def admin(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not settings.admin_token or not credentials or not hmac.compare_digest(
                credentials.credentials, settings.admin_token):
            raise HTTPException(401, 'Acceso administrativo requerido')

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers.setdefault('X-Frame-Options', 'DENY')
        return response

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    def ready():
        try:
            with db.session() as s:
                s.execute(text('SELECT 1'))
                bot.negocio(s)
            worker = getattr(app.state, 'worker', None)
            if run_worker and (worker is None or worker.done()):
                raise ValueError('Worker stopped')
            return {'status': 'ok'}
        except Exception:
            raise HTTPException(503, 'Servicio no disponible')

    @app.post('/webhooks/zernio')
    async def zernio_webhook(request: Request):
        if not settings.zernio_webhook_secret:
            raise HTTPException(403, 'Integración no configurada')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1_048_576:
                raise HTTPException(413, 'Webhook demasiado grande')
            chunks.append(chunk)
        raw = b''.join(chunks)
        signature = request.headers.get('x-zernio-signature', '')
        expected = hmac.new(settings.zernio_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(403, 'Firma inválida')
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('Invalid payload')
            ingest_zernio(bot, payload)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(400, 'Evento inválido')
        return {'received': True}

    # ---------- panel
    @app.get('/panel')
    def panel():
        return FileResponse(STATIC / 'panel.html', headers={'X-Frame-Options': 'DENY'})

    @app.get('/panel/{name}')
    def panel_asset(name: str):
        if name not in {'panel.js', 'panel.css'}:
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    def _service_date(value):
        if value:
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                raise HTTPException(400, 'Fecha inválida')
        with db.session() as s:
            schedule = Schedule(bot.negocio(s))
        now = schedule.local()
        current = schedule.current_service(now)
        return (current or now.date()).isoformat()

    @app.get('/admin/status', dependencies=[Depends(admin)])
    def status():
        with db.session() as s:
            negocio = bot.negocio(s)
            schedule = Schedule(negocio)
            now = schedule.local()
            window = schedule.order_window(now)
            return {'bot_enabled': settings.bot_enabled, 'whatsapp_configured': settings.ready_for_whatsapp(),
                    'business_number': negocio.get('business_number', ''),
                    'business_number_display': negocio.get('business_number_display', ''),
                    'phone_number_id': settings.whatsapp_phone_number_id,
                    'deepseek_configured': bool(settings.deepseek_api_key),
                    'open_now': schedule.is_open(now), 'order_window': window.mode,
                    'service_date': window.service_date.isoformat() if window.service_date else None,
                    'local_time': now.strftime('%Y-%m-%d %H:%M'),
                    'pending_events': s.query(Event).filter_by(status='pending').count(),
                    'failed_events': s.query(Event).filter_by(status='failed').count(),
                    'uncertain_deliveries': s.query(Outbox).filter_by(status='uncertain').count(),
                    'products_without_price': s.query(Product).filter(Product.price.is_(None)).count()}

    @app.get('/admin/orders', dependencies=[Depends(admin)])
    def orders(service_date: str = ''):
        sd = _service_date(service_date)
        with db.session() as s:
            rows = s.scalars(select(Order).where(Order.service_date == sd).order_by(Order.number))
            out = []
            for o in rows:
                data = db.unpack(o.data)
                conv = s.get(Conversation, o.conversation_id)
                out.append({'id': o.id, 'number': o.number, 'status': o.status, 'slot': o.slot, 'mode': o.mode,
                            'payment': o.payment, 'total': o.total, 'delivery_fee': o.delivery_fee,
                            'receipt_received': o.receipt_received, 'created': o.created,
                            'conversation_id': o.conversation_id, 'wa_name': conv.name if conv else '', **data})
            return {'service_date': sd, 'orders': out}

    class StatusBody(BaseModel):
        status: str = Field(pattern='^(' + '|'.join(ORDER_STATUSES) + ')$')
        delivery_fee: int | None = Field(default=None, ge=0, le=1_000_000)

    @app.post('/admin/orders/{order_id}/status', dependencies=[Depends(admin)])
    def order_status(order_id: str, body: StatusBody):
        try:
            order = bot.set_order_status(order_id, body.status, body.delivery_fee)
        except KeyError:
            raise HTTPException(404, 'Pedido inexistente')
        except stockmod.OutOfStock:
            raise HTTPException(409, 'No hay stock para reactivar este pedido')
        return {'id': order.id, 'status': order.status}

    @app.get('/admin/stock', dependencies=[Depends(admin)])
    def stock(service_date: str = ''):
        sd = _service_date(service_date)
        with db.session() as s:
            stockmod.remaining(s, sd)  # creates the night's rows from defaults
            s.commit()
            out = []
            for insumo in s.scalars(select(Insumo)):
                row = s.scalar(select(DailyStock).where(DailyStock.service_date == sd,
                                                        DailyStock.insumo_id == insumo.id))
                out.append({'id': insumo.id, 'name': insumo.name, 'unit': insumo.unit,
                            'default_daily': insumo.default_daily, 'initial': row.initial, 'used': row.used,
                            'remaining': None if row.initial is None else row.initial - row.used})
            return {'service_date': sd, 'insumos': out}

    class StockBody(BaseModel):
        initial: int | None = Field(default=None, ge=0, le=100_000)
        remaining: int | None = Field(default=None, ge=0, le=100_000)
        unlimited: bool = False
        default_daily: int | None = Field(default=None, ge=0, le=100_000)
        clear_default: bool = False

    @app.put('/admin/stock/{insumo_id}', dependencies=[Depends(admin)])
    def set_stock(insumo_id: str, body: StockBody, service_date: str = ''):
        sd = _service_date(service_date)
        with db.session() as s:
            insumo = s.get(Insumo, insumo_id)
            if not insumo:
                raise HTTPException(404, 'Insumo inexistente')
            stockmod.remaining(s, sd)
            row = s.scalar(select(DailyStock).where(DailyStock.service_date == sd,
                                                    DailyStock.insumo_id == insumo_id).with_for_update())
            if body.unlimited:
                row.initial = None
            elif body.remaining is not None:
                row.initial = row.used + body.remaining
            elif body.initial is not None:
                row.initial = body.initial
            if body.clear_default:
                insumo.default_daily = None
            elif body.default_daily is not None:
                insumo.default_daily = body.default_daily
            row.updated = time.time()
            s.add(Audit(action='stock_set', target=f'{sd}:{insumo_id}'))
            s.commit()
        return {'saved': True}

    @app.get('/admin/menu', dependencies=[Depends(admin)])
    def menu():
        with db.session() as s:
            variants = {}
            for v in s.scalars(select(ProductVariant).order_by(ProductVariant.position)):
                variants.setdefault(v.product_id, []).append(
                    {'id': v.id, 'name': v.name, 'price': v.price, 'recipe': json.loads(v.recipe)})
            return [{'id': p.id, 'category': p.category, 'name': p.name, 'description': p.description,
                     'price': p.price, 'available': p.available, 'days': json.loads(p.days),
                     'recipe': json.loads(p.recipe), 'variants': variants.get(p.id, [])}
                    for p in s.scalars(select(Product).order_by(Product.position))]

    class ProductBody(BaseModel):
        price: int | None = Field(default=None, ge=0, le=1_000_000)
        clear_price: bool = False
        available: bool | None = None
        description: str | None = Field(default=None, max_length=400)
        recipe: dict[str, int] | None = None
        variants: dict[str, int] | None = None

    @app.patch('/admin/menu/{product_id}', dependencies=[Depends(admin)])
    def update_product(product_id: str, body: ProductBody):
        with db.session() as s:
            p = s.get(Product, product_id)
            if not p:
                raise HTTPException(404, 'Producto inexistente')
            rows = list(s.scalars(select(ProductVariant).where(ProductVariant.product_id == product_id)
                                  .order_by(ProductVariant.position)))
            by_id = {v.id: v for v in rows}
            if body.variants is not None:
                if not set(body.variants) <= set(by_id) or any(not 0 <= v <= 1_000_000 for v in body.variants.values()):
                    raise HTTPException(400, 'Variantes inválidas')
                for vid, price in body.variants.items():
                    by_id[vid].price = price
                rows.sort(key=lambda v: v.position)
                if rows:
                    p.price = rows[0].price
            if body.clear_price:
                p.price = None
                if rows:
                    rows[0].price = None
            elif body.price is not None:
                p.price = body.price
                if rows:
                    rows[0].price = body.price
            if body.available is not None:
                p.available = body.available
            if body.description is not None:
                p.description = body.description
            if body.recipe is not None:
                known = {i.id for i in s.scalars(select(Insumo))}
                if not set(body.recipe) <= known or any(v < 0 or v > 50 for v in body.recipe.values()):
                    raise HTTPException(400, 'Receta inválida')
                p.recipe = json.dumps(body.recipe)
            s.add(Audit(action='product_updated', target=product_id))
            s.commit()
        return {'saved': True}

    @app.get('/admin/content', dependencies=[Depends(admin)])
    def get_content():
        with db.session() as s:
            return bot.negocio(s)

    @app.put('/admin/content', dependencies=[Depends(admin)])
    async def put_content(request: Request):
        body = await request.json()
        try:
            Schedule(body)
            for key in ('business_name', 'address', 'welcome', 'faq', 'payment_methods'):
                body[key]
        except (KeyError, ValueError, TypeError):
            raise HTTPException(400, 'Contenido inválido')
        with db.session() as s:
            row = s.get(Content, 'negocio')
            row.body, row.updated = json.dumps(body, ensure_ascii=False), time.time()
            s.add(Audit(action='content_updated', target='negocio'))
            s.commit()
        return {'saved': True}

    @app.get('/admin/conversations', dependencies=[Depends(admin)])
    def conversations():
        with db.session() as s:
            rows = s.scalars(select(Conversation).order_by(Conversation.updated.desc()).limit(80))
            now = time.time()
            return [{'id': c.id, 'wa_id': c.wa_id, 'name': c.name, 'needs_human': c.needs_human,
                     'bot_paused': c.state == 'human_active' and now < c.human_until,
                     'updated': c.updated} for c in rows]

    @app.get('/admin/conversations/{conv_id}/messages', dependencies=[Depends(admin)])
    def messages(conv_id: str):
        with db.session() as s:
            rows = s.scalars(select(Message).where(Message.conversation_id == conv_id)
                             .order_by(Message.created.desc()).limit(100))
            return [{'direction': m.direction, 'text': db.unpack(m.text), 'created': m.created}
                    for m in reversed(list(rows))]

    class ReplyBody(BaseModel):
        text: str = Field(min_length=1, max_length=3000)

    @app.post('/admin/conversations/{conv_id}/reply', dependencies=[Depends(admin)])
    def reply(conv_id: str, body: ReplyBody):
        try:
            bot.human_reply(conv_id, body.text)
        except KeyError:
            raise HTTPException(404, 'Conversación inexistente')
        return {'queued': True}

    class BotBody(BaseModel):
        action: str = Field(pattern='^(pause|resume|resolved)$')

    @app.post('/admin/conversations/{conv_id}/bot', dependencies=[Depends(admin)])
    def bot_action(conv_id: str, body: BotBody):
        with db.session() as s:
            conv = s.get(Conversation, conv_id)
            if not conv:
                raise HTTPException(404, 'Conversación inexistente')
            if body.action == 'pause':
                conv.state, conv.human_until = 'human_active', time.time() + 12 * 3600
            elif body.action == 'resume':
                conv.state, conv.human_until = 'automatic', 0
            conv.needs_human = False if body.action in {'resume', 'resolved'} else conv.needs_human
            s.commit()
        return {'saved': True}

    return app
