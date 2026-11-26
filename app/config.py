from pathlib import Path
from typing import Literal
from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', env_ignore_empty=True)
    app_env: str = 'local'
    database_url: str = 'sqlite:///./data/burger.db'
    admin_token: str = ''
    data_encryption_key: str = ''
    deepseek_api_key: str = ''
    deepseek_model: str = 'deepseek-flash'
    # Tope diario de llamadas a DeepSeek. Al superarlo el bot sigue con reglas y menú numerado.
    deepseek_daily_cap: int = 2000
    whatsapp_provider: Literal['zernio'] = 'zernio'
    zernio_api_key: str = ''
    zernio_account_id: str = ''
    zernio_webhook_secret: str = ''
    # Mercado Pago Checkout Pro. Si falta el token o el secreto, el bot no ofrece pago online.
    mp_access_token: str = ''
    mp_webhook_secret: str = ''
    # URL pública del servicio: se usa en notification_url y back_urls de Mercado Pago.
    public_base_url: str = 'https://clubdelaburger-whatsapp.onrender.com'
    # El local cobra recargo por tarjeta: por defecto el checkout solo muestra transferencia/saldo.
    mp_exclude_cards: bool = True
    # Vigencia del link de pago. Si vence, el panel permite generar uno nuevo.
    mp_link_expires_minutes: int = 60
    # Identificador interno del número (cualquier texto estable; se usa como prefijo de conversación).
    whatsapp_phone_number_id: str = 'club-2643179759'
    bot_enabled: bool = False
    # Lista opcional de números (solo dígitos, separados por coma) para un piloto restringido.
    demo_allowlist: str = ''
    # Minutos sin mensajes del equipo tras los cuales el bot retoma solo una conversación.
    human_pause_minutes: int = 5

    def prepare(self):
        if self.app_env != 'local':
            if not self.admin_token or len(self.admin_token) < 32 or not self.data_encryption_key:
                raise ValueError('Production requires ADMIN_TOKEN (32+ characters) and DATA_ENCRYPTION_KEY')
            if not self.database_url.startswith(('postgres://', 'postgresql')):
                raise ValueError('Production requires persistent PostgreSQL')
        else:
            Path('data').mkdir(exist_ok=True)
            if not self.data_encryption_key:
                keyfile = Path('data/encryption.key')
                if not keyfile.exists():
                    keyfile.write_bytes(Fernet.generate_key())
                self.data_encryption_key = keyfile.read_text().strip()
        Fernet(self.data_encryption_key.encode())
        if self.bot_enabled and not self.ready_for_whatsapp():
            raise ValueError('BOT_ENABLED requires Zernio credentials')
        return self

    def ready_for_whatsapp(self):
        return all((self.zernio_api_key, self.zernio_account_id,
                    self.zernio_webhook_secret, self.whatsapp_phone_number_id))

    def ready_for_mercadopago(self):
        return bool(self.mp_access_token and self.mp_webhook_secret and self.public_base_url)
