"""User dashboard blueprint: /dashboard/*"""
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, g, abort

from db import get_session
from models import Order, Service, Transaction, Notification, Ticket, TicketMessage, User, now
from services import auth as auth_svc
from services import wallet as wallet_svc
from services import orders as order_svc
from providers.kd1s import get_provider
from config import Config

bp = Blueprint("user", __name__, url_prefix="/dashboard")


def _provider():
    db = get_session()
    try:
        from models import Provider, SystemSetting
        if Config.PROVIDER_MODE == "kd1s":
            key = Config.KD1S_API_KEY
            if not key:
                s = db.query(SystemSetting).filter_by(key="kd1s_api_key").first()
                key = s.value if s else ""
            url = Config.KD1S_API_URL
            s2 = db.query(SystemSetting).filter_by(key="kd1s_api_url").first()
            if s2 and s2.value:
                url = s2.value
            return get_provider("kd1s", url, key)
        return get_provider("mock")
    finally:
        db.close()


@bp.route("/")
@auth_svc.login_required
def dashboard():
    db = get_session()
    try:
        uid = g.user.id
        balance = wallet_svc.get_balance_usd(db, uid)
        total = db.query(Order).filter_by(user_id=uid).count()
        completed = db.query(Order).filter_by(user_id=uid, status="COMPLETED").count()
        processing = db.query(Order).filter(Order.user_id == uid,
                                            Order.status.in_(["PENDING", "PROCESSING"])).count()
        recent = db.query(Order).filter_by(user_id=uid).order_by(
            Order.created_at.desc()).limit(5).all()
        unread = db.query(Notification).filter_by(user_id=uid, is_read=False).count()
        svc_ids = {o.service_id for o in recent}
        svc_names = {s.id: s.name for s in db.query(Service).filter(
            Service.id.in_(svc_ids)).all()} if svc_ids else {}
        return render_template("dashboard/index.html", balance=balance, total=total,
                               completed=completed, processing=processing,
                               recent=recent, unread=unread, svc_names=svc_names)
    finally:
        db.close()


@bp.route("/order", methods=["GET", "POST"])
@auth_svc.login_required
def new_order():
    db = get_session()
    try:
        sid = request.args.get("service") or request.form.get("service_id")
        svc = db.query(Service).filter_by(id=sid, status="active").first() if sid else None
        if request.method == "POST" and svc:
            link = request.form.get("link", "").strip()
            try:
                qty = int(request.form.get("quantity", 0))
            except ValueError:
                qty = 0
            input_data = {k: v for k, v in request.form.items()
                          if k.startswith("x_")}
            # CSRF
            if request.form.get("csrf") != __import__("flask").session.get("csrf"):
                abort(400)
            cur = g.user.currency or "USD"
            rate = _rate(cur)
            res = order_svc.create_order(g.user.id, svc.id, link, qty, input_data,
                                         _provider(), currency=cur, exchange_rate=rate)
            if res["ok"]:
                return redirect(url_for("user.order_detail", oid=res["order_id"]))
            return render_template("dashboard/order.html", svc=svc, error=res["error"],
                                   link=link, qty=qty)
        services = db.query(Service).filter_by(status="active").order_by(
            Service.sort_order).limit(300).all()
        return render_template("dashboard/order.html", svc=svc, services=services)
    finally:
        db.close()


@bp.route("/orders")
@auth_svc.login_required
def orders():
    db = get_session()
    try:
        status = request.args.get("status", "")
        q = db.query(Order).filter_by(user_id=g.user.id)
        if status:
            q = q.filter_by(status=status)
        items = q.order_by(Order.created_at.desc()).limit(100).all()
        svc_ids = {o.service_id for o in items}
        svc_names = {s.id: s.name for s in db.query(Service).filter(
            Service.id.in_(svc_ids)).all()} if svc_ids else {}
        return render_template("dashboard/orders.html", orders=items, status=status,
                               svc_names=svc_names)
    finally:
        db.close()


@bp.route("/orders/<int:oid>")
@auth_svc.login_required
def order_detail(oid):
    db = get_session()
    try:
        o = db.query(Order).filter_by(id=oid, user_id=g.user.id).first()
        if not o:
            abort(404)
        svc = db.query(Service).filter_by(id=o.service_id).first()
        return render_template("dashboard/order_detail.html", order=o, svc=svc)
    finally:
        db.close()


@bp.route("/orders/<int:oid>/refill", methods=["POST"])
@auth_svc.login_required
def refill(oid):
    db = get_session()
    try:
        o = db.query(Order).filter_by(id=oid, user_id=g.user.id).first()
        if not o:
            abort(404)
        svc = db.query(Service).filter_by(id=o.service_id).first()
        if not svc or not svc.supports_refill or not o.provider_order_id:
            abort(400)
        res = _provider().create_refill(o.provider_order_id)
        if res.ok:
            from models import Refill
            db.add(Refill(order_id=o.id, provider_refill_id=res.provider_order_id,
                          status="PENDING"))
            db.commit()
        return redirect(url_for("user.order_detail", oid=oid))
    finally:
        db.close()


