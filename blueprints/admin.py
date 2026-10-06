"""Admin panel blueprint: /admin/* — RBAC protected."""
from decimal import Decimal
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, g, abort, jsonify
from sqlalchemy import func

from db import get_session
from models import (User, Order, Service, Provider, Transaction, AuditLog,
                    SyncLog, SystemSetting, PricingRule, Platform, Category,
                    Notification, Ticket, now)
from services import auth as auth_svc
from services import wallet as wallet_svc
from services.sync import sync_provider
from providers.kd1s import get_provider
from config import Config

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _audit(action, target="", meta=None):
    db = get_session()
    try:
        db.add(AuditLog(actor_id=g.user.id if g.user else None, action=action,
                        target=target, meta=meta or {},
                        ip=request.remote_addr or "",
                        user_agent=request.headers.get("User-Agent", "")[:500]))
        db.commit()
    finally:
        db.close()


@bp.route("/")
@auth_svc.admin_required
def dashboard():
    db = get_session()
    try:
        revenue = db.query(func.coalesce(func.sum(Order.customer_charge_usd), 0)).scalar()
        costs = db.query(func.coalesce(func.sum(Order.provider_cost_usd), 0)).scalar()
        profit = db.query(func.coalesce(func.sum(Order.profit_usd), 0)).scalar()
        stats = {
            "users": db.query(User).count(),
            "orders": db.query(Order).count(),
            "revenue": revenue, "costs": costs, "profit": profit,
            "pending": db.query(Order).filter(Order.status.in_(["PENDING", "PROCESSING"])).count(),
            "failed": db.query(Order).filter_by(status="FAILED").count(),
            "services": db.query(Service).filter_by(status="active").count(),
        }
        # last 14 days orders (bucketed in Python — portable across SQLite/PostgreSQL)
        since = datetime.utcnow() - timedelta(days=14)
        rows = db.query(Order.created_at).filter(Order.created_at >= since).all()
        buckets = {}
        for (dt,) in rows:
            if dt:
                day = dt.strftime("%Y-%m-%d")
                buckets[day] = buckets.get(day, 0) + 1
        daily = [{"d": d, "c": buckets[d]} for d in sorted(buckets)]
        providers = db.query(Provider).all()
        return render_template("admin/index.html", stats=stats,
                               daily=daily,
                               providers=providers)
    finally:
        db.close()


# ---------- services ----------
@bp.route("/services")
@auth_svc.admin_required
def services():
    db = get_session()
    try:
        q = request.args.get("q", "").strip()
        query = db.query(Service)
        if q:
            query = query.filter(Service.name.ilike(f"%{q}%"))
        items = query.order_by(Service.id).limit(200).all()
        return render_template("admin/services.html", services=items, q=q)
    finally:
        db.close()


@bp.route("/services/<int:sid>", methods=["GET", "POST"])
@auth_svc.admin_required
def service_edit(sid):
    db = get_session()
    try:
        svc = db.query(Service).filter_by(id=sid).first()
        if not svc:
            abort(404)
        if request.method == "POST":
            old_price = str(svc.selling_price_usd)
            custom = request.form.get("custom_price_usd", "").strip()
            svc.custom_price_usd = Decimal(custom) if custom else None
            svc.is_featured = bool(request.form.get("is_featured"))
            svc.status = request.form.get("status", svc.status)
            svc.sort_order = int(request.form.get("sort_order", 0) or 0)
            from services.pricing import compute_selling_price
            svc.selling_price_usd = (svc.custom_price_usd
                                     if svc.custom_price_usd is not None
                                     else compute_selling_price(db, svc))
            db.commit()
            _audit("service_price_change", f"service:{sid}",
                   {"old": old_price, "new": str(svc.selling_price_usd)})
            return redirect(url_for("admin.services"))
        return render_template("admin/service_edit.html", svc=svc)
    finally:
        db.close()


@bp.route("/services/sync", methods=["POST"])
@auth_svc.admin_required
def services_sync():
    db = get_session()
    try:
        prov = db.query(Provider).filter_by(code=Config.PROVIDER_MODE).first()
        if not prov:
            abort(400)
        from providers.kd1s import get_provider as gp
        if Config.PROVIDER_MODE == "kd1s":
            key = Config.KD1S_API_KEY or _sys(db, "kd1s_api_key")
            provider = gp("kd1s", Config.KD1S_API_URL, key)
        else:
            provider = gp("mock")
        stats = sync_provider(provider, prov)
        _audit("service_sync", f"provider:{prov.code}", stats)
        return redirect(url_for("admin.services"))
    finally:
        db.close()


# ---------- orders ----------
@bp.route("/orders")
@auth_svc.admin_required
def orders():
    db = get_session()
    try:
        status = request.args.get("status", "")
        q = db.query(Order)
        if status:
            q = q.filter_by(status=status)
        items = q.order_by(Order.created_at.desc()).limit(200).all()
        return render_template("admin/orders.html", orders=items, status=status)
    finally:
        db.close()


@bp.route("/orders/<int:oid>/refund", methods=["POST"])
@auth_svc.admin_required
def order_refund(oid):
    db = get_session()
    try:
        o = db.query(Order).filter_by(id=oid).first()
        if not o or o.status == "REFUNDED":
            abort(400)
        wallet_svc.apply_transaction(
            db, o.user_id, "refund", o.customer_charge_usd,
            currency=o.currency, exchange_rate=o.exchange_rate,
            reference=f"order:{o.id}:admin_refund",
            note="Admin refund", created_by=g.user.id)
        o.status = "REFUNDED"
        db.commit()
        _audit("order_refund", f"order:{oid}", {"amount": str(o.customer_charge_usd)})
        return redirect(url_for("admin.orders"))
    finally:
        db.close()


