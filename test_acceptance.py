"""Acceptance test: full commerce loop on the mock provider."""
from decimal import Decimal

from db import init_db, get_session
from models import Provider, Service, Order, Transaction, User
from providers.mock import MockProvider
from services.sync import sync_provider
from services import auth as auth_svc
from services import wallet as wallet_svc
from services import orders as order_svc
from services.pricing import ensure_default_rules

init_db()
auth_svc.ensure_roles()
db = get_session()
ensure_default_rules(db)

# 1) sync services from mock provider
prov = db.query(Provider).filter_by(code="mock").first()
stats = sync_provider(MockProvider(), prov)
n = db.query(Service).filter_by(status="active").count()
print("SYNC:", stats, "| active services:", n)
assert n >= 7, "sync failed"

# 2) register customer + fund wallet
uid, err = auth_svc.create_user("test@istore.test", "testuser", "pass1234", name="Test")
assert not err, err
db2 = get_session()
wallet_svc.apply_transaction(db2, uid, "deposit", Decimal("50"),
                             reference="test-deposit", note="test funding")
db2.commit()
bal = wallet_svc.get_balance_usd(db2, uid)
print("BALANCE after deposit:", bal)
assert bal == Decimal("50")
db2.close()

# 3) place order
db3 = get_session()
svc = db3.query(Service).filter_by(status="active").first()
svc_id, smin = svc.id, svc.min_quantity
db3.close()
res = order_svc.create_order(uid, svc_id, "https://instagram.com/p/test123",
                             smin, {}, MockProvider())
print("ORDER:", res)
assert res["ok"], res
oid = res["order_id"]

# 4) verify: order reached provider, wallet charged, profit recorded
db4 = get_session()
o = db4.query(Order).filter_by(id=oid).first()
print(f"order status={o.status} provider_order_id={o.provider_order_id} "
      f"charge={o.customer_charge_usd} cost={o.provider_cost_usd} profit={o.profit_usd}")
assert o.provider_order_id, "no provider order id!"
assert o.status == "PROCESSING"
txs = db4.query(Transaction).filter_by(wallet_id=db4.query(
    __import__("models", fromlist=["Wallet"]).Wallet).filter_by(user_id=uid).first().id).all()
print("ledger entries:", [(t.type, str(t.amount_usd)) for t in txs])
assert any(t.type == "order_charge" and t.amount_usd < 0 for t in txs)
bal2 = wallet_svc.get_balance_usd(db4, uid)
print("BALANCE after order:", bal2)
assert bal2 == Decimal("50") - o.customer_charge_usd

# 5) idempotency: same key -> same order, no double charge
res2 = order_svc.create_order(uid, svc_id, "https://instagram.com/p/test123",
                              smin, {}, MockProvider())
print("DUPLICATE:", res2)
assert res2["ok"] and res2.get("duplicate") and res2["order_id"] == oid
bal3 = wallet_svc.get_balance_usd(db4, uid)
assert bal3 == bal2, "double charge detected!"
print("IDEMPOTENCY OK — no double charge")

# 6) insufficient balance
res3 = order_svc.create_order(uid, svc_id, "https://x.com/test",
                              1000000, {}, MockProvider())
print("OVERLIMIT:", res3)
assert not res3["ok"]

# 7) status sync
counts = order_svc.sync_order_statuses(MockProvider())
print("STATUS SYNC:", counts)
db4.close()
print("ALL ACCEPTANCE TESTS PASSED")
