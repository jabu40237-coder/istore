"""ZainCash wallet top-up tests. NO real network — httpx and provider HTTP
are mocked; JWTs are signed locally with a test API key.

Run:  python3 test_topup.py
"""
import os
import sys
import time
import uuid
from decimal import Decimal
from unittest import mock

# ---- test-only env, BEFORE any app import -------------------------------
_TEST_DB = "/tmp/test_topup.db"
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ["DB_ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["APP_ENV"] = "development"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jwt  # noqa: E402

from db import init_db, get_session  # noqa: E402
from models import TopUp, Transaction, WebhookEvent, User  # noqa: E402
from services import auth as auth_svc  # noqa: E402
from services import wallet as wallet_svc  # noqa: E402
from services import payments as pay  # noqa: E402
from services.payments.zaincash import ZainCashProvider  # noqa: E402

API_KEY = "test-api-key-123"
passed = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    passed.append(name)
    print(f"  ok: {name}")


def signed(payload: dict, key: str = API_KEY) -> str:
    return jwt.encode(payload, key, algorithm="HS256")


def seed():
    init_db()
    auth_svc.ensure_roles()
    db = get_session()
    pay.write_setting(db, "payment_provider", "zaincash")
    pay.write_setting(db, "payment_test_mode", "1")
    pay.write_setting(db, "payment_zaincash_client_id", "cid", secret=True)
    pay.write_setting(db, "payment_zaincash_client_secret", "csec", secret=True)
    pay.write_setting(db, "payment_zaincash_api_key", API_KEY, secret=True)
    pay.write_setting(db, "usd_to_iqd", "1500")
    db.commit()
    db.close()
    uid, err = auth_svc.create_user(
        "topup@test.tld", "topupuser", "pass1234", name="Topup Test")
    assert not err, err
    return uid


def make_topup(db, uid, amount_iqd=10000):
    ref = f"istore-{uid}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    t = TopUp(user_id=uid, provider="zaincash", provider_ref=ref,
              idempotency_key=uuid.uuid4().hex, amount_iqd=amount_iqd,
              exchange_rate=Decimal("1500"), status="PENDING")
    db.add(t)
    db.commit()
    return t, ref


def balance_usd(db, uid):
    return wallet_svc.get_balance_usd(db, uid)


print("== 1. IQD-only enforced at the provider ==")
p = ZainCashProvider({"client_id": "x", "client_secret": "y", "api_key": API_KEY},
                     test_mode=True)
res = p.create_invoice(user_id=1, amount=100, currency="USD",
                       description="t", return_url="http://x", webhook_url="http://y")
check("USD invoice rejected", not res.get("ok"))
check("IQD-only error code", res.get("error") == "zaincash_iqd_only")

print("== 2. Bad JWT signatures rejected ==")
bad = signed({"externalReferenceId": "r1", "status": "paid"}, key="WRONG-KEY")
r = p.verify_callback({"token": bad}, {})
check("return: bad signature -> ok=False", not r.get("ok"))
check("return: bad signature error mentions signature",
      "bad_signature" in (r.get("error") or ""))
r = p.verify_callback({}, {})
check("return: missing token -> ok=False", not r.get("ok"))

r = p.verify_webhook({"webhook_token": bad}, {})
check("webhook: bad signature -> ok=False", not r.get("ok"))
check("webhook: bad signature -> no provider_ref", not r.get("provider_ref"))

good_return = signed({"externalReferenceId": "r2", "status": "paid",
                      "transactionId": "tx-1"})
r = p.verify_callback({"token": good_return}, {})
check("return: valid signature + paid claim -> ok=True", r.get("ok"))
check("return: provider_ref extracted", r.get("provider_ref") == "r2")

print("== 3. HTTP flow tests (Flask test client) ==")
uid = seed()
from app import create_app  # noqa: E402
app = create_app()
client = app.test_client()

# log the test user in
with client.session_transaction() as sess:
    sess["user_id"] = uid
    sess["csrf"] = "csrf-token-for-tests"

db = get_session()
t, ref = make_topup(db, uid, amount_iqd=15000)
db.close()

