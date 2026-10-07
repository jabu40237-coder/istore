"""FastPay provider (Iraq). See PAYMENTS_RESEARCH.md.

VERIFIED (official portal https://developer.fast-pay.iq/website-integration):
- Auth: store_id + store_password on every API call (no OAuth);
  separate refund secret key.
- Hosted redirect flow; after redirect the merchant MUST verify via the
  Transaction Validation API.
- IPN POSTs on successful payments ONLY to a panel-configured URL.

UNVERIFIED (from community SDKs — confirm with FastPay before going live):
- exact endpoint paths below, staging self-serve credentials, fee (~3%).
"""
import uuid

import httpx

from .base import PaymentProvider, InvoiceResult, VerifyResult

# UNVERIFIED paths — override via settings if FastPay provides different ones.
STAGE_HOST = "https://stage.fast-pay.iq"   # UNVERIFIED
PROD_HOST = "https://prod.fast-pay.iq"     # UNVERIFIED


class FastPayProvider(PaymentProvider):
    code = "fastpay"
    display_name = "FastPay"
    supports_currency = ("IQD",)
    required_settings = ("store_id", "store_password")

    def _host(self):
        if self.test_mode:
            return (self.settings.get("stage_host") or STAGE_HOST).rstrip("/")
        return (self.settings.get("prod_host") or PROD_HOST).rstrip("/")

    def _auth(self):
        return {"store_id": self.settings["store_id"],
                "store_password": self.settings["store_password"]}

    def create_invoice(self, *, user_id, amount, currency="IQD",
                       description="", return_url="", webhook_url="",
                       reference="", order_id="", language="en",
                       cancel_url=""):
        if currency != "IQD":
            return InvoiceResult(ok=False, error="fastpay_iqd_only")
        try:
            ref = f"istore-{user_id}-{uuid.uuid4().hex[:12]}"
            # UNVERIFIED path — confirm with FastPay docs.
            r = httpx.post(f"{self._host()}/api/v1/payment/init", json={
                **self._auth(),
                "order_id": ref,
                "amount": int(round(amount)),
                "currency": "IQD",
                "description": description[:140],
                "redirect_url": return_url,
                "ipn_url": webhook_url,
            }, timeout=20)
            r.raise_for_status()
            data = r.json()
            redirect_url = data.get("redirect_url") or data.get("payment_url")
            if not redirect_url:
                return InvoiceResult(ok=False, error="no_redirect_url", raw=data)
            return InvoiceResult(ok=True, redirect_url=redirect_url,
                                 provider_ref=ref, raw=data)
        except Exception as e:
            return InvoiceResult(ok=False, error=str(e)[:200])

    def verify_callback(self, payload: dict, headers: dict) -> VerifyResult:
        """Validate via the Transaction Validation API (VERIFIED requirement).
        Never trust the redirect alone."""
        ref = (payload.get("order_id") or payload.get("reference")
               or payload.get("transaction_id") or "")
        if not ref:
            return VerifyResult(ok=False, error="missing_reference")
        try:
            # UNVERIFIED path — confirm with FastPay docs.
            r = httpx.post(f"{self._host()}/api/v1/payment/validate", json={
                **self._auth(), "order_id": ref,
            }, timeout=20)
            r.raise_for_status()
            data = r.json()
            status = str(data.get("status") or "")
            paid = status.lower() in ("success", "paid", "completed", "approved")
            return VerifyResult(ok=paid, provider_ref=ref,
                                amount=data.get("amount"),
                                currency=data.get("currency", "IQD"),
                                status=status, raw=data)
        except Exception as e:
            return VerifyResult(ok=False, error=str(e)[:200])
