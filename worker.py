"""i Store background worker. Run as a separate process:

    ./venv/bin/python worker.py

Jobs: service_sync, order_status_sync, provider_health.
Uses the jobs table as a lightweight queue + interval scheduling.
"""
import time
import traceback
from datetime import datetime, timedelta

from config import Config
from db import get_session, init_db
from models import Job, Provider, ProviderHealthLog, SystemSetting, now


def _setting(key: str, default: str) -> str:
    db = get_session()
    try:
        s = db.query(SystemSetting).filter_by(key=key).first()
        return s.value if s and s.value else default
    finally:
        db.close()


def _get_provider_for(provider_row):
    from providers.kd1s import get_provider
    if provider_row.code == "kd1s":
        key = Config.KD1S_API_KEY or _setting("kd1s_api_key", "")
        url = _setting("kd1s_api_url", Config.KD1S_API_URL)
        return get_provider("kd1s", url, key)
    return get_provider("mock")


def job_service_sync():
    from services.sync import sync_provider
    db = get_session()
    try:
        prov = db.query(Provider).filter_by(code=Config.PROVIDER_MODE).first()
        if not prov or not prov.is_active:
            return "no active provider"
        stats = sync_provider(_get_provider_for(prov), prov)
        return f"sync done: {stats}"
    finally:
        db.close()


def job_order_status_sync():
    from services.orders import sync_order_statuses
    db = get_session()
    try:
        prov = db.query(Provider).filter_by(code=Config.PROVIDER_MODE).first()
        if not prov:
            return "no provider"
        counts = sync_order_statuses(_get_provider_for(prov))
        return f"status sync: {counts}"
    finally:
        db.close()


def job_provider_health():
    db = get_session()
    try:
        out = []
        for prov in db.query(Provider).filter_by(is_active=True).all():
            ok, latency, err = _get_provider_for(prov).health_check()
            db.add(ProviderHealthLog(provider_id=prov.id, ok=ok,
                                     latency_ms=latency, error=err))
            out.append(f"{prov.code}: {'OK' if ok else 'FAIL'}")
        db.commit()
        return "; ".join(out)
    finally:
        db.close()


SCHEDULE = [
    ("service_sync", job_service_sync, lambda: int(_setting(
        "sync_interval_minutes", str(Config.SYNC_INTERVAL_MINUTES)))),
    ("order_status_sync", job_order_status_sync,
     lambda: Config.ORDER_POLL_INTERVAL_MINUTES),
    ("provider_health", job_provider_health, lambda: 15),
]

_last_run = {}


def main():
    init_db()
    from services.auth import ensure_roles
    ensure_roles()
    print("[worker] started", flush=True)
    while True:
        t = datetime.utcnow()
        for name, fn, interval_fn in SCHEDULE:
            interval = interval_fn()
            last = _last_run.get(name)
            if last and (t - last) < timedelta(minutes=interval):
                continue
            _last_run[name] = t
            try:
                res = fn()
                print(f"[worker] {name}: {res}", flush=True)
            except Exception as e:
                print(f"[worker] {name} FAILED: {e}", flush=True)
                traceback.print_exc()
        time.sleep(30)


if __name__ == "__main__":
    main()
