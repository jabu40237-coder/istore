"""Mock provider for development/testing. No real API key needed.

Simulates services, order creation, and status progression so the full
order engine can be tested end-to-end without touching KD1S.
"""
import itertools
import time
from decimal import Decimal

from providers.base import (
    BaseProvider, ProviderService, ProviderOrderResult, ProviderStatus,
)

_services = [
    ("101", "Instagram Followers | Premium", "Default", "Instagram/Followers", "0.90", 50, 10000, True, True),
    ("102", "Instagram Likes | Fast", "Default", "Instagram/Likes", "0.45", 50, 5000, True, True),
    ("103", "TikTok Followers | Real", "Default", "TikTok/Followers", "1.20", 100, 20000, True, True),
    ("104", "TikTok Views | Instant", "Default", "TikTok/Views", "0.08", 100, 1000000, False, True),
    ("105", "YouTube Subscribers", "Default", "YouTube/Subscribers", "2.50", 50, 5000, True, False),
    ("106", "Telegram Members | HQ", "Default", "Telegram/Members", "1.80", 100, 50000, True, True),
    ("107", "Custom Comments Pack", "Custom Comments", "Instagram/Comments", "3.00", 10, 500, False, False),
]

_orders = {}
_idem = {}
_counter = itertools.count(1000)


class MockProvider(BaseProvider):
    code = "mock"
    name = "Mock (dev)"

    def get_services(self):
        return [ProviderService(
            service_id=s[0], name=s[1], service_type=s[2], category=s[3],
            rate=Decimal(s[4]), min=s[5], max=s[6], refill=s[7], cancel=s[8],
        ) for s in _services]

    def create_order(self, service_id="", link="", quantity=0, extra=None):
        if not any(s[0] == str(service_id) for s in _services):
            return ProviderOrderResult(ok=False, error_code="SERVICE_NOT_FOUND")
        if quantity <= 0:
            return ProviderOrderResult(ok=False, error_code="INVALID_QUANTITY")
        oid = str(next(_counter))
        _orders[oid] = {"status": "In progress", "start_count": "1000",
                        "remains": str(quantity), "created": time.time(),
                        "quantity": quantity}
        return ProviderOrderResult(ok=True, provider_order_id=oid)

    def get_order_status(self, provider_order_id):
        o = _orders.get(str(provider_order_id))
        if not o:
            return ProviderStatus(error_code="UNKNOWN_PROVIDER_ERROR")
        # simulate progress over time
        if time.time() - o["created"] > 60:
            o["status"] = "Completed"
            o["remains"] = "0"
        return ProviderStatus(status=o["status"], start_count=o["start_count"],
                              remains=o["remains"])

    def create_refill(self, provider_order_id):
        if str(provider_order_id) not in _orders:
            return ProviderOrderResult(ok=False, error_code="UNKNOWN_PROVIDER_ERROR")
        return ProviderOrderResult(ok=True, provider_order_id="r" + str(provider_order_id))

    def get_refill_status(self, provider_refill_id):
        return "Completed"

    def create_cancel(self, provider_order_ids):
        out = {}
        for oid in provider_order_ids:
            o = _orders.get(str(oid))
            if o and o["status"] not in ("Completed",):
                o["status"] = "Canceled"
                out[oid] = {"cancel": 1}
            else:
                out[oid] = {"cancel": {"error": "cannot cancel"}}
        return out

    def get_balance(self):
        return Decimal("1000.00")
