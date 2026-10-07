"""i Store — database models. Money uses Numeric (never float)."""
import datetime
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey,
    Numeric, JSON, UniqueConstraint, Index, Enum as SAEnum,
)
from sqlalchemy.orm import relationship

from db import Base

MONEY = Numeric(18, 6)


def now():
    return datetime.datetime.utcnow()


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(32), unique=True, nullable=False)  # SUPER_ADMIN/ADMIN/SUPPORT/CUSTOMER


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), default="")
    phone = Column(String(32), default="")
    password_hash = Column(String(255), nullable=False)
    language = Column(String(8), default="ku")
    currency = Column(String(8), default="USD")
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, default=False)
    totp_secret = Column(String(64), default="")  # 2FA (admin)
    telegram_id = Column(String(32), default="", index=True)
    created_at = Column(DateTime, default=now)
    last_login_at = Column(DateTime, nullable=True)

    roles = relationship("Role", secondary="user_roles", backref="users")
    wallet = relationship("Wallet", uselist=False, back_populates="user")


class UserRole(Base):
    __tablename__ = "user_roles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)


class Provider(Base):
    __tablename__ = "providers"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)  # kd1s | mock
    name = Column(String(128), nullable=False)  # internal only
    api_url = Column(String(512), default="")
    is_active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)
    last_error = Column(Text, default="")
    created_at = Column(DateTime, default=now)


class Platform(Base):
    __tablename__ = "platforms"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)  # instagram/tiktok/...
    name = Column(String(64), nullable=False)
    icon = Column(String(64), default="")
    color = Column(String(16), default="#888888")
    sort_order = Column(Integer, default=0)


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    platform_id = Column(Integer, ForeignKey("platforms.id"), nullable=True)
    name = Column(String(128), nullable=False)
    sort_order = Column(Integer, default=0)
    is_hidden = Column(Boolean, default=False)


class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    provider_service_id = Column(String(64), nullable=False)  # NEVER exposed publicly
    name = Column(String(512), nullable=False)
    description = Column(Text, default="")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    platform_id = Column(Integer, ForeignKey("platforms.id"), nullable=True)
    service_type = Column(String(64), default="Default")
    provider_rate = Column(MONEY, default=Decimal("0"))      # per 1000, USD
    selling_price_usd = Column(MONEY, default=Decimal("0"))  # per 1000, USD
    custom_price_usd = Column(MONEY, nullable=True)          # admin override
    min_quantity = Column(Integer, default=1)
    max_quantity = Column(Integer, default=1000000)
    supports_refill = Column(Boolean, default=False)
    supports_cancel = Column(Boolean, default=False)
    status = Column(String(16), default="active")  # active|inactive|provider_unavailable
    sort_order = Column(Integer, default=0)
    is_featured = Column(Boolean, default=False)
    flags = Column(JSON, default=list)  # ["recommended","fast",...]
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    last_synced_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider_id", "provider_service_id"),
        Index("ix_services_search", "status", "platform_id", "category_id"),
        Index("ix_services_name", "name"),
    )


class PricingRule(Base):
    __tablename__ = "pricing_rules"
    id = Column(Integer, primary_key=True)
    scope = Column(String(16), nullable=False)  # global|platform|category|service
    scope_id = Column(Integer, nullable=True)
    markup_percent = Column(MONEY, default=Decimal("0"))
    markup_fixed_usd = Column(MONEY, default=Decimal("0"))
    is_active = Column(Boolean, default=True)


class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    balance_usd = Column(MONEY, default=Decimal("0"))
    user = relationship("User", back_populates="wallet")


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False, index=True)
    type = Column(String(32), nullable=False)  # deposit|order_charge|refund|manual_adjustment|bonus|fee
    amount_usd = Column(MONEY, nullable=False)  # signed
    balance_after_usd = Column(MONEY, nullable=False)
    currency = Column(String(8), default="USD")
    exchange_rate = Column(MONEY, default=Decimal("1"))
    reference = Column(String(128), default="", index=True)  # order id / deposit id
    note = Column(String(512), default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=now, index=True)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    provider_order_id = Column(String(64), default="", index=True)
    idempotency_key = Column(String(64), unique=True, nullable=False, index=True)
    quantity = Column(Integer, default=0)
    link = Column(String(1024), default="")
    input_data = Column(JSON, default=dict)
    provider_cost_usd = Column(MONEY, default=Decimal("0"))
    customer_charge_usd = Column(MONEY, default=Decimal("0"))
    profit_usd = Column(MONEY, default=Decimal("0"))
    currency = Column(String(8), default="USD")
    exchange_rate = Column(MONEY, default=Decimal("1"))  # rate at purchase time
    status = Column(String(16), default="PENDING", index=True)
    start_count = Column(String(64), default="")
    remains = Column(String(64), default="")
    created_at = Column(DateTime, default=now, index=True)
    updated_at = Column(DateTime, default=now, onupdate=now)
    completed_at = Column(DateTime, nullable=True)


class OrderEvent(Base):
    __tablename__ = "order_events"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    event = Column(String(64), nullable=False)
    old_status = Column(String(16), default="")
    new_status = Column(String(16), default="")
    meta = Column(JSON, default=dict)
    created_at = Column(DateTime, default=now)