print("== 3a. return-url WITHOUT inquiry confirmation does NOT credit ==")
tok = signed({"externalReferenceId": ref, "status": "paid",
              "transactionId": "tx-9", "amount": {"value": 15000}})
with mock.patch.object(ZainCashProvider, "inquiry", return_value={}):
    resp = client.get(f"/payments/zaincash/return?token={tok}")
check("return: redirect (302)", resp.status_code == 302)
check("return: redirects to wallet with pending flag",
      "/dashboard/wallet" in resp.headers["Location"]
      and "topup=pending" in resp.headers["Location"])
db = get_session()
t = db.query(TopUp).filter_by(provider_ref=ref).first()
check("return: TopUp still PENDING", t.status == "PENDING")
check("return: wallet NOT credited", balance_usd(db, uid) == Decimal("0"))
db.close()

print("== 3b. return-url WITH inquiry confirmation credits exactly once ==")
inq = {"status": "paid", "amount": {"value": 15000, "currency": "IQD"},
       "transactionId": "tx-9"}
with mock.patch.object(ZainCashProvider, "inquiry", return_value=inq):
    resp = client.get(f"/payments/zaincash/return?token={tok}")
check("return: success redirect",
      "topup=success" in resp.headers["Location"])
db = get_session()
t = db.query(TopUp).filter_by(provider_ref=ref).first()
check("return: PENDING -> COMPLETED", t.status == "COMPLETED")
check("return: completed_at set", t.completed_at is not None)
check("return: credited USD = 15000/1500 = 10.00", t.amount_usd == Decimal("10.00"))
check("return: wallet balance 10.00", balance_usd(db, uid) == Decimal("10.00"))
n_dep = db.query(Transaction).filter_by(
    reference=f"topup:zaincash:{ref}", type="deposit").count()
check("return: exactly one ledger deposit", n_dep == 1)
# repeat the return -> must not double-credit
with mock.patch.object(ZainCashProvider, "inquiry", return_value=inq):
    resp = client.get(f"/payments/zaincash/return?token={tok}")
check("return: second visit still success (idempotent)",
      "topup=success" in resp.headers["Location"])
check("return: balance unchanged after repeat",
      balance_usd(db, uid) == Decimal("10.00"))
db.close()

print("== 3c. amount mismatch does NOT credit ==")
db = get_session()
t2, ref2 = make_topup(db, uid, amount_iqd=20000)
db.close()
tok2 = signed({"externalReferenceId": ref2, "status": "paid",
               "transactionId": "tx-10"})
inq_bad = {"status": "paid", "amount": {"value": 99999, "currency": "IQD"}}
with mock.patch.object(ZainCashProvider, "inquiry", return_value=inq_bad):
    resp = client.get(f"/payments/zaincash/return?token={tok2}")
check("return: amount mismatch -> failed",
      "topup=failed" in resp.headers["Location"])
db = get_session()
t2 = db.query(TopUp).filter_by(provider_ref=ref2).first()
check("return: mismatch marks FAILED", t2.status == "FAILED")
check("return: mismatch does not credit",
      balance_usd(db, uid) == Decimal("10.00"))
db.close()

print("== 3d. double webhook delivery credits once ==")
db = get_session()
t3, ref3 = make_topup(db, uid, amount_iqd=30000)
db.close()
wh_tok = signed({"eventId": "evt-1", "merchantReferenceId": ref3,
                 "transactionId": "tx-11", "status": "paid"})
wh_inq = {"status": "paid", "amount": {"value": 30000}}
with mock.patch.object(ZainCashProvider, "inquiry", return_value=wh_inq):
    r1 = client.post("/payments/zaincash/webhook",
                     json={"webhook_token": wh_tok})
    r2 = client.post("/payments/zaincash/webhook",
                     json={"webhook_token": wh_tok})
check("webhook: first delivery 200", r1.status_code == 200)
check("webhook: second delivery 200", r2.status_code == 200)
db = get_session()
t3 = db.query(TopUp).filter_by(provider_ref=ref3).first()
check("webhook: PENDING -> COMPLETED", t3.status == "COMPLETED")
check("webhook: credited USD = 30000/1500 = 20.00",
      balance_usd(db, uid) == Decimal("30.00"))
