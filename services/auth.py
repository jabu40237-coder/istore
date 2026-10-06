"""Authentication helpers: bcrypt hashing, sessions, RBAC, rate limiting."""
import secrets
import time
from functools import wraps

import bcrypt
from flask import session, request, redirect, url_for, abort

from db import get_session
from models import User, Role, UserRole, AuditLog

_login_attempts = {}  # ip -> [timestamps]


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def login_user(user: User):
    session.clear()
    session["user_id"] = user.id
    session["csrf"] = secrets.token_hex(16)
    db = get_session()
    try:
        u = db.query(User).filter_by(id=user.id).first()
        from models import now
        u.last_login_at = now()
        db.add(AuditLog(actor_id=user.id, action="user_login",
                        ip=request.remote_addr or "",
                        user_agent=request.headers.get("User-Agent", "")[:500]))
        db.commit()
    finally:
        db.close()


def logout_user():
    session.clear()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_session()
    try:
        u = db.query(User).filter_by(id=uid, is_active=True).first()
        if u:
            roles = [r.name for r in u.roles]  # load BEFORE expunge
            db.expunge(u)
            u._role_names = roles
        return u
    finally:
        db.close()


def user_roles(user) -> list:
    return getattr(user, "_role_names", [])


def has_role(user, *roles) -> bool:
    if not user:
        return False
    return any(r in user_roles(user) for r in roles)


def is_admin(user) -> bool:
    return has_role(user, "SUPER_ADMIN", "ADMIN")


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or not is_admin(u):
            abort(403)
        return f(*a, **kw)
    return wrapper


def check_rate_limit(key: str, max_attempts: int, window_s: int = 900) -> bool:
    """Returns True if allowed, False if rate-limited."""
    t = time.time()
    arr = [x for x in _login_attempts.get(key, []) if t - x < window_s]
    if len(arr) >= max_attempts:
        return False
    arr.append(t)
    _login_attempts[key] = arr
    return True


def ensure_roles():
    db = get_session()
    try:
        for r in ["SUPER_ADMIN", "ADMIN", "SUPPORT", "CUSTOMER"]:
            if not db.query(Role).filter_by(name=r).first():
                db.add(Role(name=r))
        db.commit()
    finally:
        db.close()


def create_user(email, username, password, name="", phone="", roles=("CUSTOMER",)):
    db = get_session()
    try:
        if db.query(User).filter_by(email=email).first():
            return None, "email_exists"
        if db.query(User).filter_by(username=username).first():
            return None, "username_exists"
        u = User(email=email, username=username, name=name, phone=phone,
                 password_hash=hash_password(password))
        db.add(u)
        db.flush()
        for rn in roles:
            r = db.query(Role).filter_by(name=rn).first()
            if r:
                db.add(UserRole(user_id=u.id, role_id=r.id))
        from services import wallet as wallet_svc
        wallet_svc.get_wallet(db, u.id)
        db.commit()
        uid = u.id
        return uid, ""
    finally:
        db.close()