class Refill(Base):
    __tablename__ = "refills"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    provider_refill_id = Column(String(64), default="")
    status = Column(String(32), default="PENDING")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(32), default="INFO")
    title = Column(String(256), default="")
    body = Column(Text, default="")
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now, index=True)


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    subject = Column(String(256), nullable=False)
    category = Column(String(64), default="general")
    priority = Column(String(16), default="normal")
    status = Column(String(16), default="OPEN", index=True)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_staff = Column(Boolean, default=False)
    internal_note = Column(Boolean, default=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(128), nullable=False, index=True)
    target = Column(String(256), default="")
    meta = Column(JSON, default=dict)
    ip = Column(String(64), default="")
    user_agent = Column(String(512), default="")
    created_at = Column(DateTime, default=now, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(128), unique=True, nullable=False)
    value = Column(Text, default="")
    is_secret = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=now, onupdate=now)


class SyncLog(Base):
    __tablename__ = "sync_logs"
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    started_at = Column(DateTime, default=now)
    finished_at = Column(DateTime, nullable=True)
    found = Column(Integer, default=0)
    added = Column(Integer, default=0)
    updated = Column(Integer, default=0)
    disabled = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    duration_s = Column(MONEY, default=Decimal("0"))


class ProviderHealthLog(Base):
    __tablename__ = "provider_health_logs"
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False, index=True)
    ok = Column(Boolean, default=True)
    latency_ms = Column(Integer, default=0)
    error = Column(Text, default="")
    created_at = Column(DateTime, default=now, index=True)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, index=True)
    payload = Column(JSON, default=dict)
    status = Column(String(16), default="queued", index=True)  # queued|running|done|failed
    attempts = Column(Integer, default=0)
    run_after = Column(DateTime, default=now, index=True)
    last_error = Column(Text, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class SocialLink(Base):
    """Admin-configurable official social accounts. Never hardcoded in templates."""
    __tablename__ = "social_links"
    id = Column(Integer, primary_key=True)
    platform = Column(String(32), unique=True, nullable=False)  # telegram|instagram|tiktok|facebook|youtube|x|whatsapp|support
    url = Column(String(512), default="")
    is_enabled = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    updated_at = Column(DateTime, default=now, onupdate=now)


class Favorite(Base):
    __tablename__ = "favorites"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("user_id", "service_id"),)


class RecentlyViewed(Base):
    __tablename__ = "recently_viewed"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False, index=True)
    viewed_at = Column(DateTime, default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("user_id", "service_id"),)


class NotificationPreference(Base):
    __tablename__ = "notification_prefs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    order_updates = Column(Boolean, default=True)
    telegram_enabled = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=now, onupdate=now)


class TotpDevice(Base):
    """TOTP 2FA device for admin users. Secret is Fernet-encrypted at rest."""
    __tablename__ = "totp_devices"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False,
                     index=True)
    secret_enc = Column(Text, nullable=False)
    enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)
    last_used_at = Column(DateTime, nullable=True)


class TotpBackupCode(Base):
    """Single-use TOTP backup codes (bcrypt-hashed, never stored plaintext)."""
    __tablename__ = "totp_backup_codes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    code_hash = Column(String(128), nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)


class PaymentInvoice(Base):
    """Top-up invoice created via a payment provider (never fake)."""
    __tablename__ = "payment_invoices"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)
    provider_ref = Column(String(128), unique=True, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(8), default="IQD")
    status = Column(String(32), default="pending")  # pending|paid|failed|expired
    raw = Column(JSON, default=dict)
    created_at = Column(DateTime, default=now, index=True)
    paid_at = Column(DateTime, nullable=True)


class TopUp(Base):
    """Real-money wallet top-up via a payment provider (ZainCash).

    Money safety rules:
    - provider_ref is the ZainCash externalReferenceId (UNIQUE): one row
      per provider transaction, forever.
    - idempotency_key (UNIQUE): one credit attempt per row.
    - status only moves PENDING -> COMPLETED | FAILED | CANCELED, and the
      wallet is credited exactly once, inside the same DB transaction that
      flips the status (SELECT ... FOR UPDATE).
    - exchange_rate is the admin USD_TO_IQD rate frozen at top-up time.
    """
    __tablename__ = "topups"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(32), default="zaincash", nullable=False)
    provider_ref = Column(String(128), unique=True, nullable=False, index=True)
    idempotency_key = Column(String(64), unique=True, nullable=False, index=True)
    amount_iqd = Column(Integer, nullable=False)
    amount_usd = Column(MONEY, default=Decimal("0"))  # credited amount
    exchange_rate = Column(MONEY, default=Decimal("1500"))  # USD_TO_IQD at top-up time
    status = Column(String(16), default="PENDING", index=True)  # PENDING|COMPLETED|FAILED|CANCELED
    raw_response = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=now, index=True)
    completed_at = Column(DateTime, nullable=True)


class WebhookEvent(Base):
    """Processed provider webhook event IDs (idempotency for double delivery)."""
    __tablename__ = "webhook_events"
    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False, index=True)
    event_id = Column(String(128), nullable=False)
    received_at = Column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("provider", "event_id"),)
