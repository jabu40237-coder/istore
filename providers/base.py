"""Provider abstraction. KD1S is provider #1; others can be added later.

Customer-facing code must NEVER see provider internals (API key, URLs,
provider service IDs). All provider calls happen server-side.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional


@dataclass
class ProviderService:
    service_id: str
    name: str
    service_type: str = "Default"
    category: str = ""
    rate: Decimal = Decimal("0")  # per 1000, USD
    min: int = 1
    max: int = 1000000
    refill: bool = False
    cancel: bool = False


@dataclass
class ProviderOrderResult:
    ok: bool
    provider_order_id: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass
class ProviderStatus:
    status: str = ""  # raw provider status
    start_count: str = ""
    remains: str = ""
    error_code: str = ""


class ProviderError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code  # normalized: INVALID_API_KEY, PROVIDER_TIMEOUT, ...


class BaseProvider:
    code: str = "base"
    name: str = "Base"

    def get_services(self) -> List[ProviderService]:
        raise NotImplementedError

    def create_order(self, service_id: str, link: str = "", quantity: int = 0,
                     extra: Optional[dict] = None) -> ProviderOrderResult:
        raise NotImplementedError

    def get_order_status(self, provider_order_id: str) -> ProviderStatus:
        raise NotImplementedError

    def get_multiple_statuses(self, provider_order_ids: List[str]) -> dict:
        # default: one-by-one; override when batch API exists
        return {oid: self.get_order_status(oid) for oid in provider_order_ids}

    def create_refill(self, provider_order_id: str) -> ProviderOrderResult:
        raise NotImplementedError

    def get_refill_status(self, provider_refill_id: str) -> str:
        raise NotImplementedError

    def create_cancel(self, provider_order_ids: List[str]) -> dict:
        raise NotImplementedError

    def get_balance(self) -> Decimal:
        raise NotImplementedError

    def health_check(self) -> tuple:
        """Returns (ok: bool, latency_ms: int, error: str)."""
        import time
        t0 = time.time()
        try:
            self.get_balance()
            return True, int((time.time() - t0) * 1000), ""
        except ProviderError as e:
            return False, int((time.time() - t0) * 1000), e.code
        except Exception as e:
            return False, int((time.time() - t0) * 1000), str(e)[:200]
