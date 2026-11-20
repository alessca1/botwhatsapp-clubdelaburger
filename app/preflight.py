"""Read-only check: is the WhatsApp number 264 317-9759 connected in Zernio?

Run with the same ZERNIO_API_KEY used in Render:

    python -m app.preflight

It prints the Zernio account id, phone number id and coexistence flag. It never
changes the webhook or sends messages.
"""
import json
import re
import sys
import httpx
from app.config import Settings

TARGET = '5492643179759'


def inspect_accounts(accounts, target=TARGET):
    digits = re.sub(r'\D', '', target)
    found = []
    for account in accounts:
        metadata = account.get('metadata') or {}
        phone = re.sub(r'\D', '', str(metadata.get('displayPhoneNumber') or ''))
        if account.get('platform') == 'whatsapp' and phone == digits:
            found.append({
                'account_id': account.get('_id'),
                'name': account.get('name'),
                'display_phone_number': metadata.get('displayPhoneNumber'),
                'phone_number_id': metadata.get('phoneNumberId'),
                'coexistence': metadata.get('isCoexistence') is True,
            })
    return found


def main():
    settings = Settings()
    if not settings.zernio_api_key:
        raise SystemExit('Falta ZERNIO_API_KEY en el entorno o en .env')
    response = httpx.get('https://zernio.com/api/v1/accounts', timeout=25,
                         headers={'Authorization': f'Bearer {settings.zernio_api_key}'})
    response.raise_for_status()
    accounts = response.json().get('accounts', [])
    found = inspect_accounts(accounts, TARGET)
    result = {
        'target': TARGET,
        'connected': len(found) == 1,
        'accounts': found,
        'configured_account_id': settings.zernio_account_id,
        'bot_enabled': settings.bot_enabled,
        'webhook_secret_loaded': bool(settings.zernio_webhook_secret),
        'deepseek_loaded': bool(settings.deepseek_api_key),
    }
    if len(found) != 1:
        result['next_step'] = ('El número no tiene una conexión única en Zernio. '
                               'Conectalo en Zernio y volvé a correr esta comprobación.')
    elif settings.zernio_account_id != found[0]['account_id']:
        result['next_step'] = ('El número está conectado, pero ZERNIO_ACCOUNT_ID no coincide. '
                               f"Usá {found[0]['account_id']} en Render.")
    else:
        result['next_step'] = 'Todo listo: el número coincide con ZERNIO_ACCOUNT_ID.'
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['connected'] and settings.zernio_account_id == found[0]['account_id'] else 1


if __name__ == '__main__':
    sys.exit(main())
