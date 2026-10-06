"""i Store | ئایستۆر — configuration.

All secrets come from environment variables. Never commit real values.
See .env.example for the full list.
"""
import os
from decimal import Decimal


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _db_default() -> str:
    """Deployment-safe default SQLite path: env override, else next to the app."""
    env = os.environ.get("SQLITE_PATH", "").strip()
    if env:
        return env if "://" in env else f"sqlite:///{env}"
    here = os.path.dirname(os.path.abspath(__file__))
    return f"sqlite:///{os.path.join(here, 'istore.db')}"


class Config:
    SECRET_KEY = _get("SESSION_SECRET", "dev-secret-change-me")
    DATABASE_URL = _get("DATABASE_URL", _db_default())
    ENV = _get("APP_ENV", "development")

    # Provider (KD1S) — server-side only, never exposed to frontend
    KD1S_API_KEY = _get("KD1S_API_KEY", "")
    KD1S_API_URL = _get("KD1S_API_URL", "https://kd1s.com/api/v2")
    PROVIDER_MODE = _get("PROVIDER_MODE", "mock")  # mock | kd1s

    # Telegram
    TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ADMIN_IDS = [x.strip() for x in _get("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip()]

    # Currency
    DEFAULT_CURRENCY = _get("DEFAULT_CURRENCY", "USD")
    USD_TO_IQD = Decimal(_get("USD_TO_IQD", "1500"))

    # Sync / workers
    SYNC_INTERVAL_MINUTES = int(_get("SYNC_INTERVAL_MINUTES", "60"))
    ORDER_POLL_INTERVAL_MINUTES = int(_get("ORDER_POLL_INTERVAL_MINUTES", "5"))

    # Security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    RATELIMIT_LOGIN = int(_get("RATELIMIT_LOGIN", "10"))  # attempts per 15 min
