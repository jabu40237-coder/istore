"""i Store | ئایستۆر — Flask application factory + public routes."""
import os
from decimal import Decimal

from flask import (Flask, render_template, request, redirect, url_for,
                   session, g, abort, jsonify, make_response)

from config import Config
from db import init_db, get_session
from models import Service, Platform, Category, SystemSetting, User, now
import i18n
from services import auth as auth_svc


def create_app():
    app = Flask(__name__,
                template_folder="templates",
                static_folder="static")
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    init_db()
    auth_svc.ensure_roles()
    _seed_defaults()

    # ---------- i18n / request context ----------
    @app.before_request
    def _before():
        lang = request.args.get("lang")
        if lang in i18n.SUPPORTED:
            session["lang"] = lang
        g.lang = session.get("lang") or _user_lang() or "ku"
        g.rtl = i18n.is_rtl(g.lang)
        g.user = auth_svc.current_user()
        # maintenance mode
        if _setting("maintenance_mode") == "1" and not request.path.startswith(("/admin", "/static")):
            return render_template("maintenance.html"), 503

    @app.context_processor
    def _ctx():
        def tr(key, **kw):
            return i18n.t(g.get("lang", "ku"), key, **kw)
        return dict(t=tr, lang=g.get("lang", "ku"),
                    rtl=g.get("rtl", True), user=g.get("user"),
                    fmt_money=fmt_money)

    # ---------- security headers ----------
    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return resp

    # ---------- public pages ----------
    @app.route("/")
    def index():
        db = get_session()
        try:
            stats = {
                "services": db.query(Service).filter_by(status="active").count(),
                "orders": db.query(__import__("models", fromlist=["Order"]).Order)
                              .filter_by(status="COMPLETED").count(),
                "users": db.query(User).count(),
            }
            featured = db.query(Service).filter_by(status="active", is_featured=True).limit(6).all()
            return render_template("index.html", stats=stats, featured=featured)
        finally:
            db.close()

    @app.route("/services")
    def services():
        db = get_session()
        try:
            q = request.args.get("q", "").strip()
            platform = request.args.get("platform", "")
            query = db.query(Service).filter_by(status="active")
            if q:
                like = f"%{q}%"
                query = query.filter(Service.name.ilike(like))
            if platform:
                p = db.query(Platform).filter_by(code=platform).first()
                if p:
                    query = query.filter_by(platform_id=p.id)
            items = query.order_by(Service.sort_order, Service.id).limit(200).all()
            platforms = db.query(Platform).order_by(Platform.sort_order).all()
            return render_template("services.html", services=items,
                                   platforms=platforms, q=q, platform=platform)
        finally:
            db.close()

    @app.route("/service/<int:sid>")
    def service_detail(sid):
        db = get_session()
        try:
            svc = db.query(Service).filter_by(id=sid, status="active").first()
            if not svc:
                abort(404)
            return render_template("service_detail.html", svc=svc)
        finally:
            db.close()

    for page in ["about", "faq", "contact", "terms", "privacy"]:
        def _mk(p):
            def _view():
                return render_template(f"{p}.html")
            _view.__name__ = f"page_{p}"
            return _view
        app.add_url_rule(f"/{page}", f"page_{page}", _mk(page))

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "env": Config.ENV})

    # ---------- auth ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            ip = request.remote_addr or "?"
            if not auth_svc.check_rate_limit(f"login:{ip}", Config.RATELIMIT_LOGIN):
                return render_template("login.html", error="something_wrong"), 429
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            db = get_session()
            try:
                u = db.query(User).filter_by(email=email, is_active=True).first()
                if u and auth_svc.check_password(pw, u.password_hash):
                    auth_svc.login_user(u)
                    nxt = request.args.get("next") or url_for("user.dashboard")
                    return redirect(nxt)
                return render_template("login.html", error="login_failed"), 401
            finally:
                db.close()
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            username = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            pw2 = request.form.get("password2", "")
            name = request.form.get("name", "").strip()
            if not (email and username and pw):
                return render_template("register.html", error="fill_all"), 400
            if pw != pw2:
                return render_template("register.html", error="password_mismatch"), 400
            uid, err = auth_svc.create_user(email, username, pw, name=name)
            if err:
                return render_template("register.html", error=err), 400
            db = get_session()
            try:
                u = db.query(User).filter_by(id=uid).first()
                auth_svc.login_user(u)
            finally:
                db.close()
            return redirect(url_for("user.dashboard"))
        return render_template("register.html")

    @app.route("/logout")
    def logout():
        auth_svc.logout_user()
        return redirect(url_for("index"))

    # blueprints
    from blueprints.user import bp as user_bp
    from blueprints.admin import bp as admin_bp
    from blueprints.api import bp as api_bp
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    return app


def _user_lang():
    return None


def _setting(key: str) -> str:
    db = get_session()
    try:
        s = db.query(SystemSetting).filter_by(key=key).first()
        return s.value if s else ""
    finally:
        db.close()


def _seed_defaults():
    db = get_session()
    try:
        # providers
        from models import Provider
        if not db.query(Provider).filter_by(code="mock").first():
            db.add(Provider(code="mock", name="Mock (dev)"))
        if not db.query(Provider).filter_by(code="kd1s").first():
            db.add(Provider(code="kd1s", name="KD1S", api_url=Config.KD1S_API_URL))
        # platforms
        from services.sync import PLATFORM_NAMES
        for i, (code, name) in enumerate(PLATFORM_NAMES.items()):
            if not db.query(Platform).filter_by(code=code).first():
                db.add(Platform(code=code, name=name, sort_order=i))
        # default pricing rule
        from services.pricing import ensure_default_rules
        ensure_default_rules(db)
        # default settings
        defaults = {
            "site_name": "i Store", "accent_color": "#6c5ce7",
            "usd_to_iqd": str(Config.USD_TO_IQD),
            "maintenance_mode": "0",
        }
        for k, v in defaults.items():
            if not db.query(SystemSetting).filter_by(key=k).first():
                db.add(SystemSetting(key=k, value=v))
        db.commit()
    finally:
        db.close()


def fmt_money(amount, currency="USD") -> str:
    try:
        d = Decimal(str(amount))
    except Exception:
        d = Decimal("0")
    if currency == "IQD":
        return f"{int(d):,} IQD"
    return f"${d:,.2f}"


if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5050, debug=(Config.ENV == "development"))
