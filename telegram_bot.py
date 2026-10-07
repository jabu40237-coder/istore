"""i Store Telegram bot (long polling, no extra deps beyond httpx).

Commands: /start /services /order /orders /balance /support /language /help
Admin: /admin /stats (Telegram user ID mapped to admin role, server-side).

Needs TELEGRAM_BOT_TOKEN env. Run: ./venv/bin/python telegram_bot.py
"""
import time
from decimal import Decimal

import httpx

from config import Config
from db import get_session, init_db
from models import User, Service, Order, Notification, now
from services import wallet as wallet_svc

API = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}"


def set_api_base(url: str):
    """Override the Telegram API base (e.g. vault surrogate URL)."""
    global API
    API = url.rstrip("/")


def _api_configured() -> bool:
    """True when the API base carries a usable token/surrogate.
    Checks the resolved base (not just the env var) so vault-surrogate
    mode works."""
    base = (API or "").rstrip("/")
    return bool(base) and not base.endswith("/bot")
STR = {
    "ku": {"welcome": "بخێر بهێن بۆ ئایستۆر 🏪\n/order — داواکاریا نوو\n/orders — داواکاریێن من\n/balance — باڵانس\n/services — خزمەتگوزاری\n/support — پشتیڤانی\n/language — زمان\n/help — هاریکاری",
           "balance": "باڵانسا تە: ${b}",
           "no_account": "هەژمار نەهاتە دیتن.\nل مالپەڕێ ب ئەکاونتا خۆ بچە ژوور: داشبۆرد ← پروفایل ← گرێدانا تێلێگرامێ، پاش /start بنڤیسە.",
           "help": "/start /services /order /orders /balance /support /language /help",
           "order_hint": "بۆ داواکاریێ سەرەدانا مالپەڕێ بکە:\n${url}/dashboard/order",
           "support_hint": "بۆ پشتیڤانیێ سەرەدانا مالپەڕێ بکە:\n${url}/dashboard/support",
           "lang_set": "زمان هاتە گوهارتن.",
           "lang_ask": "زمانێ خۆ هەلبژێرە: ku / ar / en",
           "no_orders": "هێشتا داواکاری نینن."},
    "ar": {"welcome": "أهلاً بك في آي ستور 🏪\n/order — طلب جديد\n/orders — طلباتي\n/balance — الرصيد\n/services — الخدمات\n/support — الدعم\n/language — اللغة\n/help — مساعدة",
           "balance": "رصيدك: ${b}",
           "no_account": "لم يتم العثور على حساب.\nادخل لموقعك: لوحة التحكم ← الملف الشخصي ← ربط تليجرام، ثم أرسل /start.",
           "help": "/start /services /order /orders /balance /support /language /help",
           "order_hint": "للطلب زر الموقع:\n${url}/dashboard/order",
           "support_hint": "للدعم زر الموقع:\n${url}/dashboard/support",
           "lang_set": "تم تغيير اللغة.",
           "lang_ask": "اختر لغتك: ku / ar / en",
           "no_orders": "لا توجد طلبات بعد."},
    "en": {"welcome": "Welcome to i Store 🏪\n/order — new order\n/orders — my orders\n/balance — balance\n/services — services\n/support — support\n/language — language\n/help — help",
           "balance": "Your balance: ${b}",
           "no_account": "No account found.\nLog in on the website: Dashboard → Profile → Link Telegram, then send /start.",
           "help": "/start /services /order /orders /balance /support /language /help",
           "order_hint": "To order, visit:\n${url}/dashboard/order",
           "support_hint": "For support, visit:\n${url}/dashboard/support",
           "lang_set": "Language changed.",
           "lang_ask": "Choose your language: ku / ar / en",
           "no_orders": "No orders yet."},
}

SITE_URL = ""  # optional: set SITE_URL env to advertise the web URL in bot hints


