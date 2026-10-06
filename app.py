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
    if Config.ENV == "production":
        app.config["SESSION_COOKIE_SECURE"] = True

    init_db()
    auth_svc.ensure_roles()
    _seed_defaults()
    _bootstrap_admin()

    # ---------- i18n / request context ----------
    @app.before_request
    def _before():
        lang = request.args.get("lang")
        if lang in i18n.SUPPORTED:
            session["lang"] = lang
        g.lang = session.get("lang") or _user_lang() or "ku"
        g.rtl = i18n.is_rtl(g.lang)
        g.user = auth_svc.current_user()
        # ensure a CSRF token exists for every session (guests included)
        if not session.get("csrf"):
            import secrets
            session["csrf"] = secrets.token_hex(16)
        # global CSRF protection for all POST (except session-less auth forms)
        if request.method == "POST" and request.endpoint not in (
                "login", "register", "health"):
            token = request.form.get("csrf", "")
            if not token or token != session.get("csrf"):
                abort(400)
        # maintenance mode
        if _setting("maintenance_mode") == "1" and not request.path.startswith(("/admin", "/static")):
            return render_template("maintenance.html"), 503

    @app.context_processor
    def _ctx():
        from services import site as site_svc

        def tr(key, **kw):
            return i18n.t(g.get("lang", "ku"), key, **kw)
        cur = ((g.get("user").currency if g.get("user") else None)
               or session.get("currency") or Config.DEFAULT_CURRENCY)
        return dict(t=tr, lang=g.get("lang", "ku"),
                    rtl=g.get("rtl", True), user=g.get("user"),
                    fmt_money=fmt_money,
                    fmt_price=lambda a, c=None: site_svc.fmt_price(a, c or cur),
                    currency=cur, rate=site_svc.get_rate(cur),
                    social_links=site_svc.get_social_links())

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
            category = request.args.get("category", "")
            sort = request.args.get("sort", "recommended")
            f_refill = request.args.get("refill", "")
            f_cancel = request.args.get("cancel", "")
            query = db.query(Service).filter_by(status="active")
            if q:
                like = f"%{q}%"
                query = query.filter(
                    (Service.name.ilike(like)) | (Service.description.ilike(like)))
            if platform:
                p = db.query(Platform).filter_by(code=platform).first()
                if p:
                    query = query.filter_by(platform_id=p.id)
            if category:
                c = db.query(Category).filter_by(id=int(category)).first() \
                    if category.isdigit() else None
                if c:
                    query = query.filter_by(category_id=c.id)
            if f_refill == "1":
                query = query.filter_by(supports_refill=True)
            if f_cancel == "1":
                query = query.filter_by(supports_cancel=True)
            if sort == "price_asc":
                query = query.order_by(Service.selling_price_usd.asc())
            elif sort == "price_desc":
                query = query.order_by(Service.selling_price_usd.desc())
            elif sort == "newest":
                query = query.order_by(Service.created_at.desc())
            else:  # recommended
                query = query.order_by(Service.is_featured.desc(),
                                      Service.sort_order, Service.id)
            items = query.limit(200).all()
            platforms = db.query(Platform).order_by(Platform.sort_order).all()
            categories = db.query(Category).filter_by(is_hidden=False).order_by(
                Category.sort_order).all()
            fav_ids = set()
            if g.user:
                from models import Favorite
                fav_ids = {f.service_id for f in db.query(Favorite).filter_by(
                    user_id=g.user.id).all()}
            return render_template("services.html", services=items,
                                   platforms=platforms, categories=categories,
                                   q=q, platform=platform, category=category,
                                   sort=sort, f_refill=f_refill,
                                   f_cancel=f_cancel, fav_ids=fav_ids)
        finally:
            db.close()

    @app.route("/service/<int:sid>")
    def service_detail(sid):
        db = get_session()
        try:
            from services import forms as form_svc
            from models import Favorite, RecentlyViewed
            svc = db.query(Service).filter_by(id=sid, status="active").first()
            if not svc:
                abort(404)
            platform = db.query(Platform).filter_by(id=svc.platform_id).first() \
                if svc.platform_id else None
            category = db.query(Category).filter_by(id=svc.category_id).first() \
                if svc.category_id else None
            schema = form_svc.get_form_schema(svc)
            is_fav = False
            if g.user:
                is_fav = db.query(Favorite).filter_by(
                    user_id=g.user.id, service_id=svc.id).first() is not None
                # track recently viewed (upsert)
                rv = db.query(RecentlyViewed).filter_by(
                    user_id=g.user.id, service_id=svc.id).first()
                if rv:
                    rv.viewed_at = now()
                else:
                    db.add(RecentlyViewed(user_id=g.user.id, service_id=svc.id))
                db.commit()
            return render_template("service_detail.html", svc=svc,
                                   platform=platform, category=category,
                                   schema=schema, is_fav=is_fav)
        finally:
            db.close()

    @app.route("/favorite/<int:sid>", methods=["POST"])
    def favorite_toggle(sid):
        if not g.user:
            abort(401)
        db = get_session()
        try:
            from models import Favorite
            f = db.query(Favorite).filter_by(user_id=g.user.id,
                                             service_id=sid).first()
            if f:
                db.delete(f)
                on = False
            else:
                db.add(Favorite(user_id=g.user.id, service_id=sid))
                on = True
            db.commit()
            return jsonify({"ok": True, "favorite": on})
        finally:
            db.close()

    @app.route("/favorites")
    def favorites():
        if not g.user:
            return redirect(url_for("login", next="/favorites"))
        db = get_session()
        try:
            from models import Favorite
            ids = [f.service_id for f in db.query(Favorite).filter_by(
                user_id=g.user.id).order_by(Favorite.created_at.desc()).all()]
            items = db.query(Service).filter(
                Service.id.in_(ids), Service.status == "active").all() if ids else []
            order = {sid: i for i, sid in enumerate(ids)}
            items.sort(key=lambda s: order.get(s.id, 0))
            return render_template("services.html", services=items,
                                   platforms=[], categories=[], q="",
                                   platform="", category="", sort="",
                                   f_refill="", f_cancel="",
                                   fav_ids=set(ids), favorites_page=True)
        finally:
            db.close()

    @app.route("/compare")
    def compare():
        db = get_session()
        try:
            ids = [int(x) for x in request.args.get("ids", "").split(",")
                   if x.strip().isdigit()][:3]
            items = db.query(Service).filter(
                Service.id.in_(ids), Service.status == "active").all() if ids else []
            return render_template("compare.html", services=items)
        finally:
            db.close()

    @app.route("/set-currency", methods=["POST"])
    def set_currency():
        cur = request.form.get("currency", "USD").upper()
        if cur not in ("USD", "IQD"):
            cur = "USD"
        if g.user:
            db = get_session()
            try:
                u = db.query(User).filter_by(id=g.user.id).first()
                if u:
                    u.currency = cur
                    db.commit()
            finally:
                db.close()
        else:
            session["currency"] = cur
        return redirect(request.referrer or url_for("index"))

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


