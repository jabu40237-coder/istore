# Payment Gateway Research — i Store (Iraq)

**Date:** 2026-10-07
**Scope:** Merchant/online-payment API research for the four providers Ali named — ZainCash, FastPay (Iraq), AsiaHawala, FIB (First Iraq Bank). Public documentation only; no accounts, logins, or credentials involved.
**Method:** Public web research (official docs + community SDKs that mirror the official APIs). Every fact is tagged ✅ verified-from-official-docs, 🟡 verified-from-community/third-party-source (not the provider itself), or ❌ could not verify.

> No API keys, secrets, or credentials appear in this document — all integration credentials must be issued directly by each provider after merchant onboarding.

---

## 1. ZainCash (Zain Iraq wallet)

**Official docs:** https://docs.zaincash.iq/ ✅ — Payment Gateway API v2 guide (version 1.0, updated 11 Aug 2026)

### Auth
✅ OAuth2 client-credentials grant. `POST /oauth2/token` with `client_id`, `client_secret`, and a space-separated `scope` string (e.g. `payment:read payment:write reverse:write reverse:read disbursement:write disbursement:read`). All API calls use `Bearer <access_token>`. A separate **API key** is used to verify HMAC-SHA256 signatures on the JWTs returned in redirects/webhooks.

### Payment flow
✅ Hosted redirect flow:
1. Backend gets an access token.
2. `POST /api/v2/payment-gateway/transaction/init` — body: `language` (en/ar/ku), `externalReferenceId` (UUID — serves as your idempotency/reconciliation key), `orderId` (your internal order id), `serviceType`, `amount` `{value, currency}`, `customer.phone` (optional), `redirectUrls` `{successUrl, failureUrl}`.
3. Redirect the customer to the `redirectUrl` from the response (hosted ZainCash page; customer approves with PIN/OTP in their wallet).
4. ZainCash redirects back to `successUrl`/`failureUrl` with `?token=<JWT>`; verify the JWT signature with your API key (HS256).
5. Confirm final status with `GET /api/v2/payment-gateway/transaction/inquiry/{transactionId}`.

✅ Refunds supported: `POST /api/v2/payment-gateway/transaction/reverse` (full) and `/partial-reverse` (partial). ⚠️ The docs warn these endpoints are **not idempotent** — a blind retry of a timed-out call can double-refund; always reconcile with `GET /api/v2/payment-gateway/transaction/reversals` before retrying. A cash-disbursement (payout) API for batch wallet payouts also exists.

### Callback / webhook
✅ `notificationUrl` receives a JSON POST containing a `webhook_token` JWT when a transaction reaches its final status (`eventType: STATUS_CHANGED`). Payload identifies your payment via `transactionId`, `merchantReferenceId` (= your `externalReferenceId`), and `orderId`. Docs say: **the webhook event is the source of truth**; the redirect token is for UX and the inquiry endpoint is a fallback. Use `eventId` for webhook idempotency. ⚠️ Webhooks only work in production — **no webhook testing in the UAT environment**, and the URL must be registered with the ZainCash business team.

### Sandbox
✅ Test environment: `https://pg-api-uat.zaincash.iq`. Production base URL is provided during onboarding; separate credentials per environment. (Older v1 docs also reference `https://test.zaincash.iq/` vs `https://api.zaincash.iq/` — the v2 UAT host is the current one.)

### Currency / fees
✅ `amount.currency` **must be `IQD`** (Iraqi dinar only). ❌ No public fee schedule found — fees are negotiated per merchant.

### Merchant signup
🟡 Per community SDKs documenting the official process: register a wallet type — **Special Wallet** (small businesses/startups, monthly transaction cap), **Corporate Wallet** (larger businesses), or **Government Wallet**. Your website must be submitted to ZainCash first for a security-guideline review. After registration you receive test merchant phone/PIN, test customer phone/PIN, and a private key/JWT secret by email; production credentials follow after the integration is reviewed.

### Iraq-specific notes
✅ ZainCash is Iraq's largest wallet network; the checkout language options explicitly include Kurdish (`ku`). Company must be registered in Iraq (🟡 community sources).

---

## 2. FastPay (Iraq wallet gateway)

**Official docs:** https://developer.fast-pay.iq/website-integration ✅ (merchant onboarding page: https://fast-pay.iq/integration ✅; merchant panel: https://merchant.fast-pay.iq ✅)

### Auth
✅ Simple **store credentials** — no OAuth: `store_id` + `store_password` sent in the body of every API call. Refunds additionally need a separate `refund_secret_key`. Credentials are issued by FastPay and emailed to the merchant (✅ per official docs) after onboarding.

