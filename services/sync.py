"""Service sync engine: provider -> database.

- New services -> added
- Changed services -> updated (rate/min/max/type/refill/cancel)
- Missing services -> status=provider_unavailable (never deleted)
- History preserved; every run logged to sync_logs.
"""
import time
from decimal import Decimal

from db import get_session
from models import Provider, Service, Platform, Category, SyncLog, now
from providers.base import BaseProvider


def _platform_code(name: str, category: str) -> str:
    blob = f"{name} {category}".lower()
    for code in ["instagram", "tiktok", "youtube", "telegram", "facebook", "twitter", "x "]:
        if code.strip() in blob:
            return "twitter" if code == "x " else code
    if "x/" in blob or blob.startswith("x "):
        return "x"
    return "other"


PLATFORM_NAMES = {
    "instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube",
    "telegram": "Telegram", "facebook": "Facebook", "twitter": "Twitter",
    "x": "X", "other": "Other",
}


def _get_or_create_platform(db, code: str) -> Platform:
    p = db.query(Platform).filter_by(code=code).first()
    if not p:
        p = Platform(code=code, name=PLATFORM_NAMES.get(code, code.title()))
        db.add(p)
        db.flush()
    return p


def _get_or_create_category(db, platform_id: int, name: str) -> Category:
    name = name or "General"
    c = db.query(Category).filter_by(platform_id=platform_id, name=name).first()
    if not c:
        c = Category(platform_id=platform_id, name=name)
        db.add(c)
        db.flush()
    return c


def sync_provider(provider: BaseProvider, provider_row: Provider) -> dict:
    db = get_session()
    t0 = time.time()
    log = SyncLog(provider_id=provider_row.id)
    db.add(log)
    db.commit()

    stats = {"found": 0, "added": 0, "updated": 0, "disabled": 0, "errors": 0}
    try:
        remote = provider.get_services()
        stats["found"] = len(remote)
        seen = set()

        for rs in remote:
            seen.add((provider_row.id, rs.service_id))
            try:
                plat = _get_or_create_platform(db, _platform_code(rs.name, rs.category))
                cat_name = rs.category.split("/")[-1].strip() if "/" in rs.category else (rs.category or "General")
                cat = _get_or_create_category(db, plat.id, cat_name)

                svc = db.query(Service).filter_by(
                    provider_id=provider_row.id,
                    provider_service_id=rs.service_id).first()
                if not svc:
                    svc = Service(provider_id=provider_row.id,
                                  provider_service_id=rs.service_id)
                    db.add(svc)
                    stats["added"] += 1
                else:
                    changed = (
                        svc.name != rs.name or svc.provider_rate != rs.rate
                        or svc.min_quantity != rs.min or svc.max_quantity != rs.max
                        or svc.service_type != rs.service_type
                        or svc.supports_refill != rs.refill or svc.supports_cancel != rs.cancel
                    )
                    if changed:
                        stats["updated"] += 1

                svc.name = rs.name
                svc.service_type = rs.service_type
                svc.provider_rate = rs.rate
                svc.min_quantity = rs.min
                svc.max_quantity = rs.max
                svc.supports_refill = rs.refill
                svc.supports_cancel = rs.cancel
                svc.platform_id = plat.id
                svc.category_id = cat.id
                if svc.status == "provider_unavailable":
                    svc.status = "active"  # came back
                svc.last_synced_at = now()

                # selling price from pricing engine (only if no custom price)
                from services.pricing import compute_selling_price
                if svc.custom_price_usd is None:
                    svc.selling_price_usd = compute_selling_price(db, svc)
                else:
                    svc.selling_price_usd = svc.custom_price_usd
                db.flush()
            except Exception:
                stats["errors"] += 1
                db.rollback()

        # disable missing
        for svc in db.query(Service).filter_by(provider_id=provider_row.id,
                                               status="active").all():
            if (svc.provider_id, svc.provider_service_id) not in seen:
                svc.status = "provider_unavailable"
                stats["disabled"] += 1

        provider_row.last_sync_at = now()
        provider_row.last_error = ""
        db.commit()
    except Exception as e:
        stats["errors"] += 1
        provider_row.last_error = str(e)[:500]
        db.commit()

    log.finished_at = now()
    log.found, log.added, log.updated = stats["found"], stats["added"], stats["updated"]
    log.disabled, log.errors = stats["disabled"], stats["errors"]
    log.duration_s = Decimal(str(round(time.time() - t0, 2)))
    db.commit()
    db.close()
    return stats
