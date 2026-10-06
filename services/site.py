"""Shared site helpers: social links, currency, pricing display."""
from decimal import Decimal

from db import get_session
from models import SocialLink, SystemSetting
from config import Config

SOCIAL_PLATFORMS = [
    ("telegram", "Telegram"),
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
    ("facebook", "Facebook"),
    ("youtube", "YouTube"),
    ("x", "X"),
    ("whatsapp", "WhatsApp"),
    ("support", "Support"),
]


def get_social_links():
    """Return enabled social links in sort order. Never hardcoded URLs."""
    db = get_session()
    try:
        links = db.query(SocialLink).filter_by(is_enabled=True).order_by(
            SocialLink.sort_order, SocialLink.id).all()
        return [{"platform": l.platform, "url": l.url} for l in links if l.url]
    finally:
        db.close()


def get_all_social_links():
    """Admin view: all platforms with their config (seeded on first use)."""
    db = get_session()
    try:
        existing = {l.platform: l for l in db.query(SocialLink).all()}
        out = []
        for i, (code, name) in enumerate(SOCIAL_PLATFORMS):
            l = existing.get(code)
            if not l:
                l = SocialLink(platform=code, url="", is_enabled=False,
                              sort_order=i)
                db.add(l)
                db.flush()
            out.append(l)
        db.commit()
        return out
    finally:
        db.close()


def get_rate(currency: str) -> Decimal:
    if (currency or "USD").upper() == "IQD":
        db = get_session()
        try:
            s = db.query(SystemSetting).filter_by(key="usd_to_iqd").first()
            if s and s.value:
                return Decimal(s.value)
        finally:
            db.close()
        return Config.USD_TO_IQD
    return Decimal("1")


def convert_usd(amount_usd, currency: str) -> Decimal:
    return (Decimal(str(amount_usd or 0)) * get_rate(currency)).quantize(
        Decimal("0.01"))


def fmt_price(amount_usd, currency: str = "USD") -> str:
    """Format a USD amount in the user's currency."""
    cur = (currency or "USD").upper()
    if cur == "IQD":
        return f"{int(convert_usd(amount_usd, 'IQD')):,} IQD"
    d = Decimal(str(amount_usd or 0)).quantize(Decimal("0.01"))
    return f"${d:,.2f}"
