"""TOTP 2FA for admin users (pyotp).

Secrets are Fernet-encrypted at rest via services.secrets (DB_ENCRYPTION_KEY).
Backup codes are bcrypt-hashed, single-use, shown once at enrollment.
Only exposed to SUPER_ADMIN via /admin/security.
"""
import base64
import io
import secrets as py_secrets

import bcrypt
import pyotp
import qrcode

from db import get_session
from models import TotpDevice, TotpBackupCode, now


def _enc(plaintext: str) -> str:
    from services.secrets import encrypt_value
    return encrypt_value(plaintext)


def _dec(token: str) -> str:
    from services.secrets import decrypt_value
    return decrypt_value(token)


def _device(db, user_id):
    return db.query(TotpDevice).filter_by(user_id=user_id).first()


def is_enabled(user_id: int) -> bool:
    db = get_session()
    try:
        d = _device(db, user_id)
        return bool(d and d.enabled)
    finally:
        db.close()


def login_requires_totp(user_id: int) -> bool:
    """True when the user must pass a TOTP challenge after password login."""
    return is_enabled(user_id)


def begin_enrollment(user_id: int, issuer: str = "i Store"):
    """(Re)starts enrollment: new secret stored encrypted, disabled until
    confirmed. Old backup codes are invalidated. Returns
    (secret_plain, otpauth_uri, backup_codes_plain)."""
    db = get_session()
    try:
        secret = pyotp.random_base32()
        d = _device(db, user_id)
        if not d:
            d = TotpDevice(user_id=user_id, secret_enc="", enabled=False)
            db.add(d)
        d.secret_enc = _enc(secret)
        d.enabled = False
        # invalidate old backup codes
        db.query(TotpBackupCode).filter_by(user_id=user_id).delete()
        codes = []
        for _ in range(10):
            raw = py_secrets.token_hex(4).upper()  # 8 hex chars
            pretty = f"{raw[:4]}-{raw[4:]}"
            codes.append(pretty)
            db.add(TotpBackupCode(
                user_id=user_id,
                code_hash=bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()))
        db.commit()
        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=f"admin-{user_id}", issuer_name=issuer)
        return secret, uri, codes
    finally:
        db.close()


def qr_data_uri(otpauth_uri: str) -> str:
    img = qrcode.make(otpauth_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def confirm_enrollment(user_id: int, code: str) -> bool:
    db = get_session()
    try:
        d = _device(db, user_id)
        if not d or d.enabled:
            return False
        totp = pyotp.TOTP(_dec(d.secret_enc))
        if totp.verify(code.strip().replace(" ", ""), valid_window=1):
            d.enabled = True
            d.last_used_at = now()
            db.commit()
            return True
        return False
    finally:
        db.close()


def verify_code(user_id: int, code: str) -> bool:
    """Verify a TOTP code or a single-use backup code."""
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code:
        return False
    db = get_session()
    try:
        d = _device(db, user_id)
        if not d or not d.enabled:
            return False
        totp = pyotp.TOTP(_dec(d.secret_enc))
        if totp.verify(code, valid_window=1):
            d.last_used_at = now()
            db.commit()
            return True
        # backup codes (compare against de-prettified raw)
        for bc in db.query(TotpBackupCode).filter_by(
                user_id=user_id, used_at=None).all():
            if bcrypt.checkpw(code.encode(), bc.code_hash.encode()):
                bc.used_at = now()
                d.last_used_at = now()
                db.commit()
                return True
        return False
    finally:
        db.close()


def disable(user_id: int):
    db = get_session()
    try:
        db.query(TotpBackupCode).filter_by(user_id=user_id).delete()
        db.query(TotpDevice).filter_by(user_id=user_id).delete()
        db.commit()
    finally:
        db.close()


def backup_codes_remaining(user_id: int) -> int:
    db = get_session()
    try:
        return db.query(TotpBackupCode).filter_by(
            user_id=user_id, used_at=None).count()
    finally:
        db.close()