@bp.route("/orders/<int:oid>/cancel", methods=["POST"])
@auth_svc.login_required
def cancel(oid):
    db = get_session()
    try:
        o = db.query(Order).filter_by(id=oid, user_id=g.user.id).first()
        if not o:
            abort(404)
        svc = db.query(Service).filter_by(id=o.service_id).first()
        if not svc or not svc.supports_cancel or not o.provider_order_id:
            abort(400)
        _provider().create_cancel([o.provider_order_id])
        o.status = "CANCELED"
        # refund remaining value conservatively: full charge minus nothing?
        # Provider cancel may be partial; we refund full charge and let
        # reconciliation handle differences (safe default: customer-friendly).
        wallet_svc.apply_transaction(
            db, g.user.id, "refund", o.customer_charge_usd,
            currency=o.currency, exchange_rate=o.exchange_rate,
            reference=f"order:{o.id}:cancel", note="Refund: order canceled")
        db.commit()
        return redirect(url_for("user.order_detail", oid=oid))
    finally:
        db.close()


@bp.route("/wallet")
@auth_svc.login_required
def wallet():
    db = get_session()
    try:
        from models import Wallet
        w = db.query(Wallet).filter_by(user_id=g.user.id).first()
        txs = db.query(Transaction).filter_by(wallet_id=w.id).order_by(
            Transaction.created_at.desc()).limit(50).all() if w else []
        return render_template("dashboard/wallet.html",
                               balance=w.balance_usd if w else Decimal("0"), txs=txs)
    finally:
        db.close()


@bp.route("/notifications")
@auth_svc.login_required
def notifications():
    db = get_session()
    try:
        items = db.query(Notification).filter_by(user_id=g.user.id).order_by(
            Notification.created_at.desc()).limit(50).all()
        return render_template("dashboard/notifications.html", items=items)
    finally:
        db.close()


@bp.route("/notifications/read", methods=["POST"])
@auth_svc.login_required
def notif_read():
    db = get_session()
    try:
        db.query(Notification).filter_by(user_id=g.user.id).update({"is_read": True})
        db.commit()
        return redirect(url_for("user.notifications"))
    finally:
        db.close()


@bp.route("/support", methods=["GET", "POST"])
@auth_svc.login_required
def support():
    db = get_session()
    try:
        if request.method == "POST":
            subject = request.form.get("subject", "").strip()
            body = request.form.get("message", "").strip()
            if subject and body:
                t = Ticket(user_id=g.user.id, subject=subject,
                           category=request.form.get("category", "general"),
                           priority=request.form.get("priority", "normal"))
                db.add(t)
                db.flush()
                db.add(TicketMessage(ticket_id=t.id, user_id=g.user.id, body=body))
                db.commit()
                return redirect(url_for("user.support"))
        tickets = db.query(Ticket).filter_by(user_id=g.user.id).order_by(
            Ticket.created_at.desc()).all()
        return render_template("dashboard/support.html", tickets=tickets)
    finally:
        db.close()


@bp.route("/support/<int:tid>", methods=["GET", "POST"])
@auth_svc.login_required
def ticket_detail(tid):
    db = get_session()
    try:
        t = db.query(Ticket).filter_by(id=tid, user_id=g.user.id).first()
        if not t:
            abort(404)
        if request.method == "POST":
            body = request.form.get("message", "").strip()
            if body and t.status != "CLOSED":
                db.add(TicketMessage(ticket_id=t.id, user_id=g.user.id, body=body))
                t.status = "WAITING_SUPPORT"
                db.commit()
                return redirect(url_for("user.ticket_detail", tid=tid))
        msgs = db.query(TicketMessage).filter_by(ticket_id=t.id).order_by(
            TicketMessage.created_at).all()
        return render_template("dashboard/ticket.html", ticket=t, msgs=msgs)
    finally:
        db.close()


@bp.route("/profile", methods=["GET", "POST"])
@auth_svc.login_required
def profile():
    db = get_session()
    try:
        u = db.query(User).filter_by(id=g.user.id).first()
        if request.method == "POST":
            u.name = request.form.get("name", "").strip()
            u.phone = request.form.get("phone", "").strip()
            lang = request.form.get("language", "")
            if lang in ("ku", "ar", "en"):
                u.language = lang
                from flask import session as fsession
                fsession["lang"] = lang
            cur = request.form.get("currency", "")
            if cur in ("USD", "IQD"):
                u.currency = cur
            db.commit()
            return redirect(url_for("user.profile"))
        return render_template("dashboard/profile.html", u=u)
    finally:
        db.close()


def _rate(currency: str) -> Decimal:
    if currency == "IQD":
        db = get_session()
        try:
            from models import SystemSetting
            s = db.query(SystemSetting).filter_by(key="usd_to_iqd").first()
            return Decimal(s.value) if s and s.value else Decimal("1500")
        finally:
            db.close()
    return Decimal("1")
