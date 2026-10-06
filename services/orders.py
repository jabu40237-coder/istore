"""Order engine with safe money state machine:

  Wallet Reserved -> Provider Request -> Provider Order Created -> Finalize

If the provider request fails, the reservation is released (refund tx).
Idempotency keys prevent double orders on retry/refresh.
"""
import hashlib
import time
from decimal import Decimal

from db import get_session
from models import Order, OrderEvent, Service, User, now
from services import wallet as wallet_svc
from providers.base import BaseProvider


def _idem_key(user_id: int, service_id: int, link: str, quantity: int,
              input_data: dict) -> str:
    raw = f"{user_id}|{service_id}|{link}|{quantity}|{sorted((input_data or {}).items())}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _provider_cost(service: Service, quantity: int) -> Decimal:
    return ((service.provider_rate or Decimal("0")) * Decimal(quantity) / Decimal(1000)
            ).quantize(Decimal("0.000001"))


def _customer_charge(service: Service, quantity: int) -> Decimal:
    return ((service.selling_price_usd or Decimal("0")) * Decimal(quantity) / Decimal(1000)
            ).quantize(Decimal("0.000001"))


def _log_event(db, order: Order, event: str, old: str = "", new: str = "", meta=None):
    db.add(OrderEvent(order_id=order.id, event=event, old_status=old,
                      new_status=new, meta=meta or {}))


def validate_order_input(service: Service, link: str, quantity: int,
                         input_data: dict) -> str:
    """Returns error key or '' if valid. Schema-driven: checks required fields."""
    from services import forms as form_svc
    stype = (service.service_type or "Default").lower()
    data = input_data or {}
    if "subscri" in stype:
        # subscriptions: quantity is derived from posts, not the quantity field
        try:
            posts = int(data.get("posts", 0))
        except (ValueError, TypeError):
            posts = 0
        if posts <= 0:
            return "invalid_quantity"
    elif quantity < service.min_quantity or quantity > service.max_quantity:
        return "invalid_quantity"
    schema = form_svc.get_form_schema(service)
    for f in schema:
        if not f["required"]:
            continue
        p = f["param"]
        if p == "link":
            # subscriptions use username instead of a link
            if "subscri" in stype:
                continue
            if not link or not link.startswith("http"):
                return "invalid_link"
        elif p == "quantity":
            continue  # already checked against min/max
        elif not str(data.get(p, "")).strip():
            return "missing_field"
    return ""


def create_order(user_id: int, service_id: int, link: str, quantity: int,
                input_data: dict, provider: BaseProvider,
                currency: str = "USD", exchange_rate: Decimal = Decimal("1"),
                idempotency_key: str = "") -> dict:
    """Returns {'ok': True, 'order_id': int} or {'ok': False, 'error': key}."""
    db = get_session()
    try:
        service = db.query(Service).filter_by(id=service_id, status="active").first()
        if not service:
            return {"ok": False, "error": "service_unavailable"}
        user = db.query(User).filter_by(id=user_id, is_active=True).first()
        if not user:
            return {"ok": False, "error": "something_wrong"}

        err = validate_order_input(service, link, quantity, input_data or {})
        if err:
            return {"ok": False, "error": err}

        if not idempotency_key:
            idempotency_key = _idem_key(user_id, service_id, link, quantity, input_data or {})

        # duplicate protection
        existing = db.query(Order).filter_by(idempotency_key=idempotency_key).first()
        if existing:
            return {"ok": True, "order_id": existing.id, "duplicate": True}

        # subscriptions: effective quantity comes from posts count
        stype = (service.service_type or "").lower()
        eff_quantity = quantity
        if "subscri" in stype:
            try:
                eff_quantity = int((input_data or {}).get("posts", 0))
            except (ValueError, TypeError):
                eff_quantity = 0

        charge = _customer_charge(service, eff_quantity)
        cost = _provider_cost(service, eff_quantity)
        profit = charge - cost        # 1) reserve wallet
        try:
            wallet_svc.apply_transaction(
                db, user_id, "order_charge", -charge, currency=currency,
                exchange_rate=exchange_rate, reference=f"order:{idempotency_key}",
                note=f"Order charge — {service.name[:80]}")
        except ValueError:
            db.rollback()
            return {"ok": False, "error": "insufficient_balance"}

        order = Order(
            user_id=user_id, service_id=service_id, provider_id=service.provider_id,
            idempotency_key=idempotency_key, quantity=eff_quantity, link=link,
            input_data=input_data or {}, provider_cost_usd=cost,
            customer_charge_usd=charge, profit_usd=profit, currency=currency,
            exchange_rate=exchange_rate, status="PENDING",
        )
        db.add(order)
        db.flush()
        _log_event(db, order, "created", "", "PENDING")
        db.commit()  # reservation committed; provider call happens outside tx

        # 2) provider request
        extra = dict(input_data or {})
        result = provider.create_order(service.provider_service_id, link=link,
                                       quantity=eff_quantity, extra=extra)

        db2 = get_session()
        try:
            o = db2.query(Order).filter_by(id=order.id).first()
            if result.ok and result.provider_order_id:
                o.provider_order_id = result.provider_order_id
                old = o.status
                o.status = "PROCESSING"
                _log_event(db2, o, "provider_accepted", old, "PROCESSING",
                           {"provider_order_id": result.provider_order_id})
                db2.commit()
                _notify(db2, user_id, "ORDER", "order_created",
                        f"Order #{o.id} — {service.name[:60]}")
                return {"ok": True, "order_id": o.id}
            else:
                # 3) release reservation safely
                wallet_svc.apply_transaction(
                    db2, user_id, "refund", charge, currency=currency,
                    exchange_rate=exchange_rate, reference=f"order:{o.id}",
                    note="Auto-refund: provider rejected the order")
                old = o.status
                o.status = "FAILED"
                _log_event(db2, o, "provider_failed", old, "FAILED",
                           {"error": result.error_code})
                db2.commit()
                _notify(db2, user_id, "ERROR", "order_failed",
                        f"Order #{o.id} failed: {result.error_code}")
                return {"ok": False, "error": "order_failed",
                        "provider_error": result.error_code}
        finally:
            db2.close()
    finally:
        db.close()


