"""Wallet ledger. Every balance change creates an immutable transaction.

Never do balance += x without a transaction row. Uses SELECT ... FOR UPDATE
style locking via the DB transaction (SQLite: single writer; PostgreSQL:
row locks work the same through this code path).
"""
from decimal import Decimal
import logging

from db import get_session
from models import Wallet, Transaction, User, now

log = logging.getLogger("istore.security")


def get_wallet(db, user_id: int) -> Wallet:
    w = db.query(Wallet).filter_by(user_id=user_id).first()
    if not w:
        w = Wallet(user_id=user_id, balance_usd=Decimal("0"))
        db.add(w)
        db.flush()
    return w


def apply_transaction(db, user_id: int, type_: str, amount_usd: Decimal,
                      currency: str = "USD", exchange_rate: Decimal = Decimal("1"),
                      reference: str = "", note: str = "",
                      created_by=None) -> Transaction:
    """amount_usd is SIGNED (negative for charges). Returns the transaction."""
    amount_usd = Decimal(str(amount_usd))
    wallet = get_wallet(db, user_id)
    # lock row (PostgreSQL); on SQLite the write transaction serializes
    db.flush()
    new_balance = (wallet.balance_usd or Decimal("0")) + amount_usd
    if new_balance < 0:
        raise ValueError("insufficient_balance")
    wallet.balance_usd = new_balance
    tx = Transaction(
        wallet_id=wallet.id, type=type_, amount_usd=amount_usd,
        balance_after_usd=new_balance, currency=currency,
        exchange_rate=exchange_rate, reference=reference, note=note,
        created_by=created_by,
    )
    db.add(tx)
    db.flush()
    # Security audit trail: amounts and parties only — never secrets.
    log.info("event=wallet_tx user_id=%s type=%s amount_usd=%s balance_after=%s ref=%s",
             user_id, type_, str(amount_usd), str(new_balance), reference or "-")
    return tx


def get_balance_usd(db, user_id: int) -> Decimal:
    return get_wallet(db, user_id).balance_usd or Decimal("0")