n_dep = db.query(Transaction).filter_by(
    reference=f"topup:zaincash:{ref3}", type="deposit").count()
check("webhook: exactly one ledger deposit despite 2 deliveries", n_dep == 1)
n_evt = db.query(WebhookEvent).filter_by(
    provider="zaincash", event_id="evt-1").count()
check("webhook: event recorded once", n_evt == 1)
db.close()

print("== 3e. webhook with bad signature: 200, no credit ==")
db = get_session()
t4, ref4 = make_topup(db, uid, amount_iqd=5000)
db.close()
bad_wh = signed({"eventId": "evt-2", "merchantReferenceId": ref4,
                 "transactionId": "tx-12", "status": "paid"}, key="WRONG")
with mock.patch.object(ZainCashProvider, "inquiry",
                       return_value={"status": "paid"}):
    r = client.post("/payments/zaincash/webhook", json={"webhook_token": bad_wh})
check("webhook: bad signature still 200", r.status_code == 200)
db = get_session()
t4 = db.query(TopUp).filter_by(provider_ref=ref4).first()
check("webhook: bad signature leaves PENDING", t4.status == "PENDING")
check("webhook: bad signature does not credit",
      balance_usd(db, uid) == Decimal("30.00"))
db.close()

print("== 3f. webhook non-paid terminal status marks FAILED ==")
db = get_session()
t5, ref5 = make_topup(db, uid, amount_iqd=5000)
db.close()
fail_wh = signed({"eventId": "evt-3", "merchantReferenceId": ref5,
                  "transactionId": "tx-13", "status": "failed"})
with mock.patch.object(ZainCashProvider, "inquiry", return_value={}):
    r = client.post("/payments/zaincash/webhook", json={"webhook_token": fail_wh})
check("webhook: failed status 200", r.status_code == 200)
db = get_session()
t5 = db.query(TopUp).filter_by(provider_ref=ref5).first()
check("webhook: non-paid status marks FAILED", t5.status == "FAILED")
check("webhook: failed does not credit",
      balance_usd(db, uid) == Decimal("30.00"))
db.close()

print("== 3g. cancel marks PENDING top-up CANCELED ==")
db = get_session()
t6, ref6 = make_topup(db, uid, amount_iqd=5000)
db.close()
r = client.get(f"/payments/zaincash/cancel?ref={ref6}")
check("cancel: redirect", r.status_code == 302)
check("cancel: wallet?topup=canceled", "topup=canceled" in r.headers["Location"])
db = get_session()
t6 = db.query(TopUp).filter_by(provider_ref=ref6).first()
check("cancel: PENDING -> CANCELED", t6.status == "CANCELED")
db.close()

print("== 3h. create: POST /dashboard/wallet/topup (mocked invoice) ==")
fake = {"ok": True, "redirect_url": "https://pay.test/checkout",
        "provider_ref": "istore-x", "raw": {}}
with mock.patch.object(
        ZainCashProvider, "create_invoice",
        return_value=fake) as m:
    r = client.post("/dashboard/wallet/topup",
                    data={"csrf": "csrf-token-for-tests", "amount_iqd": "25000"})
check("create: redirects to provider URL",
      r.status_code == 302 and r.headers["Location"] == "https://pay.test/checkout")
check("create: invoice called with IQD amount",
      m.call_args.kwargs.get("amount") == 25000
      and m.call_args.kwargs.get("currency") == "IQD")
db = get_session()
nt = db.query(TopUp).filter_by(user_id=uid, amount_iqd=25000,
                               status="PENDING").count()
check("create: PENDING TopUp row persisted", nt == 1)
db.close()

print("== 3i. create: amount below min rejected ==")
r = client.post("/dashboard/wallet/topup",
                data={"csrf": "csrf-token-for-tests", "amount_iqd": "100"})
check("create: invalid amount redirects", r.status_code == 302)
check("create: invalid_amount flag",
      "topup=invalid_amount" in r.headers["Location"])

print(f"\nALL {len(passed)} TOPUP TESTS PASSED")
