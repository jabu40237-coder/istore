"""ZainCash wallet top-up flow. REAL MONEY — idempotency is critical.

Endpoints:
  POST /dashboard/wallet/topup   create TopUp(PENDING) + provider invoice,
                                 redirect user to ZainCash (login + CSRF +
                                 10/hour rate limit)
  GET  /payments/zaincash/return  ?token=JWT redirect callback. Signature is
                                 verified, then inquiry() is the AUTHORITATIVE
                                 check — the redirect alone never credits.
  POST /payments/zaincash/webhook server-to-server webhook_token JWT.
                                 eventId deduplicates double delivery.
                                 Always returns HTTP 200 (fast).
  GET  /payments/zaincash/cancel  mark a PENDING top-up CANCELED.

Money-safety invariants (see models.TopUp):
  * One TopUp row per provider_ref (UNIQUE).
  * Wallet credited at most once per row: status flip PENDING->COMPLETED
    and the ledger entry happen in ONE transaction under SELECT FOR UPDATE.
  * The ledger reference f"topup:{provider}:{provider_ref}" is checked
    before crediting, so even a logic slip cannot double-credit.
  * IQD->USD uses the admin rate frozen on the TopUp row at creation time.
"""
import logging
import time
import uuid
from decimal import Decimal

from flask import Blueprint, g, redirect, request, url_for, jsonify

from db import get_session
from models import TopUp, Transaction, WebhookEvent, SystemSetting, now
from services import wallet as wallet_svc
from services import auth as auth_svc
from services.limits import limiter

log = logging.getLogger("istore.security")

bp = Blueprint("topup", __name__)


# ---------------------------------------------------------------- helpers

def _setting(key: str, default: str = "") -> str:
    db = get_session()
    try:
        s = db.query(SystemSetting).filter_by(key=key).first()
        return s.value if s and s.value else default
    finally:
        db.close()


def topup_bounds():
    """(min_iqd, max_iqd) from settings, with safe defaults. Public helper."""
    try:
        lo = int(_setting("payment_topup_min_iqd", "5000"))
    except ValueError:
        lo = 5000
    try:
        hi = int(_setting("payment_topup_max_iqd", "1000000"))
    except ValueError:
        hi = 1000000
    return max(1000, lo), max(lo, hi)


def _usd_rate() -> Decimal:
    try:
        return Decimal(_setting("usd_to_iqd", "1500") or "1500")
    except Exception:
        return Decimal("1500")


def _active_zaincash():
    """The configured ZainCash provider, or None (=> 'coming soon')."""
    from services import payments as pay
    p = pay.get_active_provider()
    if p and p.code == "zaincash" and p.is_configured():
        return p
    return None


def _is_paid_status(s) -> bool:
    return str(s or "").strip().lower() in (
        "paid", "success", "successful", "completed", "approved",
        "captured", "settled")


def _extract_amount(data) -> int | None:
    """Pull an IQD integer out of inquiry/JWT payloads of unknown shape."""
    if isinstance(data, dict):
        for k in ("amount", "value", "total", "paid_amount"):
            v = data.get(k)
            if isinstance(v, dict):
                v = v.get("value")
            if v is None:
                continue
            try:
                return int(float(str(v)))
            except (ValueError, TypeError):
                continue
        for k in ("data", "transaction", "result"):
            v = data.get(k)
            if isinstance(v, dict):
                amt = _extract_amount(v)
                if amt is not None:
                    return amt
    return None


def _extract_status(data) -> str:
    if isinstance(data, dict):
        for k in ("status", "transactionStatus", "transaction_status",
                  "paymentStatus", "payment_status"):
            v = data.get(k)
            if v:
                return str(v)
        for k in ("data", "transaction", "result"):
            v = data.get(k)
            if isinstance(v, dict):
                s = _extract_status(v)
                if s:
                    return s
    return ""


def _ledger_ref(topup: TopUp) -> str:
    return f"topup:{topup.provider}:{topup.provider_ref}"


def _credit_topup_idempotent(db, topup: TopUp, provider) -> bool:
    """Credit the wallet exactly once for this top-up.

    MUST be called with the TopUp row locked (SELECT ... FOR UPDATE) inside
    the caller's transaction. Returns True if credited now, False if the
    row was already handled (or money already present).
    """
    if topup.status != "PENDING":
        return False
    ref = _ledger_ref(topup)
    # Belt and suspenders: a deposit with this reference must not exist.
    dup = db.query(Transaction).filter_by(reference=ref, type="deposit").first()
    if dup:
        topup.status = "COMPLETED"
        topup.completed_at = now()
        db.commit()
        log.warning("event=topup_reconciled ref=%s already_credited", ref)
        return False
    rate = topup.exchange_rate or Decimal("1500")
    amount_usd = (Decimal(topup.amount_iqd) / rate).quantize(Decimal("0.01"))
    topup.amount_usd = amount_usd
    wallet_svc.apply_transaction(
        db, topup.user_id, "deposit", amount_usd,
        currency="USD", exchange_rate=rate, reference=ref,
        note=f"Wallet top-up via {provider.display_name}")
    topup.status = "COMPLETED"
    topup.completed_at = now()
    db.commit()
    log.info("event=topup_completed user_id=%s ref=%s iqd=%s usd=%s",
             topup.user_id, ref, topup.amount_iqd, str(amount_usd))
    return True


