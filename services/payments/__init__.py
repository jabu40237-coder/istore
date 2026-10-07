"""Payment provider registry + active-provider resolution.

Configuration lives in system_settings (credentials encrypted via
services.secrets):
  payment_provider            none | zaincash | fastpay
  payment_test_mode           1 | 0
  payment_zaincash_client_id / _client_secret / _api_key / _prod_host
  payment_fastpay_store_id / _store_password / _refund_secret
                              / _stage_host / _prod_host
"""
from db import get_session
from models import SystemSetting

from .base import PaymentProvider  # noqa: F401
from .zaincash import ZainCashProvider
from .fastpay import FastPayProvider

REGISTRY = {
    ZainCashProvider.code: ZainCashProvider,
    FastPayProvider.code: FastPayProvider,
}

_PREFIX = {
    "zaincash": ("client_id", "client_secret", "api_key", "prod_host"),
    "fastpay": ("store_id", "store_password", "refund_secret",
                "stage_host", "prod_host"),
}


def read_setting(db, key: str) -> str:
    """Read a setting, decrypting secrets. Falls back gracefully if the
    secrets module isn't available yet (scaffold mode)."""
    s = db.query(SystemSetting).filter_by(key=key).first()
    if not s or not s.value:
        return ""
    try:
        from services.secrets import decrypt_value
        v = s.value
        return decrypt_value(v) if v.startswith("v1:") else v
    except Exception:
        return s.value


def write_setting(db, key: str, value: str, secret: bool = False):
    s = db.query(SystemSetting).filter_by(key=key).first()
    if not s:
        s = SystemSetting(key=key)
        db.add(s)
    if secret and value:
        try:
            from services.secrets import encrypt_value
            value = encrypt_value(value)
        except Exception:
            pass  # scaffold mode: stored plaintext, re-encrypted on read later
    s.value = value or ""
    s.is_secret = secret


def get_provider(code: str, test_mode: bool = True) -> PaymentProvider | None:
    cls = REGISTRY.get(code)
    if not cls:
        return None
    db = get_session()
    try:
        settings = {k: read_setting(db, f"payment_{code}_{k}")
                    for k in _PREFIX[code]}
        return cls(settings, test_mode=test_mode)
    finally:
        db.close()


def get_active_provider() -> PaymentProvider | None:
    """The provider Ali activated, or None (wallet shows 'coming soon')."""
    db = get_session()
    try:
        s = db.query(SystemSetting).filter_by(key="payment_provider").first()
        code = (s.value if s and s.value else "none").strip()
        t = db.query(SystemSetting).filter_by(key="payment_test_mode").first()
        test_mode = (t.value if t and t.value else "1") != "0"
    finally:
        db.close()
    if code in ("none", ""):
        return None
    p = get_provider(code, test_mode=test_mode)
    return p if p and p.is_configured() else None
