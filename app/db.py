import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet
from sqlalchemy import (create_engine, String, Text, LargeBinary, Float, Integer, Boolean,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parent.parent


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    phone_id: Mapped[str] = mapped_column(String(80))
    wa_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120), default='')
    zernio_id: Mapped[str] = mapped_column(String(100), default='')
    # automatic | human_active
    state: Mapped[str] = mapped_column(String(24), default='automatic')
    human_until: Mapped[float] = mapped_column(Float, default=0)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    context: Mapped[bytes] = mapped_column(LargeBinary)
    last_inbound: Mapped[float] = mapped_column(Float, default=0)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Message(Base):
    """Readable chat history for the panel (text encrypted at rest)."""
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(160), index=True)
    # in | bot | human
    direction: Mapped[str] = mapped_column(String(8))
    text: Mapped[bytes] = mapped_column(LargeBinary)
    created: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[str] = mapped_column(String(240), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(160), index=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[float] = mapped_column(Float, default=time.time)
    error: Mapped[str] = mapped_column(String(80), default='')
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Outbox(Base):
    __tablename__ = 'outbox'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(160), index=True)
    body: Mapped[bytes] = mapped_column(LargeBinary)
    # bot | human | status
    kind: Mapped[str] = mapped_column(String(12), default='bot')
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    provider_id: Mapped[str] = mapped_column(String(200), default='', index=True)
    error: Mapped[str] = mapped_column(String(80), default='')
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Content(Base):
    __tablename__ = 'content'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Product(Base):
    __tablename__ = 'products'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    category: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default='')
    aliases: Mapped[str] = mapped_column(Text, default='[]')
    # None = sin precio confirmado: no se puede pedir.
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    days: Mapped[str] = mapped_column(Text, default='[]')
    recipe: Mapped[str] = mapped_column(Text, default='{}')
    position: Mapped[int] = mapped_column(Integer, default=0)


class ProductVariant(Base):
    """Size/option of a product (Simple, Doble, Triple) with its own price and recipe."""
    __tablename__ = 'product_variants'
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(40))
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recipe: Mapped[str] = mapped_column(Text, default='{}')
    position: Mapped[int] = mapped_column(Integer, default=0)