def _fail_topup(db, topup: TopUp, reason: str):
    topup.status = "FAILED"
    topup.completed_at = now()
    topup.raw_response = dict(topup.raw_response or {}, fail_reason=reason)
    db.commit()
    log.warning("event=topup_failed user_id=%s ref=%s reason=%s",
                topup.user_id, topup.provider_ref, reason[:120])


# ---------------------------------------------------------------- create

@bp.route("/dashboard/wallet/topup", methods=["POST"])
@auth_svc.login_required
@limiter.limit("10 per hour", methods=["POST"])
def wallet_topup():
    """Create a PENDING TopUp + ZainCash invoice, redirect user to pay."""
    provider = _active_zaincash()
    if not provider:
        from flask import abort
        abort(404)
    lo, hi = topup_bounds()
    try:
        amount_iqd = int(request.form.get("amount_iqd", 0))
    except (ValueError, TypeError):
        amount_iqd = 0
    if amount_iqd < lo or amount_iqd > hi:
        return redirect(url_for("user.wallet", topup="invalid_amount"))

    ref = f"istore-{g.user.id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    idem = uuid.uuid4().hex
    rate = _usd_rate()
    db = get_session()
    try:
        topup = TopUp(
            user_id=g.user.id, provider="zaincash",
            provider_ref=ref, idempotency_key=idem,
            amount_iqd=amount_iqd, exchange_rate=rate, status="PENDING")
        db.add(topup)
        db.commit()
        topup_id = topup.id
    finally:
        db.close()

    lang = (getattr(g, "lang", "") or "en")[:2]
    res = provider.create_invoice(
        user_id=g.user.id, amount=amount_iqd, currency="IQD",
        description="i Store wallet top-up",
        reference=ref, order_id=str(topup_id), language=lang,
        return_url=url_for("topup.zaincash_return", _external=True),
        cancel_url=url_for("topup.zaincash_cancel", ref=ref, _external=True),
        webhook_url=url_for("topup.zaincash_webhook", _external=True),
    )
    if not res.get("ok") or not res.get("redirect_url"):
        db = get_session()
        try:
            t = db.query(TopUp).filter_by(id=topup_id).first()
            if t:
                _fail_topup(db, t, res.get("error") or "invoice_failed")
        finally:
            db.close()
        log.warning("event=topup_invoice_failed user_id=%s err=%s",
                    g.user.id, (res.get("error") or "")[:120])
        return redirect(url_for("user.wallet", topup="provider_error"))

    db = get_session()
    try:
        t = db.query(TopUp).filter_by(id=topup_id).first()
        if t:
            t.raw_response = {"init": res.get("raw") or {}}
            db.commit()
    finally:
        db.close()
    return redirect(res["redirect_url"])


# ---------------------------------------------------------------- return

@bp.route("/payments/zaincash/return")
@auth_svc.login_required
def zaincash_return():
    """ZainCash redirects here with ?token=JWT after payment.

    The JWT signature is verified, but the redirect alone NEVER credits —
    inquiry() is the authoritative check.
    """
    provider = _active_zaincash()
    if not provider:
        from flask import abort
        abort(404)
    wallet_url = lambda s: url_for("user.wallet", topup=s)  # noqa: E731
    result = provider.verify_callback(dict(request.args), dict(request.headers))
    ref = result.get("provider_ref") or ""
    if not ref:
        log.warning("event=topup_return_no_ref user_id=%s", g.user.id)
        return redirect(wallet_url("failed"))

    db = get_session()
    try:
        topup = db.query(TopUp).filter_by(provider_ref=ref).with_for_update().first()
        if not topup or topup.user_id != g.user.id:
            return redirect(wallet_url("failed"))
        if topup.status == "COMPLETED":
            return redirect(wallet_url("success"))
        if topup.status != "PENDING":
            return redirect(wallet_url("failed"))

        # Authoritative server-side confirmation. Unknown outcomes leave the
        # row PENDING so the webhook (source of truth) can still complete it.
        inquiry = provider.inquiry(result.get("transaction_id") or "")
        inq_status = _extract_status(inquiry)
        if not _is_paid_status(inq_status):
            if inq_status:
                _fail_topup(db, topup, f"inquiry:{inq_status}")
                return redirect(wallet_url("failed"))
            return redirect(wallet_url("pending"))

        # Amount cross-check when the provider reports one.
        inq_amt = _extract_amount(inquiry) or _extract_amount(result.get("raw"))
        if inq_amt is not None and inq_amt != int(topup.amount_iqd):
            _fail_topup(db, topup,
                        f"amount_mismatch:expected={topup.amount_iqd}:got={inq_amt}")
            log.warning("event=topup_amount_mismatch ref=%s", ref)
            return redirect(wallet_url("failed"))

        topup.raw_response = dict(topup.raw_response or {},
                                  return_verify=result.get("raw") or {},
                                  inquiry=inquiry,
                                  transaction_id=result.get("transaction_id") or "")
        _credit_topup_idempotent(db, topup, provider)
        return redirect(wallet_url("success"))
    finally:
        db.close()


