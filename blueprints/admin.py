"""Admin panel blueprint: /admin/* — RBAC protected."""
import os
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


def _client_ip() -> str:
    """Real client IP. X-Forwarded-For is honored ONLY when TRUST_PROXY=1,
    otherwise request.remote_addr is used (prevents header spoofing)."""
    if os.environ.get("TRUST_PROXY") == "1":
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or ""


@bp.before_request
def _enforce_admin_ip_allowlist():
    """Ali-only network gate for /admin.

    Source of truth: ADMIN_IP_ALLOWLIST env var (comma-separated, empty=allow).
    If the env var is UNSET, falls back to the `admin_ip_allowlist` system
    setting. Returns 404 (not 403) to avoid confirming the path exists.
    X-Forwarded-For is honored ONLY when TRUST_PROXY=1."""
    raw = os.environ.get("ADMIN_IP_ALLOWLIST")
    if raw is None:
        db = get_session()
        try:
            s = db.query(SystemSetting).filter_by(key="admin_ip_allowlist").first()
            raw = s.value if s and s.value else ""
        finally:
            db.close()
    raw = (raw or "").strip()
    if not raw:
        return None
    allowed = {x.strip() for x in raw.split(",") if x.strip()}
    if _client_ip() not in allowed:
        abort(404)
    return None


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
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
def users():
    db = get_session()
    try:
        q = request.args.get("q", "").strip()
        query = db.query(User)
        if q:
            like = f"%{q}%"
            query = query.filter((User.email.ilike(like)) | (User.username.ilike(like)))
        items = query.order_by(User.created_at.desc()).limit(200).all()
        return render_template("admin/users.html", users=items, q=q)
    finally:
        db.close()


@bp.route("/users/<int:uid>/adjust", methods=["POST"])
@auth_svc.super_admin_required
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


@bp.route("/users/<int:uid>/suspend", methods=["POST"])
@auth_svc.super_admin_required
def user_suspend(uid):
    db = get_session()
    try:
        u = db.query(User).filter_by(id=uid).first()
        if not u:
            abort(404)
        if u.id == g.user.id or any(r.name in ("SUPER_ADMIN", "ADMIN") for r in u.roles):
            abort(403)
        u.is_active = False
        db.commit()
        _audit("user_suspend", f"user:{uid}", {})
        return redirect(url_for("admin.users"))
    finally:
        db.close()


@bp.route("/users/<int:uid>/activate", methods=["POST"])
@auth_svc.super_admin_required
def user_activate(uid):
    db = get_session()
    try:
        u = db.query(User).filter_by(id=uid).first()
        if not u:
            abort(404)
        u.is_active = True
        db.commit()
        _audit("user_activate", f"user:{uid}", {})
        return redirect(url_for("admin.users"))
    finally:
        db.close()


@bp.route("/tickets")
@auth_svc.super_admin_required
def tickets():
    from models import Ticket
    db = get_session()
    try:
        status = request.args.get("status", "")
        q = db.query(Ticket)
        if status:
            q = q.filter_by(status=status)
        items = q.order_by(Ticket.updated_at.desc()).limit(200).all()
        uids = {t.user_id for t in items}
        users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
        return render_template("admin/tickets.html", tickets=items, users=users,
                               status=status)
    finally:
        db.close()


@bp.route("/tickets/<int:tid>", methods=["GET", "POST"])
@auth_svc.super_admin_required
def ticket_detail(tid):
    from models import Ticket, TicketMessage
    db = get_session()
    try:
        t = db.query(Ticket).filter_by(id=tid).first()
        if not t:
            abort(404)
        if request.method == "POST":
            body = request.form.get("message", "").strip()
            action = request.form.get("action", "reply")
            if action == "close":
                t.status = "CLOSED"
            elif action == "reopen":
                t.status = "OPEN"
            elif body:
                db.add(TicketMessage(ticket_id=t.id, user_id=g.user.id,
                                     body=body, is_staff=True))
                t.status = "WAITING_USER"
            db.commit()
            _audit("ticket_reply", f"ticket:{tid}", {"action": action})
            if action not in ("close", "reopen") and body:
                from models import Notification
                db.add(Notification(user_id=t.user_id, type="SUPPORT",
                                    title="support_reply",
                                    body=f"Support replied to ticket #{t.id}"))
                db.commit()
                try:
                    from telegram_bot import notify_user as _tg
                    _tg(t.user_id, f"Support replied to your ticket #{t.id}")
                except Exception:
                    pass
            return redirect(url_for("admin.ticket_detail", tid=tid))
        msgs = db.query(TicketMessage).filter_by(ticket_id=t.id).order_by(
            TicketMessage.created_at).all()
        u = db.query(User).filter_by(id=t.user_id).first()
        return render_template("admin/ticket_detail.html", ticket=t, msgs=msgs, u=u)
    finally:
        db.close()


# ---------- providers ----------
@bp.route("/providers")
@auth_svc.super_admin_required
def providers():
    db = get_session()
    try:
        items = db.query(Provider).all()
        return render_template("admin/providers.html", providers=items)
    finally:
        db.close()


