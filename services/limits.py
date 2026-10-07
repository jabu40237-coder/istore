"""Shared Flask-Limiter instance.

Rate-limit key: ``CF-Connecting-IP`` when the app runs behind a trusted
proxy (``TRUST_PROXY=1``, e.g. behind Cloudflare, which sets that header
and cannot be spoofed through it), otherwise ``request.remote_addr``.
The plain ``X-Forwarded-For`` leftmost value is deliberately NOT trusted:
it is trivially spoofable when the app is not behind a verified proxy, so
spoofed XFF headers are ignored by the limiter.

Storage is in-memory (``memory://``): limits are enforced per-process, so
under gunicorn with several workers each worker gets its own bucket.
Production upgrade: point ``storage_uri`` at a shared store such as Redis
(``redis://localhost:6379/0``) — no other code changes needed.
"""
import logging
import os

from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

log = logging.getLogger("istore.security")


def _rate_limit_key() -> str:
    if os.environ.get("TRUST_PROXY") == "1":
        cf = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf:
            return cf
    return get_remote_address()


def _log_breach(request_limit):
    # Structured security log; never includes credentials or session values.
    try:
        log.warning(
            "event=rate_limit_hit path=%s key=%s limit=%s",
            request.path,
            _rate_limit_key(),
            getattr(request_limit.limit, "limit", "?"),
        )
    except Exception:
        log.warning("event=rate_limit_hit path=%s", request.path)
    return None  # fall through to the default 429 handler


limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=["200 per hour"],
    storage_uri="memory://",
    on_breach=_log_breach,
)

__all__ = ["limiter"]