# ---------------------------------------------------------------- webhook

@bp.route("/payments/zaincash/webhook", methods=["POST"])
def zaincash_webhook():
    """Server-to-server webhook. No session/CSRF (signed JWT instead).

    Always returns HTTP 200 quickly. eventId deduplicates double delivery.
    Unknown inquiry outcomes leave the row PENDING so a redelivery retries.
    """
    provider = _active_zaincash()
    if not provider:
        return jsonify({"ok": False, "error": "unknown_provider"}), 404
    payload = request.get_json(silent=True) or {}
    result = provider.verify_webhook(payload, dict(request.headers))
    if not result.get("provider_ref"):
        # Bad signature or malformed payload: acknowledge, do nothing.
        log.warning("event=topup_webhook_rejected err=%s",
                    (result.get("error") or "")[:120])
        return jsonify({"ok": True})

    db = get_session()
    try:
        event_id = result.get("event_id") or ""
        if event_id:
            seen = db.query(WebhookEvent).filter_by(
                provider="zaincash", event_id=event_id).first()
            if seen:
                return jsonify({"ok": True})  # double delivery: no-op

        ref = result["provider_ref"]
        topup = db.query(TopUp).filter_by(provider_ref=ref).with_for_update().first()
        if not topup or topup.status != "PENDING":
            if event_id:
                db.add(WebhookEvent(provider="zaincash", event_id=event_id))
                db.commit()
            return jsonify({"ok": True})

        if not result.get("ok"):
            # Valid signature, terminal non-paid status.
            _fail_topup(db, topup, f"webhook:{result.get('status') or 'not_paid'}")
        else:
            inquiry = provider.inquiry(result.get("transaction_id") or "")
            if _is_paid_status(_extract_status(inquiry)):
                inq_amt = _extract_amount(inquiry)
                if inq_amt is not None and inq_amt != int(topup.amount_iqd):
                    _fail_topup(
                        db, topup,
                        f"amount_mismatch:expected={topup.amount_iqd}:got={inq_amt}")
                else:
                    topup.raw_response = dict(
                        topup.raw_response or {},
                        webhook=result.get("raw") or {}, inquiry=inquiry)
                    _credit_topup_idempotent(db, topup, provider)
            elif _extract_status(inquiry):
                _fail_topup(db, topup,
                            f"webhook_inquiry:{_extract_status(inquiry)}")
            else:
                # Unknown outcome: stay PENDING; do NOT record the event so
                # a redelivery retries the inquiry.
                db.rollback()
                log.warning("event=topup_webhook_unknown ref=%s", ref)
                return jsonify({"ok": True})

        if event_id:
            # Unique constraint makes concurrent double-processing safe:
            # the loser rolls back and its credit attempt is a no-op via
            # the PENDING check + ledger reference check.
            try:
                db.add(WebhookEvent(provider="zaincash", event_id=event_id))
                db.commit()
            except Exception:
                db.rollback()
        return jsonify({"ok": True})
    except Exception:
        db.rollback()
        log.exception("event=topup_webhook_error")
        return jsonify({"ok": True})  # never retry-storm the provider
    finally:
        db.close()


# ---------------------------------------------------------------- cancel

@bp.route("/payments/zaincash/cancel")
@auth_svc.login_required
def zaincash_cancel():
    """User abandoned/cancelled at ZainCash (failureUrl)."""
    ref = (request.args.get("ref") or "").strip()
    db = get_session()
    try:
        if ref:
            topup = db.query(TopUp).filter_by(
                provider_ref=ref, user_id=g.user.id,
                status="PENDING").with_for_update().first()
            if topup:
                topup.status = "CANCELED"
                topup.completed_at = now()
                db.commit()
                log.info("event=topup_canceled user_id=%s ref=%s",
                         g.user.id, ref)
    finally:
        db.close()
    return redirect(url_for("user.wallet", topup="canceled"))
