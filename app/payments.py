"""Mercado Pago Checkout Pro: payment links and webhook validation."""
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
import httpx

logger = logging.getLogger(__name__)

MP_API = 'https://api.mercadopago.com'


class MercadoPagoError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class MercadoPago:
    def __init__(self, settings, http=None):
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5))

    async def close(self):
        await self.http.aclose()

    def enabled(self):
        return self.settings.ready_for_mercadopago()

    async def create_preference(self, *, order_id, number, total, description, base_url):
        if not self.settings.mp_access_token:
            raise MercadoPagoError('Mercado Pago no está configurado')
        base_url = base_url.rstrip('/')
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=max(5, int(self.settings.mp_link_expires_minutes)))
        payload = {
            'items': [{
                'title': f'Pedido #{number}',
                'description': (description or 'El Club de la Burger')[:120],
                'quantity': 1,
                'currency_id': 'ARS',
                'unit_price': float(total),
            }],
            'external_reference': order_id,
            'notification_url': f'{base_url}/webhooks/mercadopago',
            'back_urls': {
                'success': f'{base_url}/pago/ok',
                'pending': f'{base_url}/pago/pendiente',
                'failure': f'{base_url}/pago/error',
            },
            'auto_return': 'approved',
            'statement_descriptor': 'CLUB DE LA BURGER',
            'expires': True,
            'expiration_date_to': expires_at.strftime('%Y-%m-%dT%H:%M:%S.000+00:00'),
            'metadata': {'order_id': order_id, 'order_number': number},
        }
        if self.settings.mp_exclude_cards:
            payload['payment_methods'] = {
                'excluded_payment_types': [{'id': 'credit_card'}, {'id': 'debit_card'}],
            }
        response = await self.http.post(
            f'{MP_API}/checkout/preferences',
            headers={'Authorization': f'Bearer {self.settings.mp_access_token}'},
            json=payload,
        )
        if response.status_code >= 400:
            logger.error('Mercado Pago preference error %s: %s', response.status_code, response.text[:500])
            raise MercadoPagoError('No se pudo crear el link de pago', response.status_code)
        data = response.json()
        url = data.get('init_point')
        if str(self.settings.mp_access_token).startswith('TEST-'):
            url = data.get('sandbox_init_point') or url
        if not data.get('id') or not url:
            raise MercadoPagoError('Mercado Pago no devolvió el link de pago')
        return {'id': str(data['id']), 'url': url}

    async def get_payment(self, payment_id):
        if not self.settings.mp_access_token:
            raise MercadoPagoError('Mercado Pago no está configurado')
        response = await self.http.get(
            f'{MP_API}/v1/payments/{payment_id}',
            headers={'Authorization': f'Bearer {self.settings.mp_access_token}'},
        )
        if response.status_code >= 400:
            logger.error('Mercado Pago payment error %s: %s', response.status_code, response.text[:500])
            raise MercadoPagoError('No se pudo consultar el pago', response.status_code)
        return response.json()

    @staticmethod
    def verify_signature(secret, signature_header, request_id, data_id):
        """Validate the x-signature header sent by Mercado Pago."""
        if not secret or not signature_header or not data_id:
            return False
        parts = {}
        for chunk in str(signature_header).split(','):
            if '=' in chunk:
                key, value = chunk.split('=', 1)
                parts[key.strip()] = value.strip()
        timestamp, signature = parts.get('ts'), parts.get('v1')
        if not timestamp or not signature:
            return False
        manifest = f'id:{data_id};request-id:{request_id or ""};ts:{timestamp};'
        expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