### Payment flow
✅ Hosted redirect flow (3 steps in official docs):
1. **Payment Initiation:** merchant server POSTs store credentials + order data (order_id, cart items, bill amount) to FastPay → receives a redirect URL → sends the customer to FastPay's hosted page.
2. **Payment Verification:** customer pays in the FastPay wallet/app and is redirected back to your `success_url`/`cancel_url`. You must verify the outcome via FastPay's **Transaction Validation API** (authenticating with store_id + store_password + order_id).
3. **Transaction Update:** update your order in your DB from the validated status.
✅ Refunds supported via the merchant panel/API (refund secret key required). 🟡 Community SDKs also document a **QR-vending** mode (generate a payment QR for in-person/POS use) and a **mobile deep-link** mode (`client_uri`) for apps — both reuse the same store credentials.

### Callback / webhook
✅ **Instant Payment Notification (IPN):** FastPay POSTs a notification **only on successful payments** to the IPN URL you configure in the merchant panel (failure notification URL is a separate field). Official docs require merchants to **validate every IPN through the Validation API** (never trust the notification payload alone) and stress configuring the IPN URL because it fires even when the customer doesn't return to your site. ❌ Signature scheme for IPN payloads could not be verified from public docs — the validated pattern (notify → validate → update) is the safe route.

### Sandbox
✅ Staging base URL: `https://staging-apigw-merchant.fast-pay.iq`; Production: `https://apigw-merchant.fast-pay.iq`. 🟡 Whether a self-serve staging merchant account is available before contract signing is unclear — official docs say credentials arrive by email after onboarding; third-party billing software docs note merchants "test on FastPay's staging system."

### Currency / fees
✅ **IQD only** (docs + all community SDKs). 🟡 One third-party source (potan.co) lists **3% per transaction** — not confirmed by FastPay publicly.

### Merchant signup
✅ Official: contact the **FastPay Merchant Acquisition Team**; they guide account setup, and you receive system-generated credentials + API integration guidelines by email. 🟡 Third-party source: fill out a merchant registration form, provide legal/company documents (company must be registered in Iraq), sign a contract — reportedly takes a month or more.

### Iraq-specific notes
✅ Merchant panel at `merchant.fast-pay.iq`; deposit via FastPay scratch cards, withdrawal via agents. Slow support response is a recurring complaint in third-party reviews (🟡, not from FastPay).

---

## 3. AsiaHawala (Asiacell mobile money)

**Official docs:** ❌ **No public developer documentation found.** The only documented claim (🟡, potan.co) is a **SOAP API** once referenced from asiahawala.iq — the link could not be retrieved and no current public spec is indexed anywhere. Other third-party sources (cartdna.com, osoustech.com) acknowledge an API exists but give no endpoints or docs link.

### Auth
❌ Could not verify (no public docs).

### Payment flow
❌ Could not verify. 🟡 Third-party descriptions mention QR payments and in-app purchases, but no confirmed redirect/API pattern.

### Callback / webhook
❌ Could not verify.

### Sandbox
❌ Could not verify. No test environment is publicly documented.

### Currency / fees
🟡 Fees of **1–7% per transaction** reported by potan.co (negotiated per contract) — not confirmed by AsiaHawala. Currency IQD (wallet is dinar-based).

### Merchant signup
🟡 Per potan.co: open an Iraqi bank account, **visit AsiaHawala's main branch in person**, provide company documents (**company must be registered in Iraq**), Face ID identity verification, and sign a commercial contract in an in-person meeting. Reported duration: **a month or more**. There is no self-serve online onboarding.