def _notify(db, user_id: int, ntype: str, title: str, body: str):
    from models import Notification
    db.add(Notification(user_id=user_id, type=ntype, title=title, body=body))
    db.commit()
    # push to Telegram if linked (best-effort, never blocks the order flow)
    try:
        from telegram_bot import notify_user as _tg
        _tg(user_id, body)
    except Exception:
        pass


STATUS_MAP = {
    "pending": "PENDING", "processing": "PROCESSING", "in progress": "PROCESSING",
    "completed": "COMPLETED", "partial": "PARTIAL", "canceled": "CANCELED",
    "cancelled": "CANCELED", "refunded": "REFUNDED",
}


def normalize_status(raw: str) -> str:
    return STATUS_MAP.get((raw or "").strip().lower(), "PROCESSING")


def sync_order_statuses(provider: BaseProvider, limit: int = 100) -> dict:
    """Batch-poll active orders. Returns counts."""
    db = get_session()
    counts = {"checked": 0, "updated": 0}
    try:
        active = db.query(Order).filter(
            Order.status.in_(["PENDING", "PROCESSING", "PARTIAL"]),
            Order.provider_order_id != "").limit(limit).all()
        if not active:
            return counts
        by_provider = {}
        for o in active:
            by_provider.setdefault(o.provider_order_id, o)
        results = provider.get_multiple_statuses(list(by_provider.keys()))
        for poid, st in results.items():
            o = by_provider.get(poid)
            if not o or st.error_code:
                continue
            counts["checked"] += 1
            new_status = normalize_status(st.status)
            if new_status != o.status:
                old = o.status
                o.status = new_status
                o.start_count = st.start_count or o.start_count
                o.remains = st.remains or o.remains
                if new_status in ("COMPLETED", "PARTIAL", "CANCELED"):
                    o.completed_at = now()
                _log_event(db, o, "status_sync", old, new_status,
                           {"start_count": o.start_count, "remains": o.remains})
                counts["updated"] += 1
                if new_status == "PARTIAL":
                    _handle_partial(db, o)
                _notify(db, o.user_id, "ORDER", f"st_{new_status.lower()}",
                        f"Order #{o.id} — {new_status}")
        db.commit()
    finally:
        db.close()
    return counts


def _handle_partial(db, order: Order):
    """Refund the unfulfilled portion on partial completion."""
    try:
        remains = int(order.remains or 0)
    except ValueError:
        return
    if remains <= 0 or order.quantity <= 0:
        return
    per_unit = order.customer_charge_usd / Decimal(order.quantity)
    refund_amount = (per_unit * Decimal(remains)).quantize(Decimal("0.000001"))
    if refund_amount > 0:
        wallet_svc.apply_transaction(
            db, order.user_id, "refund", refund_amount, currency=order.currency,
            exchange_rate=order.exchange_rate, reference=f"order:{order.id}:partial",
            note=f"Partial refund — {remains} units unfulfilled")
        order.status = "REFUNDED"
        _log_event(db, order, "partial_refund", "PARTIAL", "REFUNDED",
                   {"refunded_usd": str(refund_amount)})
