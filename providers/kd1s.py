"""KD1S provider client. POST https://kd1s.com/api/v2

Actions: services, add, status, refill, refill_status, cancel, balance.
API key travels ONLY in server-side POST body. Never logged, never exposed.
"""
import time
from decimal import Decimal, InvalidOperation

import httpx

from providers.base import (
    BaseProvider, ProviderService, ProviderOrderResult, ProviderStatus, ProviderError,
)

TIMEOUT = 25.0

# Direct connection: the VM's proxy env vars are malformed for httpx
# (bracketed IPv6 in no_proxy), so bypass proxy handling entirely.
_client = httpx.Client(trust_env=False, timeout=TIMEOUT)


def _to_decimal(v) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


class KD1SProvider(BaseProvider):
    code = "kd1s"
    name = "KD1S"

    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        # NOTE: key is kept only in memory, never logged
        self._api_key = api_key

    def _post(self, action: str, **params) -> dict:
        payload = {"key": self._api_key, "action": action}
        payload.update({k: v for k, v in params.items() if v is not None})
        # strip the key from anything that could be logged
        safe = {k: ("***" if k == "key" else v) for k, v in payload.items()}
        last_exc = None
        for attempt in range(3):
            try:
                r = _client.post(self.api_url, data=payload)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, dict) and "error" in data:
                    raise ProviderError(_normalize_error(str(data["error"])), str(data["error"]))
                return data
            except ProviderError:
                raise
            except httpx.TimeoutException as e:
                last_exc = ProviderError("PROVIDER_TIMEOUT", "provider timeout")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    last_exc = ProviderError("RATE_LIMITED", "rate limited")
                else:
                    last_exc = ProviderError("PROVIDER_HTTP_ERROR", f"http {e.response.status_code}")
            except Exception as e:
                last_exc = ProviderError("PROVIDER_UNAVAILABLE", str(e)[:120])
            time.sleep(2 ** attempt)  # exponential backoff
        raise last_exc or ProviderError("PROVIDER_UNAVAILABLE")

    def get_services(self):
        data = self._post("services")
        out = []
        for s in data if isinstance(data, list) else []:
            out.append(ProviderService(
                service_id=str(s.get("service", "")),
                name=str(s.get("name", "")),
                service_type=str(s.get("type", "Default")),
                category=str(s.get("category", "")),
                rate=_to_decimal(s.get("rate", 0)),
                min=int(s.get("min", 1) or 1),
                max=int(s.get("max", 1000000) or 1000000),
                refill=bool(s.get("refill", False)),
                cancel=bool(s.get("cancel", False)),
            ))
        return out

    def create_order(self, service_id, link="", quantity=0, extra=None):
        params = {"service": service_id, "link": link, "quantity": quantity}
        if extra:
            params.update(extra)
        try:
            data = self._post("add", **params)
            return ProviderOrderResult(ok=True, provider_order_id=str(data.get("order", "")))
        except ProviderError as e:
            return ProviderOrderResult(ok=False, error_code=e.code, error_message=str(e))

    def get_order_status(self, provider_order_id):
        try:
            data = self._post("status", order=provider_order_id)
            return ProviderStatus(
                status=str(data.get("status", "")),
                start_count=str(data.get("start_count", "")),
                remains=str(data.get("remains", "")),
            )
        except ProviderError as e:
            return ProviderStatus(error_code=e.code)

    def get_multiple_statuses(self, provider_order_ids):
        if not provider_order_ids:
            return {}
        try:
            data = self._post("status", orders=",".join(provider_order_ids[:100]))
            out = {}
            for oid, info in (data.items() if isinstance(data, dict) else []):
                if isinstance(info, dict) and "error" not in info:
                    out[oid] = ProviderStatus(
                        status=str(info.get("status", "")),
                        start_count=str(info.get("start_count", "")),
                        remains=str(info.get("remains", "")),
                    )
                else:
                    out[oid] = ProviderStatus(error_code="UNKNOWN_PROVIDER_ERROR")
            return out
        except ProviderError:
            return {oid: ProviderStatus(error_code="PROVIDER_UNAVAILABLE")
                    for oid in provider_order_ids}

    def create_refill(self, provider_order_id):
        try:
            data = self._post("refill", order=provider_order_id)
            return ProviderOrderResult(ok=True, provider_order_id=str(data.get("refill", "")))
        except ProviderError as e:
            return ProviderOrderResult(ok=False, error_code=e.code, error_message=str(e))

    def get_refill_status(self, provider_refill_id):
        try:
            data = self._post("refill_status", refill=provider_refill_id)
            return str(data.get("status", ""))
        except ProviderError:
            return ""

    def create_cancel(self, provider_order_ids):
        try:
            data = self._post("cancel", orders=",".join(provider_order_ids[:100]))
            return data if isinstance(data, dict) else {}
        except ProviderError as e:
            return {"error": e.code}

    def get_balance(self):
        data = self._post("balance")
        return _to_decimal(data.get("balance", 0))


def _normalize_error(msg: str) -> str:
    m = msg.lower()
    if "api key" in m or "invalid key" in m:
        return "INVALID_API_KEY"
    if "service" in m:
        return "SERVICE_NOT_FOUND"
    if "balance" in m or "funds" in m:
        return "INSUFFICIENT_PROVIDER_BALANCE"
    if "link" in m:
        return "INVALID_LINK"
    if "quantity" in m or "min" in m or "max" in m:
        return "INVALID_QUANTITY"
    return "UNKNOWN_PROVIDER_ERROR"


def get_provider(code: str, api_url: str = "", api_key: str = "") -> BaseProvider:
    if code == "kd1s":
        return KD1SProvider(api_url, api_key)
    from providers.mock import MockProvider
    return MockProvider()