def notify_telegram(tg_id: str, text: str) -> bool:
    """Send a notification to a linked Telegram user. Returns success."""
    if not _api_configured() or not tg_id:
        return False
    try:
        r = httpx.post(f"{API}/sendMessage",
                       json={"chat_id": int(tg_id), "text": text}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def notify_user(user_id: int, text: str) -> bool:
    """Notify a user via Telegram if they linked it and enabled notifications."""
    db = get_session()
    try:
        from models import NotificationPreference
        u = db.query(User).filter_by(id=user_id).first()
        if not u or not u.telegram_id:
            return False
        pref = db.query(NotificationPreference).filter_by(user_id=user_id).first()
        if pref and not pref.telegram_enabled:
            return False
        tg_id = u.telegram_id
    finally:
        db.close()
    return notify_telegram(tg_id, text)


def _t(lang, key, **kw):
    s = STR.get(lang, STR["ku"]).get(key, key)
    for k, v in kw.items():
        s = s.replace("${" + k + "}", str(v))
    return s


def api(method, **params):
    r = httpx.post(f"{API}/{method}", json=params, timeout=30)
    return r.json()


def send(chat_id, text):
    try:
        api("sendMessage", chat_id=chat_id, text=text)
    except Exception as e:
        print("send failed:", e)


def find_user(tg_id):
    from sqlalchemy.orm import joinedload
    db = get_session()
    try:
        # joinedload: handle() reads user.roles after the session closes
        return db.query(User).options(joinedload(User.roles)).filter_by(
            telegram_id=str(tg_id)).first()
    finally:
        db.close()


def handle(msg):
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    tg_id = msg["from"]["id"]
    user = find_user(tg_id)
    lang = (user.language if user else "ku") if user else "ku"

    if text.startswith("/start"):
        if not user:
            send(chat_id, _t(lang, "no_account"))
        else:
            send(chat_id, _t(lang, "welcome"))
    elif text.startswith("/balance"):
        if not user:
            send(chat_id, _t(lang, "no_account")); return
        db = get_session()
        try:
            b = wallet_svc.get_balance_usd(db, user.id)
        finally:
            db.close()
        send(chat_id, _t(lang, "balance", b=b))
    elif text.startswith("/order"):
        if not user:
            send(chat_id, _t(lang, "no_account")); return
        send(chat_id, _t(lang, "order_hint", url=SITE_URL or ""))
    elif text.startswith("/support"):
        if not user:
            send(chat_id, _t(lang, "no_account")); return
        send(chat_id, _t(lang, "support_hint", url=SITE_URL or ""))
    elif text.startswith("/language"):
        parts = text.split()
        if len(parts) > 1 and parts[1] in ("ku", "ar", "en") and user:
            db = get_session()
            try:
                u = db.query(User).filter_by(id=user.id).first()
                u.language = parts[1]
                db.commit()
                lang = parts[1]
            finally:
                db.close()
            send(chat_id, _t(lang, "lang_set"))
        else:
            send(chat_id, _t(lang, "lang_ask"))
    elif text.startswith("/link"):
        # /link <code> — code shown on website profile page
        parts = text.split()
        if len(parts) > 1:
            from services import auth as auth_svc
            uid = auth_svc.verify_telegram_code(parts[1].strip())
            if uid:
                db = get_session()
                try:
                    u = db.query(User).filter_by(id=uid).first()
                    if u:
                        u.telegram_id = str(tg_id)
                        db.commit()
                        user = u
                        lang = u.language or "ku"
                finally:
                    db.close()
                send(chat_id, _t(lang, "welcome"))
            else:
                send(chat_id, _t(lang, "no_account"))
        else:
            send(chat_id, _t(lang, "no_account"))
    elif text.startswith("/orders"):
        if not user:
            send(chat_id, _t(lang, "no_account")); return
        db = get_session()
        try:
            orders = db.query(Order).filter_by(user_id=user.id).order_by(
                Order.created_at.desc()).limit(5).all()
            if not orders:
                send(chat_id, "—"); return
            lines = [f"#{o.id} · {o.status} · ${o.customer_charge_usd}" for o in orders]
            send(chat_id, "\n".join(lines))
        finally:
            db.close()
    elif text.startswith("/services"):
        db = get_session()
        try:
            svcs = db.query(Service).filter_by(status="active").limit(10).all()
            lines = [f"• {s.name} — ${s.selling_price_usd}/1k" for s in svcs]
            send(chat_id, "\n".join(lines) or "—")
        finally:
            db.close()
    elif text.startswith("/admin") or text.startswith("/stats"):
        if not user or not any(r.name in ("SUPER_ADMIN", "ADMIN") for r in user.roles):
            return
        db = get_session()
        try:
            from sqlalchemy import func
            revenue = db.query(func.coalesce(func.sum(Order.customer_charge_usd), 0)).scalar()
            n_orders = db.query(Order).count()
            n_users = db.query(User).count()
            send(chat_id, f"📊 orders={n_orders} users={n_users} revenue=${revenue}")
        finally:
            db.close()
    else:
        send(chat_id, _t(lang, "help"))


def main():
    if not _api_configured():
        print("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return
    init_db()
    print("[bot] polling...", flush=True)
    offset = 0
    failures = 0
    while True:
        try:
            r = httpx.post(f"{API}/getUpdates",
                           json={"offset": offset, "timeout": 25}, timeout=40)
            data = r.json()
            if not data.get("ok"):
                # 409 = another instance polling or webhook set: exit loudly
                if data.get("error_code") == 409:
                    print("[bot] 409 conflict: another instance is polling or a "
                          "webhook is set. Run deleteWebhook and keep a single "
                          "instance.", flush=True)
                    raise SystemExit(1)
                failures += 1
                wait = min(60, 2 ** failures)
                print(f"[bot] getUpdates not ok, backing off {wait}s", flush=True)
                time.sleep(wait)
                continue
            failures = 0
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    handle(upd["message"])
        except SystemExit:
            raise
        except Exception as e:
            failures += 1
            wait = min(60, 2 ** failures)
            print("[bot] poll error:", str(e)[:120], f"— retry in {wait}s")
            time.sleep(wait)


if __name__ == "__main__":
    main()