### Iraq-specific notes
⚠️ Major limitations: wallet accounts are **only available to Asiacell carrier subscribers** (🟡, potan.co); USSD-based (*212#) registration; strongest in northern/central Iraq. One source notes AsiaHawala planned an infrastructure upgrade to a REST API and support for all carriers — 🟡 could not confirm whether this happened.

---

## 4. FIB — First Iraq Bank

**Official docs:** ✅ Developer portal exists: https://fib.iq/en/developers — but it is currently a **landing page** (Magento / iOS / Android / Web Payments SDKs) with **no public API reference on the page itself**. The underlying REST API is well documented by multiple independent community SDKs that mirror the official spec (parakit, laravel-fib, thejano/fib-payment-laravel, kurdi-dev/fib-sdk, iraqpay). Facts below marked 🟡 where they come from those SDKs rather than FIB's own published text.

### Auth
🟡 OAuth2 **client-credentials** grant (Keycloak): `POST /auth/realms/fib-online-shop/protocol/openid-connect/token` → Bearer token used on all API calls. Credentials are a `client_id` + `client_secret` pair **issued by FIB after you submit their integration request form** (🟡).

### Payment flow
🟡 **QR-code driven** (not a hosted redirect page): `POST /protected/v1/payments` (stage: `https://fib.stage.fib.iq`, prod: `https://fib.prod.fib.iq`) with amount, currency, description, optional `callbackUrl`, optional `redirectUri`, optional `expiresIn` (ISO-8601), and a transaction `category` (e.g. ECOMMERCE). Response returns a `paymentId`, a **QR code** (base64), a **human-readable code**, `validUntil`, and deep links that open the payment directly in the FIB personal/business/corporate apps. The customer **pays inside the FIB mobile app**; there is no web checkout page to redirect to. You can optionally redirect the browser to a URL after payment/cancellation (`redirectUri`). 🟡 Refund within a configurable window (default P7D) and cancel of unpaid payments are supported via dedicated endpoints.

### Callback / webhook
🟡 FIB **POSTs** `{ paymentId, status }` to the `callbackUrl` registered per payment (statuses: PAID / UNPAID / DECLINED); server-to-server status inquiry is `GET /protected/v1/payments/{id}/status`. Treat the callback as a trigger and the status endpoint as the authoritative check; restrict to FIB's IPs if they publish them.

### Sandbox
🟡 Stage host `https://fib.stage.fib.iq` exists; credentials are issued by FIB (the laravel-fib docs say "provided by FIB"). ❌ Whether stage credentials are available without an approved merchant application could not be verified.

### Currency / fees
🟡 The API accepts **IQD and USD** (per iraqpay SDK), but parakit's docs assert **FIB settles IQD only** — treat settlement as IQD-only unless FIB confirms otherwise. ❌ No public fee schedule; potan.co reports **1–5% per transaction negotiated in the commercial contract** (unverified by FIB).

### Merchant signup
🟡 Apply through FIB's **integration request form** (client_id/client_secret sent after approval); contact: customer-service@fib.iq, WhatsApp 066 666 6999, call center 066 220 6977 (✅ from fib.iq). Potan.co reports an in-app flow: passport scan + Face ID, then upload company documents (**company must be registered in Iraq**) and an in-person commercial contract, taking a week or more — 🟡 could not verify against FIB's current process.

### Iraq-specific notes
✅ FIB is a fully licensed Iraqi digital bank (CBI-licensed per third parties); onboarding is entirely mobile (account opens with ID/passport in minutes). ⚠️ Its payer base is smaller than ZainCash/FastPay (potan.co estimated ~7,000 users in 2024; current numbers ❌ unverified) — customers must have the FIB app installed to pay, so it covers fewer buyers than the big wallets.

---

## Comparison table

| Criterion | ZainCash ✅ best-documented | FastPay ✅ simplest auth | AsiaHawala ⚠️ weakest docs | FIB 🟡 modern but QR-only |
|---|---|---|---|---|
| Official docs URL | https://docs.zaincash.iq/ | https://developer.fast-pay.iq/website-integration | none found | https://fib.iq/en/developers (landing only) |
| Auth | OAuth2 client_credentials + scopes; API key for JWT verify | `store_id` + `store_password` per request | ❌ unknown | 🟡 OAuth2 client_credentials (Keycloak) |
| Flow | Hosted redirect (OTP/PIN in wallet) | Hosted redirect + Validation API | ❌ unknown | QR code / app deep link (no web page) |
| Server confirmation | Webhook JWT (source of truth) + inquiry fallback | IPN (success-only) + Validation API | ❌ unknown | 🟡 POST {paymentId,status} + status endpoint |
| Refunds | ✅ full + partial | ✅ (refund secret key) | ❌ unknown | 🟡 within refundable window |
| Sandbox / staging | ✅ UAT host; webhooks prod-only | ✅ staging host | ❌ none documented | 🟡 stage host (credential access unverified) |
| Currency | IQD only ✅ | IQD only ✅ | IQD (🟡) | API IQD/USD (🟡); settlement IQD (🟡) |
| Fees (public) | ❌ negotiated | 🟡 ~3% (third-party) | 🟡 1–7% (third-party) | 🟡 1–5% (third-party) |
| Merchant signup | Wallet registration + site security review | Merchant Acquisition Team → form + docs + contract | In-person branch visit + contract | Integration request form / in-app + in-person contract |
| Company must be Iraqi-registered | 🟡 yes | 🟡 yes | 🟡 yes | 🟡 yes |
| Payer reach | Largest wallet network in Iraq | Large | Asiacell subscribers only | Smallest (FIB app users) |
| Flask-friendliness | High (plain REST + JWT verify) | High (plain REST) | ❌ not feasible without docs | Medium (needs QR display UI) |

Legend: ✅ verified from official docs · 🟡 verified from credible third-party/community source, not the provider · ❌ could not verify.

---

## Recommendation

**Integrate ZainCash first.** It has the best public documentation of the four (complete v2 API reference with endpoints, webhook semantics, error codes, and a UAT host), OAuth2, server-side webhooks with signed JWTs, full and partial refunds, IQD-native, and the largest Iraqi payer base. Implementation in Flask is straightforward: `requests` for token + init, redirect to `redirectUrl`, a return route that verifies the JWT with HS256, and a webhook route that treats the signed event as truth (with the inquiry endpoint as fallback).

**FastPay as second choice.** Its simpler store_id/store_password auth and clean 3-step redirect + validation flow are genuinely easy to implement in Flask, but official docs are thinner (no public reference of the actual API endpoints beyond the portal overview — endpoint shapes come from community SDKs), IPN fires on success only, and there's no publicly confirmable fee or self-serve sandbox.

**FIB is a reasonable third option** if Ali wants QR/app-based payments too, but only after confirming the stage credentials and the official API spec with FIB directly — the community SDKs agree on the shape, yet FIB publishes no public endpoint reference itself. Note the product difference: the customer must leave the store to pay inside the FIB app, so checkout completion depends on app adoption.

**De-prioritize AsiaHawala for now.** No public API docs (outdated SOAP at best), in-person-only merchant onboarding, and an Asiacell-subscriber-only wallet base. Revisit only if AsiaHawala publishes a REST API and self-serve merchant onboarding.

### What Ali must do next — per provider

- **ZainCash:** Register a merchant wallet (Special Wallet fits a small store) at ZainCash, pass their website security review, collect the UAT `client_id`/`client_secret`/API key, and ask the business team to register the production `notificationUrl` once live (webhooks don't work on UAT — design the Flask order-status flow around the inquiry endpoint during testing).
- **FastPay:** Contact the Merchant Acquisition Team (fast-pay.iq/integration), complete the registration form + company documents, sign the contract, receive store_id/store_password (+ refund secret key) by email, and set success/cancel/IPN URLs in the merchant panel.
- **FIB:** Submit FIB's integration request form (or contact customer-service@fib.iq / WhatsApp 066 666 6999) and ask for (a) the official API reference, (b) stage `client_id`/`client_secret` for testing, (c) the fee schedule, and (d) the confirmed list of callback IP addresses.
- **AsiaHawala:** Ask directly whether a current REST merchant API exists and how to get documentation and sandbox credentials before investing any effort; without that, don't build against it.

### Notes for the i Store implementation (all providers)
- All four require an **Iraq-registered company** (third-party reports, not confirmed by the providers themselves) — budget ~1 month for onboarding everywhere except possibly FIB.
- Every provider is IQD-first; keep the store's pricing engine in IQD or convert USD→IQD at checkout time.
- Webhooks/IPNs need a publicly reachable HTTPS endpoint — the current Flask preview on GitHub Codespaces is fine for testing redirects, but production needs the real host.
- Credential storage: put every secret (client secrets, store passwords, API keys) in the Secure Vault / env vars only — never in code, logs, or chat.

## Sources
- ZainCash Payment Gateway API v2 — https://docs.zaincash.iq/
- FastPay Developer Portal (website integration) — https://developer.fast-pay.iq/website-integration
- FastPay integration/onboarding — https://fast-pay.iq/integration
- FIB developer portal — https://fib.iq/en/developers
- Potan Co, "Local Payment Gateways in Iraq" — https://potan.co/en/blog/local-payment-gateways-in-iraq
- parakit (Laravel gateway kit for Iraq/Kurdistan) — https://github.com/ShahramMebashar/parakit
- laravel-fib — https://github.com/nizaamomer/laravel-fib
- laravel-fib-payment — https://github.com/Hamoi1/laravel-fib-payment
- fib-payment-laravel — https://github.com/thejano/fib-payment-laravel
- laravel-fastpay — https://github.com/nizaamomer/laravel-fastpay
- iraqpay SDK — https://github.com/balghanimi/iraqpay
