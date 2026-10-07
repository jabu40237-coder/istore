"""ZainCash provider (Iraq). See PAYMENTS_RESEARCH.md.

VERIFIED (official docs https://docs.zaincash.iq/, Payment Gateway API v2,
guide v1.0 updated 11 Aug 2026):
- OAuth2 client-credentials: POST {host}/oauth2/token with client_id,
  client_secret and a SPACE-SEPARATED scope string
  (e.g. "payment:read payment:write reverse:write reverse:read
  disbursement:write disbursement:read")
- Init: POST {host}/api/v2/payment-gateway/transaction/init with body:
  language (en/ar/ku), externalReferenceId (UUID, idempotency key),
  orderId (internal order id), serviceType, amount {value, currency},
  customer.phone (optional), redirectUrls {successUrl, failureUrl}
- Redirect the customer to `redirectUrl` from the init response.
- Redirect callback: ?token=<JWT>, signature verified with the API key
  (HMAC-SHA256 / HS256). The redirect token is for UX only.
- Server webhook: JSON POST with `webhook_token` JWT on STATUS_CHANGED
  events — the webhook event is the SOURCE OF TRUTH. Use `eventId` for
  webhook idempotency. Webhooks only work in production (not UAT) and the
  notificationUrl must be registered with the ZainCash business team.
- Authoritative status: GET
  {host}/api/v2/payment-gateway/transaction/inquiry/{transactionId}
- Refunds: POST .../transaction/reverse (full) and .../partial-reverse.
  WARNING (per docs): reverse endpoints are NOT idempotent — a blind
  retry of a timed-out call can DOUBLE-REFUND. Always reconcile with
  GET .../transaction/reversals before retrying.

NOT VERIFIED: production host (set via admin settings), fee schedule
(negotiated per merchant), exact inquiry/reverse response shapes (parsed
defensively with fallbacks).
"""
import time
import uuid

import httpx
import jwt as pyjwt

from .base import PaymentProvider, InvoiceResult, VerifyResult