class ProductImage(Base):
    """Public menu photo uploaded from the panel. Stored in Postgres so it survives deploys."""
    __tablename__ = 'product_images'
    product_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(40))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Promo(Base):
    """Time-limited discount on one product. The product must be a combo."""
    __tablename__ = 'promos'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    product_id: Mapped[str] = mapped_column(String(40), index=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[float] = mapped_column(Float)
    ends_at: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Insumo(Base):
    __tablename__ = 'insumos'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(20), default='u')
    # Cantidad que se carga por defecto cada noche. None = sin control de stock.
    default_daily: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DailyStock(Base):
    """Stock of one insumo for one service night. Orders for that night consume it."""
    __tablename__ = 'daily_stock'
    __table_args__ = (UniqueConstraint('service_date', 'insumo_id', name='uq_stock_day'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    service_date: Mapped[str] = mapped_column(String(10), index=True)
    insumo_id: Mapped[str] = mapped_column(String(40))
    initial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Order(Base):
    __tablename__ = 'orders'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    number: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str] = mapped_column(String(160), index=True)
    service_date: Mapped[str] = mapped_column(String(10), index=True)
    # nuevo | confirmado | en_cocina | listo | en_camino | entregado | cancelado
    status: Mapped[str] = mapped_column(String(16), default='nuevo', index=True)
    slot: Mapped[str] = mapped_column(String(20), default='')
    mode: Mapped[str] = mapped_column(String(12))
    payment: Mapped[str] = mapped_column(String(30), default='')
    total: Mapped[int] = mapped_column(Integer, default=0)
    delivery_fee: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Encrypted JSON: name, phone, address, items, notes.
    data: Mapped[bytes] = mapped_column(LargeBinary)
    consumption: Mapped[str] = mapped_column(Text, default='{}')
    receipt_received: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Usage(Base):
    __tablename__ = 'usage'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(20), default='')
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(160), default='')
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Database:
    def __init__(self, settings):
        self.settings = settings
        url = settings.database_url
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql+psycopg://', 1)
        elif url.startswith('postgresql://'):
            url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
        self.engine = create_engine(url, pool_pre_ping=True,
                                    connect_args={'check_same_thread': False} if url.startswith('sqlite') else {})
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        self.cipher = Fernet(settings.data_encryption_key.encode())

    def pack(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def unpack(self, value):
        return json.loads(self.cipher.decrypt(value))

    def initialize(self):
        Base.metadata.create_all(self.engine)
        with self.session() as s:
            # Version marker: imports the completed questionnaire once, then never
            # overwrites prices or FAQ edited from the panel.
            seed = s.get(Content, 'seed:2026-11-20-carta-v1')
            negocio = ROOT / 'content/negocio.json'
            current = s.get(Content, 'negocio')
            if not current:
                s.add(Content(id='negocio', body=negocio.read_text(encoding='utf-8')))
            elif not seed:
                current.body, current.updated = negocio.read_text(encoding='utf-8'), time.time()
            carta = json.loads((ROOT / 'content/carta.json').read_text(encoding='utf-8'))
            deleted = {row.target for row in s.query(Audit).filter(Audit.action == 'product_deleted')}
            for position, p in enumerate(carta['productos']):
                if p['id'] in deleted:
                    continue
                row = s.get(Product, p['id'])
                if not row:
                    row = Product(id=p['id'])
                    s.add(row)
                if not seed or not row.category:
                    row.category = p['categoria']
                    row.name = p['nombre']
                    row.description = p.get('descripcion', '')
                    row.aliases = json.dumps(p.get('alias', []), ensure_ascii=False)
                    row.price = p.get('precio')
                    row.days = json.dumps(p.get('dias', []))
                    row.recipe = json.dumps(p.get('receta', {}))
                    row.position = position
                for vposition, v in enumerate(p.get('variantes', [])):
                    vr = s.get(ProductVariant, v['id'])
                    if not vr:
                        vr = ProductVariant(id=v['id'])
                        s.add(vr)
                    if not seed or not vr.product_id:
                        vr.product_id = p['id']
                        vr.name = v['nombre']
                        vr.price = v.get('precio')
                        vr.recipe = json.dumps(v.get('receta', {}))
                        vr.position = vposition
            for i in carta['insumos']:
                row = s.get(Insumo, i['id'])
                if not row:
                    row = Insumo(id=i['id'])
                    s.add(row)
                if not seed or not row.name:
                    row.name = i['nombre']
                    row.unit = i.get('unidad', 'u')
                    row.default_daily = i.get('stock_diario')
            if not seed:
                s.add(Content(id='seed:2026-11-20-carta-v1', body=json.dumps({'seeded_at': time.time()})))
            promo_seed = s.get(Content, 'seed:2026-11-25-promo-prueba')
            if not promo_seed:
                now = time.time()
                local = datetime.fromtimestamp(now, ZoneInfo('America/Argentina/San_Juan'))
                end = local.replace(hour=15, minute=0, second=0, microsecond=0)
                if now < end.timestamp() and not s.get(Promo, 'prueba'):
                    s.add(Promo(id='prueba', name='Prueba', product_id='combo3',
                                discount_percent=15, starts_at=now, ends_at=end.timestamp(),
                                active=True, position=0))
                s.add(Content(id='seed:2026-11-25-promo-prueba',
                              body=json.dumps({'seeded_at': now})))
            # A stopped process may have sent these messages; never replay blindly.
            for row in s.query(Outbox).filter_by(status='sending'):
                row.status = 'uncertain'
            s.commit()