def _bootstrap_admin():
    """First-run admin bootstrap for hosted deploys (no shell access).

    If ADMIN_EMAIL + ADMIN_PASSWORD env vars are set and no SUPER_ADMIN
    exists yet, create/promote that user. Safe to run on every boot.
    """
    import os
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    from models import User, Role, UserRole
    db = get_session()
    try:
        admin_role = db.query(Role).filter_by(name="SUPER_ADMIN").first()
        if admin_role and db.query(UserRole).filter_by(role_id=admin_role.id).first():
            return  # an admin already exists — do nothing
        username = email.split("@")[0]
        uid, err = auth_svc.create_user(email, username, password, name="Admin",
                                       roles=("SUPER_ADMIN", "ADMIN"))
        if err == "email_exists":
            u = db.query(User).filter_by(email=email).first()
            for rn in ("SUPER_ADMIN", "ADMIN"):
                r = db.query(Role).filter_by(name=rn).first()
                if r and not db.query(UserRole).filter_by(
                        user_id=u.id, role_id=r.id).first():
                    db.add(UserRole(user_id=u.id, role_id=r.id))
            db.commit()
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
        # default settings — atomic under multiple workers (INSERT OR IGNORE)
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        is_pg = db.bind.dialect.name == "postgresql"
        for k, v in defaults.items():
            stmt = (pg_insert if is_pg else sqlite_insert)(SystemSetting).values(
                key=k, value=v)
            stmt = stmt.on_conflict_do_nothing(index_elements=["key"])
            db.execute(stmt)
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
