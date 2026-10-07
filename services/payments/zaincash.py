"""ZainCash provider (Iraq). See PAYMENTS_RESEARCH.md.

VERIFIED (official docs https://docs.zaincash.iq/, Aug 2026):
- OAuth2 client-credentials: POST {host}/oauth2/token (scopes)
- Init: POST {host}/api/v2/payment-gateway/transaction/init
  (idempotency via externalReferenceId, currency forced IQD, lang en/ar/ku)
- UAT host: https://pg-api-uat.zaincash.iq (webhooks only work in production)
- Redirect callback: signed JWT, verified with API key (HS256)
- Server webhook: JWT webhook_token, STATUS_CHANGED events (source of truth)

NOT VERIFIED: production host (set via settings), fee schedule (negotiated).
"""
import time
import uuid

import httpx
import jwt as pyjwt

from .base import PaymentProvider, InvoiceResult, VerifyResult

UAT_HOST = "https://pg-api-uat.zaincash.iq"  # VERIFIED


class ZainCashProvider(PaymentProvider):
    code = "zaincash"
    display_name = "ZainCash"
    supports_currency = ("IQD",)
    required_settings = ("client_id", "client_secret", "api_key")

    def _host(self):
        if self.test_mode:
            return UAT_HOST
        return (self.settings.get("prod_host") or "").rstrip("/") or UAT_HOST

    def _token(self) -> str:
        """OAuth2 client-credentials token (VERIFIED endpoint)."""
        r = httpx.post(f"{self._host()}/oauth2/token", data={
            "grant_type": "client_credentials",
            "client_id": self.settings["client_id"],
            "client_secret": self.settings["client_secret"],
            "scope": "payment",
        }, timeout=20)
        r.raise_for_status()
        return r.json()["access_token"]

    def create_invoice(self, *, user_id, amount, currency="IQD",
                       description="", return_url="", webhook_url=""):
        if currency != "IQD":
            return InvoiceResult(ok=False, error="zaincash_iqd_only")
        try:
            token = self._token()
            ref = f"istore-{user_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
            r = httpx.post(
                f"{self._host()}/api/v2/payment-gateway/transaction/init",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "externalReferenceId": ref,  # idempotency (VERIFIED)
                    "amount": int(round(amount)),
                    "currency": "IQD",
                    "language": "en",
                    "description": description[:140],
                    "redirectUrl": return_url,
                    "webhookUrl": webhook_url,
                }, timeout=20)
            r.raise_for_status()
            data = r.json()
            redirect_url = (data.get("redirectUrl") or data.get("redirect_url")
                            or data.get("paymentUrl"))
            if not redirect_url:
                return InvoiceResult(ok=False, error="no_redirect_url", raw=data)
            return InvoiceResult(ok=True, redirect_url=redirect_url,
                                 provider_ref=ref, raw=data)
        except Exception as e:
            return InvoiceResult(ok=False, error=str(e)[:200])

    def verify_callback(self, payload: dict, headers: dict) -> VerifyResult:
        """Verify the signed-JWT redirect callback with the API key (HS256)."""
        token = (payload.get("token") or payload.get("jwt") or "")
        if not token:
            return VerifyResult(ok=False, error="missing_token")
        try:
            data = pyjwt.decode(token, self.settings["api_key"],
                                algorithms=["HS256"])
        except Exception as e:
            return VerifyResult(ok=False, error=f"bad_signature: {e}"[:200])
        status = str(data.get("status") or data.get("transactionStatus") or "")
        return VerifyResult(
            ok=status.lower() in ("success", "completed", "paid", "approved"),
            provider_ref=data.get("externalReferenceId") or data.get("referenceId"),
            amount=data.get("amount"),
            currency=data.get("currency", "IQD"),
            status=status,
            raw=data,
        )

    def verify_webhook(self, payload: dict, headers: dict) -> VerifyResult:
        """Server-to-server webhook (JWT webhook_token). Same shape as callback."""
        return self.verify_callback(payload, headers)