# ---------- users ----------
@bp.route("/users")
@auth_svc.admin_required
def users():
    db = get_session()
    try:
        items = db.query(User).order_by(User.created_at.desc()).limit(200).all()
        return render_template("admin/users.html", users=items)
    finally:
        db.close()


@bp.route("/users/<int:uid>/adjust", methods=["POST"])
@auth_svc.admin_required
def user_adjust(uid):
    db = get_session()
    try:
        amount = Decimal(request.form.get("amount", "0"))
        reason = request.form.get("reason", "").strip() or "admin adjustment"
        if amount == 0:
            abort(400)
        wallet_svc.apply_transaction(db, uid, "manual_adjustment", amount,
                                     reference=f"admin:{g.user.id}",
                                     note=reason, created_by=g.user.id)
        db.commit()
        _audit("wallet_adjust", f"user:{uid}", {"amount": str(amount), "reason": reason})
        return redirect(url_for("admin.users"))
    finally:
        db.close()


# ---------- providers ----------
@bp.route("/providers")
@auth_svc.admin_required
def providers():
    db = get_session()
    try:
        items = db.query(Provider).all()
        return render_template("admin/providers.html", providers=items)
    finally:
        db.close()


@bp.route("/providers/<int:pid>/test", methods=["POST"])
@auth_svc.admin_required
def provider_test(pid):
    db = get_session()
    try:
        prov = db.query(Provider).filter_by(id=pid).first()
        if not prov:
            abort(404)
        if prov.code == "kd1s":
            key = Config.KD1S_API_KEY or _sys(db, "kd1s_api_key")
            p = get_provider("kd1s", Config.KD1S_API_URL, key)
        else:
            p = get_provider("mock")
        ok, latency, err = p.health_check()
        from models import ProviderHealthLog
        db.add(ProviderHealthLog(provider_id=prov.id, ok=ok,
                                 latency_ms=latency, error=err))
        db.commit()
        return jsonify({"ok": ok, "latency_ms": latency, "error": err})
    finally:
        db.close()


@bp.route("/providers/<int:pid>/balance")
@auth_svc.admin_required
def provider_balance(pid):
    db = get_session()
    try:
        prov = db.query(Provider).filter_by(id=pid).first()
        if not prov:
            abort(404)
        if prov.code == "kd1s":
            key = Config.KD1S_API_KEY or _sys(db, "kd1s_api_key")
            p = get_provider("kd1s", Config.KD1S_API_URL, key)
        else:
            p = get_provider("mock")
        return jsonify({"balance": str(p.get_balance())})
    finally:
        db.close()


# ---------- settings ----------
@bp.route("/settings", methods=["GET", "POST"])
@auth_svc.admin_required
def settings():
    db = get_session()
    try:
        keys = ["site_name", "accent_color", "usd_to_iqd", "maintenance_mode",
                "kd1s_api_url", "sync_interval_minutes"]
        if request.method == "POST":
            for k in keys:
                v = request.form.get(k)
                if v is None:
                    continue
                s = db.query(SystemSetting).filter_by(key=k).first()
                if not s:
                    s = SystemSetting(key=k)
                    db.add(s)
                s.value = v.strip()
            # secret: only update when provided (never displayed back)
            secret = request.form.get("kd1s_api_key", "").strip()
            if secret:
                s = db.query(SystemSetting).filter_by(key="kd1s_api_key").first()
                if not s:
                    s = SystemSetting(key="kd1s_api_key", is_secret=True)
                    db.add(s)
                s.value = secret
            db.commit()
            _audit("settings_change", "settings", {"keys": keys})
            return redirect(url_for("admin.settings"))
        vals = {k: _sys(db, k) for k in keys}
        vals["kd1s_api_key_set"] = bool(Config.KD1S_API_KEY or _sys(db, "kd1s_api_key"))
        logs = db.query(SyncLog).order_by(SyncLog.started_at.desc()).limit(10).all()
        audit = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(30).all()
        return render_template("admin/settings.html", vals=vals, logs=logs, audit=audit)
    finally:
        db.close()


@bp.route("/pricing", methods=["GET", "POST"])
@auth_svc.admin_required
def pricing():
    db = get_session()
    try:
        if request.method == "POST":
            scope = request.form.get("scope", "global")
            pct = Decimal(request.form.get("markup_percent", "0") or "0")
            r = db.query(PricingRule).filter_by(scope=scope, scope_id=None).first()
            if not r:
                r = PricingRule(scope=scope)
                db.add(r)
            r.markup_percent = pct
            r.is_active = True
            db.commit()
            _audit("pricing_change", scope, {"markup_percent": str(pct)})
            # recompute all selling prices
            from services.pricing import compute_selling_price
            for svc in db.query(Service).filter(Service.custom_price_usd.is_(None)).all():
                svc.selling_price_usd = compute_selling_price(db, svc)
            db.commit()
            return redirect(url_for("admin.pricing"))
        rules = db.query(PricingRule).all()
        return render_template("admin/pricing.html", rules=rules)
    finally:
        db.close()


def _sys(db, key: str) -> str:
    s = db.query(SystemSetting).filter_by(key=key).first()
    return s.value if s else ""
