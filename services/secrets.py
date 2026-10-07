"""Fernet encryption for secrets stored in system_settings (at rest).

SECRETS — key management & rotation
-----------------------------------
* The encryption key comes from the ``DB_ENCRYPTION_KEY`` env var and must
  be a Fernet key (urlsafe base64, 44 chars), e.g.::

      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

* If the env var is missing the app still boots, but encrypts with an
  ephemeral key generated at startup — a loud warning is logged and any
  values written with it are unreadable after restart. Fix: set the env
  var in production before saving any secret.
* Ciphertexts are version-tagged (``v1:<token>``) so a future rotation
  scheme can distinguish key generations. ``decrypt_value`` also accepts
  untagged legacy tokens.
* KEY ROTATION is NOT automatic: values are encrypted with the key that
  was active when they were saved. To rotate:
    1. with the OLD key still configured, read each secret
       (``get_secret`` decrypts it),
    2. set the NEW key in ``DB_ENCRYPTION_KEY``,
    3. re-save each secret (admin Settings page, or ``set_secret``) so it
       is re-encrypted — and re-tagged — with the new key.
  Rotating without re-saving leaves old ciphertext undecryptable.
* Keys are chosen for encryption by name pattern: ``*api_key*``,
  ``*_secret``, ``*_token``, ``*_key`` (e.g. ``kd1s_api_key``).
* Never encrypt passwords (they use bcrypt hashes in services/auth.py).
  Values are never logged or printed by this module.
"""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_ENV_VAR = "DB_ENCRYPTION_KEY"
_VERSION_TAG = "v1:"

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Return the app Fernet instance (singleton).

    Uses the env key when present; otherwise generates an ephemeral key
    and logs a loud warning. Never raises on a missing key.
    """
    global _fernet
    if _fernet is not None:
        return _fernet
    raw = os.environ.get(_ENV_VAR, "").strip()
    if raw:
        try:
            _fernet = Fernet(raw.encode())
            return _fernet
        except Exception:
            log.error(
                "%s is set but is not a valid Fernet key; falling back to an "
                "ephemeral key. Secrets will NOT survive a restart.", _ENV_VAR
            )
    else:
        log.warning(
            "!!! %s is not set — generating an EPHEMERAL encryption key. "
            "Secrets written now will be unreadable after restart. Set %s "
            "in production. !!!", _ENV_VAR, _ENV_VAR
        )
    _fernet = Fernet(Fernet.generate_key())
    return _fernet


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext secret; returns the version-tagged token."""
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be str")
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _VERSION_TAG + token


def decrypt_value(token: str) -> str:
    """Decrypt a version-tagged (or legacy untagged) token to plaintext.

    Raises ValueError if the token is not valid ciphertext for the
    current key.
    """
    if not isinstance(token, str):
        raise TypeError("token must be str")
    raw = token[len(_VERSION_TAG):] if token.startswith(_VERSION_TAG) else token
    try:
        return _get_fernet().decrypt(raw.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("invalid or undecryptable secret token") from e


def is_sensitive_key(key: str) -> bool:
    """True if a system_settings key name looks like a secret."""
    k = (key or "").lower()
    return ("api_key" in k or k.endswith(("_secret", "_token", "_key")))


def get_secret(db, key: str, default=None):
    """Read a secret from system_settings.

    - If the stored value is Fernet-encrypted (``v1:``-tagged or legacy),
      decrypt and return it.
    - If it is stored in plaintext, return it AND re-encrypt it in place
      (transparent migration on write; the caller's commit() persists it).
    - Return ``default`` when the key does not exist or the ciphertext is
      undecryptable with the current key.
    Never logs or prints the value.
    """
    from models import SystemSetting

    row = db.query(SystemSetting).filter_by(key=key).first()
    if row is None or row.value is None:
        return default
    value = row.value
    if _looks_encrypted(value):
        try:
            return decrypt_value(value)
        except ValueError:
            # ciphertext undecryptable with the current key (rotated key?)
            log.error("secret %r is not decryptable with the current key", key)
            return default
    # plaintext: return it, then migrate to encrypted on write
    if is_sensitive_key(key):
        try:
            row.value = encrypt_value(value)
            db.flush()  # caller's commit() persists the migration
        except Exception:
            log.exception("failed to migrate secret %r to encrypted storage", key)
    return value


def set_secret(db, key: str, value: str) -> None:
    """Fernet-encrypt ``value`` and store it under ``key`` (always
    encrypted, never plaintext).

    Creates the system_settings row (marked is_secret) when missing.
    """
    from models import SystemSetting

    row = db.query(SystemSetting).filter_by(key=key).first()
    if row is None:
        row = SystemSetting(key=key, is_secret=True)
        db.add(row)
    row.is_secret = True
    row.value = encrypt_value(value)
    db.flush()  # caller's commit() persists


def _looks_encrypted(value: str) -> bool:
    """Heuristic: 'v1:'-tagged, or a bare Fernet token (starts gAAAAA)."""
    v = (value or "").strip()
    if v.startswith(_VERSION_TAG):
        return True
    return len(v) >= 60 and v.startswith("gAAAAA")


__all__ = ["encrypt_value", "decrypt_value", "is_sensitive_key",
           "get_secret", "set_secret"]
