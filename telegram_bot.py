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
STR = {
    "ku": {"welcome": "بخێر بهێن بۆ ئایستۆر 🏪\n/order — داواکاریا نوو\n/orders — داواکاریێن من\n/balance — باڵانس\n/services — خزمەتگوزاری\n/help — هاریکاری",
           "balance": "باڵانسا تە: ${b}",
           "no_account": "هەژمار نەهاتە دیتن. لە مالپەڕێ خۆ تۆمار بکە پاش /start بنڤیسە.",
           "help": "/start /services /order /orders /balance /language /help"},
    "ar": {"welcome": "أهلاً بك في آي ستور 🏪",
           "balance": "رصيدك: ${b}",
           "no_account": "لم يتم العثور على حساب. سجّل في الموقع ثم أرسل /start.",
           "help": "/start /services /order /orders /balance /language /help"},
    "en": {"welcome": "Welcome to i Store 🏪",
           "balance": "Your balance: ${b}",
           "no_account": "No account found. Register on the website then send /start.",
           "help": "/start /services /order /orders /balance /language /help"},
}


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
    db = get_session()
    try:
        return db.query(User).filter_by(telegram_id=str(tg_id)).first()
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
    if not Config.TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return
    init_db()
    print("[bot] polling...", flush=True)
    offset = 0
    while True:
        try:
            r = httpx.post(f"{API}/getUpdates",
                           json={"offset": offset, "timeout": 25}, timeout=40)
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    handle(upd["message"])
        except Exception as e:
            print("[bot] poll error:", str(e)[:120])
            time.sleep(5)


if __name__ == "__main__":
    main()
