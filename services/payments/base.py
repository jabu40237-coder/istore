"""Pluggable payment providers for i Store.

Each provider implements: create_invoice() and verify_callback().
No provider is active until Ali selects it AND saves real credentials
in /admin/payments — until then the wallet shows "Payment System Coming Soon".

Endpoint details: see PAYMENTS_RESEARCH.md. Constants marked VERIFIED come
from official docs; UNVERIFIED ones come from community SDKs and must be
confirmed with the provider before going live.
"""
from abc import ABC, abstractmethod


class InvoiceResult(dict):
    """create_invoice() return: {ok, redirect_url?, provider_ref?, error?}"""


class VerifyResult(dict):
    """verify_callback() return: {ok, provider_ref?, amount?, currency?,
    status?, error?, raw?}"""


class PaymentProvider(ABC):
    code = "base"
    display_name = "Base"
    supports_currency = ("IQD",)

    def __init__(self, settings: dict, test_mode: bool = True):
        self.settings = settings
        self.test_mode = test_mode

    @abstractmethod
    def create_invoice(self, *, user_id: int, amount: float, currency: str,
                       description: str, return_url: str,
                       webhook_url: str) -> InvoiceResult:
        ...

    @abstractmethod
    def verify_callback(self, payload: dict, headers: dict) -> VerifyResult:
        ...

    def is_configured(self) -> bool:
        return all(self.settings.get(k) for k in self.required_settings)

    required_settings: tuple = ()