@bp.route("/providers/<int:pid>/test", methods=["POST"])
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
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
@auth_svc.super_admin_required
def settings():
    db = get_session()
    try:
        keys = ["site_name", "accent_color", "usd_to_iqd", "maintenance_mode",
                "kd1s_api_url", "sync_interval_minutes", "admin_ip_allowlist"]
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
            # secret: encrypted at rest via services.secrets (never displayed back)
            if secret:
                from services.secrets import set_secret
                set_secret(db, "kd1s_api_key", secret)
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


@bp.route("/security")
@auth_svc.super_admin_required
def security():
    """2FA (TOTP) management for the Ali-only admin account."""
    from services import totp as totp_svc
    enabled = totp_svc.is_enabled(g.user.id)
    remaining = totp_svc.backup_codes_remaining(g.user.id) if enabled else 0
    return render_template("admin/security.html", totp_enabled=enabled,
                           codes_remaining=remaining)


@bp.route("/security/totp/begin", methods=["POST"])
@auth_svc.super_admin_required
def totp_begin():
    from services import totp as totp_svc
    secret, uri, codes = totp_svc.begin_enrollment(g.user.id)
    _audit("totp_enroll_begin", f"user:{g.user.id}", {})
    return render_template("admin/security.html", totp_enabled=False,
                           codes_remaining=0, enroll_secret=secret,
                           enroll_uri=uri, enroll_qr=totp_svc.qr_data_uri(uri),
                           backup_codes=codes)


@bp.route("/security/totp/confirm", methods=["POST"])
@auth_svc.super_admin_required
def totp_confirm():
    from services import totp as totp_svc
    code = request.form.get("code", "")
    if totp_svc.confirm_enrollment(g.user.id, code):
        _audit("totp_enabled", f"user:{g.user.id}", {})
        return redirect(url_for("admin.security"))
    return render_template("admin/security.html", totp_enabled=False,
                           codes_remaining=0, enroll_error=True)


@bp.route("/security/totp/disable", methods=["POST"])
@auth_svc.super_admin_required
def totp_disable():
    from services import totp as totp_svc
    code = request.form.get("code", "")
    # require a valid current code before disabling
    if totp_svc.verify_code(g.user.id, code):
        totp_svc.disable(g.user.id)
        _audit("totp_disabled", f"user:{g.user.id}", {})
    return redirect(url_for("admin.security"))


@bp.route("/payments", methods=["GET", "POST"])
@auth_svc.super_admin_required
def payments():
    """Payment provider configuration. Credentials are encrypted at rest.
    Nothing is active until a provider is selected AND its credentials saved."""
    from services import payments as pay
    db = get_session()
    try:
        if request.method == "POST":
            code = request.form.get("provider", "none")
            if code not in ("none", "zaincash", "fastpay"):
                code = "none"
            pay.write_setting(db, "payment_provider", code)
            pay.write_setting(db, "payment_test_mode",
                              "0" if request.form.get("test_mode") != "1" else "1")
            fields = {
                "zaincash": ["client_id", "client_secret", "api_key", "prod_host"],
                "fastpay": ["store_id", "store_password", "refund_secret",
                            "stage_host", "prod_host"],
            }
            for f in fields.get(code, []):
                v = (request.form.get(f"payment_{code}_{f}") or "").strip()
                if v:  # only overwrite when provided (never echo back)
                    pay.write_setting(db, f"payment_{code}_{f}", v, secret=True)
            db.commit()
            _audit("payment_settings", f"provider:{code}", {})
            return redirect(url_for("admin.payments"))
        vals = {"provider": pay.read_setting(db, "payment_provider") or "none",
                "test_mode": pay.read_setting(db, "payment_test_mode") or "1"}
        configured = {}
        for code in ("zaincash", "fastpay"):
            p = pay.get_provider(code)
            configured[code] = bool(p and p.is_configured())
        from models import PaymentInvoice
        invoices = db.query(PaymentInvoice).order_by(
            PaymentInvoice.created_at.desc()).limit(20).all()
        return render_template("admin/payments.html", vals=vals,
                               configured=configured, invoices=invoices)
    finally:
        db.close()


@bp.route("/pricing", methods=["GET", "POST"])
@auth_svc.super_admin_required
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
    # secret keys go through the encrypted store (Fernet at rest);
    # get_secret migrates legacy plaintext to ciphertext on read.
    if key == "kd1s_api_key" or key.startswith("payment_"):
        try:
            from services.secrets import get_secret
            return get_secret(db, key, "")
        except Exception:
            pass
    s = db.query(SystemSetting).filter_by(key=key).first()
    return s.value if s else ""


@bp.route("/social", methods=["GET", "POST"])
@auth_svc.super_admin_required
def social():
    from models import SocialLink
    from services import site as site_svc
    db = get_session()
    try:
        links = site_svc.get_all_social_links()
        if request.method == "POST":
            for l in links:
                url = request.form.get(f"url_{l.platform}", "").strip()
                l.url = url
                l.is_enabled = bool(request.form.get(f"en_{l.platform}")) and bool(url)
            db.commit()
            _audit("social_change", "social_links", {})
            return redirect(url_for("admin.social"))
        return render_template("admin/social.html", links=links,
                               names=dict(site_svc.SOCIAL_PLATFORMS))
    finally:
        db.close()
