"""Pricing engine: provider_rate -> customer selling price.

Priority: service custom price > service rule > category rule >
            platform rule > global rule > provider rate as-is.

All amounts are Decimal (per 1000, USD).
"""
from decimal import Decimal

from models import PricingRule


def _apply_markup(rate: Decimal, pct: Decimal, fixed: Decimal) -> Decimal:
    price = rate * (Decimal("1") + pct / Decimal("100")) + fixed
    return price.quantize(Decimal("0.000001"))


def compute_selling_price(db, service) -> Decimal:
    if service.custom_price_usd is not None:
        return service.custom_price_usd

    rate = service.provider_rate or Decimal("0")

    # service-specific rule
    rule = db.query(PricingRule).filter_by(
        scope="service", scope_id=service.id, is_active=True).first()
    if not rule and service.category_id:
        rule = db.query(PricingRule).filter_by(
            scope="category", scope_id=service.category_id, is_active=True).first()
    if not rule and service.platform_id:
        rule = db.query(PricingRule).filter_by(
            scope="platform", scope_id=service.platform_id, is_active=True).first()
    if not rule:
        rule = db.query(PricingRule).filter_by(scope="global", is_active=True).first()

    if rule:
        return _apply_markup(rate, rule.markup_percent or Decimal("0"),
                             rule.markup_fixed_usd or Decimal("0"))
    return rate


def ensure_default_rules(db):
    """Seed a global 30% markup if nothing exists."""
    if not db.query(PricingRule).filter_by(scope="global").first():
        db.add(PricingRule(scope="global", markup_percent=Decimal("30")))
        db.commit()