UAT_HOST = "https://pg-api-uat.zaincash.iq"  # VERIFIED
_SCOPE = "payment:read payment:write"  # VERIFIED space-separated perms


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
            "scope": _SCOPE,
        }, timeout=20)
        r.raise_for_status()
        return r.json()["access_token"]

    def create_invoice(self, *, user_id, amount, currency="IQD",
                       description="", return_url="", webhook_url="",
                       reference="", order_id="", language="en",
                       cancel_url=""):
        if currency != "IQD":
            return InvoiceResult(ok=False, error="zaincash_iqd_only")
        try:
            token = self._token()
            # Caller-supplied reference (our TopUp.provider_ref) doubles as
            # the provider-side idempotency key; generate one if absent.
            ref = reference or f"istore-{user_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
            body = {
                # Official v2 field names (VERIFIED)
                "language": language if language in ("en", "ar", "ku") else "en",
                "externalReferenceId": ref,  # idempotency key (VERIFIED)
                "orderId": str(order_id or ref),  # our internal id (VERIFIED)
                "serviceType": "wallet_topup",
                "amount": {"value": int(round(amount)), "currency": "IQD"},
                "redirectUrls": {
                    "successUrl": return_url,
                    "failureUrl": cancel_url or return_url,
                },
            }
            if description:
                body["description"] = description[:140]
            r = httpx.post(
                f"{self._host()}/api/v2/payment-gateway/transaction/init",
                headers={"Authorization": f"Bearer {token}"},
                json=body, timeout=20)
            r.raise_for_status()
            data = r.json()
            # Response field names vary across doc versions — try all known.
            redirect_url = (data.get("redirectUrl") or data.get("redirect_url")
                            or data.get("paymentUrl") or data.get("payment_url"))
            if not redirect_url:
                return InvoiceResult(ok=False, error="no_redirect_url", raw=data)
            return InvoiceResult(ok=True, redirect_url=redirect_url,
                                 provider_ref=ref, raw=data)
        except Exception as e:
            # Never include credential values in errors (settings are not
            # part of the exception chain here by construction).
            return InvoiceResult(ok=False, error=str(e)[:200])

    def inquiry(self, transaction_id: str) -> dict:
        """Authoritative transaction status (VERIFIED endpoint).

        Returns the parsed JSON ({} on failure). Callers must treat a
        missing/ambiguous status as NOT paid.
        """
        try:
            token = self._token()
            r = httpx.get(
                f"{self._host()}/api/v2/payment-gateway/transaction/"
                f"inquiry/{transaction_id}",
                headers={"Authorization": f"Bearer {token}"}, timeout=20)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def reverse(self, transaction_id: str, amount: int | None = None) -> dict:
        """Refund a transaction (admin use only).

        WARNING — per official docs the reverse endpoints are NOT
        idempotent: a blind retry of a timed-out call can DOUBLE-REFUND.
        Always reconcile with GET .../transaction/reversals before
        retrying, and never call this from an automatic retry loop.
        Returns {"ok": bool, ...}; callers must verify via reversals.
        """
        try:
            token = self._token()
            path = ("partial-reverse" if amount else "reverse")
            body = {"transactionId": transaction_id}
            if amount:
                body["amount"] = {"value": int(amount), "currency": "IQD"}
            r = httpx.post(
                f"{self._host()}/api/v2/payment-gateway/transaction/{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=body, timeout=20)
            r.raise_for_status()
            data = r.json() if r.content else {}
            return {"ok": True, "raw": data}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    def _paid_status(status: str) -> bool:
        s = (status or "").strip().lower()
        return s in ("paid", "success", "successful", "completed", "approved",
                     "captured", "settled")

    def _decode(self, token: str) -> dict:
        """Decode + HS256-verify a ZainCash JWT with the API key."""
        return pyjwt.decode(token, self.settings["api_key"],
                            algorithms=["HS256"])

    def verify_callback(self, payload: dict, headers: dict) -> VerifyResult:
        """Verify the signed-JWT redirect callback (?token=).

        ok=True means: signature valid AND the JWT claims a paid status.
        Callers MUST still confirm via inquiry() before crediting — the
        redirect token is for UX only, never the source of truth.
        """
        token = (payload.get("token") or payload.get("jwt") or "")
        if not token:
            return VerifyResult(ok=False, error="missing_token")
        try:
            data = self._decode(token)
        except Exception as e:
            return VerifyResult(ok=False, error=f"bad_signature: {e}"[:200])
        status = str(data.get("status") or data.get("transactionStatus")
                     or data.get("transaction_status") or "")
        return VerifyResult(
            ok=self._paid_status(status),
            provider_ref=(data.get("externalReferenceId")
                          or data.get("merchantReferenceId")
                          or data.get("referenceId")),
            transaction_id=(data.get("transactionId")
                            or data.get("transaction_id")),
            amount=data.get("amount"),
            currency=data.get("currency", "IQD"),
            status=status,
            raw=data,
        )

    def verify_webhook(self, payload: dict, headers: dict) -> VerifyResult:
        """Server-to-server webhook: JSON body carries `webhook_token` JWT."""
        token = payload.get("webhook_token") or ""
        if not token:
            return VerifyResult(ok=False, error="missing_webhook_token")
        try:
            data = self._decode(token)
        except Exception as e:
            return VerifyResult(ok=False, error=f"bad_signature: {e}"[:200])
        status = str(data.get("status") or data.get("transactionStatus")
                     or data.get("eventType") or "")
        # STATUS_CHANGED is the event envelope; the inner status decides.
        inner = str(data.get("transaction_status")
                    or data.get("paymentStatus") or "")
        effective = inner or status
        if data.get("eventType") == "STATUS_CHANGED" and not inner:
            # Envelope without a clear inner status: do not treat as paid.
            effective = ""
        return VerifyResult(
            ok=self._paid_status(effective),
            provider_ref=(data.get("merchantReferenceId")
                          or data.get("externalReferenceId")
                          or data.get("referenceId")),
            transaction_id=(data.get("transactionId")
                            or data.get("transaction_id")),
            event_id=data.get("eventId") or data.get("event_id"),
            amount=data.get("amount"),
            currency=data.get("currency", "IQD"),
            status=effective,
            raw=data,
        )
