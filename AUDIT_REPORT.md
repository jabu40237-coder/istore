# i Store | ئایستۆر — Code Audit Report
**Date:** 2026-10-06 · **Scope:** read-only audit of `~/workspace/istore/` · **No files modified**

---

## 1. Route Inventory

### Public routes (`app.py`)
| Route | Methods | Auth | Handler |
|---|---|---|---|
| `/` | GET | none | `index()` — real DB stats (active services, completed orders, user count) + featured services |
| `/services` | GET | none | `services()` — search by name (`ilike`), filter by platform, limit 200 |
| `/service/<int:sid>` | GET | none | `service_detail()` — 404 if not active |
| `/about`, `/faq`, `/contact`, `/terms`, `/privacy` | GET | none | static templates |
| `/health` | GET | none | `{"ok": true, "env": ...}` |
| `/login` | GET/POST | none (rate-limited 10/15min per IP) | form login |
| `/register` | GET/POST | none | creates user, auto-login |
| `/logout` | GET | none | clears session |

### User dashboard (`blueprints/user.py`, prefix `/dashboard`, all `@login_required`)
| Route | Methods | Notes |
|---|---|---|
| `/dashboard/` | GET | balance, order counts, recent 5 orders, unread notifications |
| `/dashboard/order` | GET/POST | new order form; **CSRF token checked here only** (`user.py:76`) |
| `/dashboard/orders` | GET | order list, `?status=` filter |
| `/dashboard/orders/<oid>` | GET | detail, scoped to `user_id` (404 otherwise) |
| `/dashboard/orders/<oid>/refill` | POST | only if `svc.supports_refill` and provider_order_id set |
| `/dashboard/orders/<oid>/cancel` | POST | only if `svc.supports_cancel`; **marks CANCELED + full refund without verifying provider cancel result** |
| `/dashboard/wallet` | GET | balance + last 50 transactions |
| `/dashboard/notifications` | GET | list |
| `/dashboard/notifications/read` | POST | mark all read |
| `/dashboard/support` | GET/POST | ticket list + create |
| `/dashboard/support/<tid>` | GET/POST | ticket thread, scoped to owner |
| `/dashboard/profile` | GET/POST | name/phone/language/currency update |

### Admin (`blueprints/admin.py`, prefix `/admin`, all `@admin_required` → 403 otherwise)
| Route | Methods | Notes |
|---|---|---|
| `/admin/` | GET | real aggregate stats (users, orders, revenue, costs, profit, pending, failed, active services) + 14-day order chart (Python-bucketed) + providers |
| `/admin/services` | GET | search, limit 200 |
| `/admin/services/<sid>` | GET/POST | edit: custom price, featured, status, sort_order |
| `/admin/services/sync` | POST | triggers `sync_provider()` |
| `/admin/orders` | GET | `?status=` filter, limit 200 |
| `/admin/orders/<oid>/refund` | POST | full refund + status REFUNDED |
| `/admin/users` | GET | list, limit 200 |
| `/admin/users/<uid>/adjust` | POST | **manual wallet credit/debit** (`manual_adjustment` ledger entry) |
| `/admin/providers` | GET | list |
| `/admin/providers/<pid>/test` | POST | health check → JSON, logged to `provider_health_logs` |
| `/admin/providers/<pid>/balance` | GET | live provider balance → JSON |
| `/admin/settings` | GET/POST | site_name, accent_color, usd_to_iqd, maintenance_mode, kd1s_api_url, sync_interval; `kd1s_api_key` write-only (never displayed back) |
| `/admin/pricing` | GET/POST | global markup %; recomputes all non-custom prices |

### Internal API (`blueprints/api.py`, prefix `/api`, **no auth**)
| Route | Notes |
|---|---|
| `/api/services` | public JSON catalog (id, name, platform_id, category_id, price, min/max, refill/cancel, type, featured) |
| `/api/service/<sid>/price?quantity=` | live total calculator |

**REAL:** all routes above exist and are wired to real DB queries. **MOCK/PLACEHOLDER:** none in routing. **MISSING:** admin ticket management (no admin view/reply for support tickets); admin user detail/suspend/activate; admin refill-status view; public reseller API (intentionally out of scope per docstring).

---

## 2. Database Schema (`models.py`, `db.py`)

Engine: SQLAlchemy, `DATABASE_URL` env (PostgreSQL via `postgresql+psycopg2`, SQLite fallback). Money = `Numeric(18,6)`, never float. `Base.metadata.create_all` on boot (no Alembic migrations — schema changes require manual handling).

| Table | Key fields | Notes |
|---|---|---|
| `roles` | name (SUPER_ADMIN/ADMIN/SUPPORT/CUSTOMER) | seeded by `ensure_roles()` |
| `users` | email/username unique+indexed, password_hash, language, currency, is_active, telegram_id (indexed, unused by app) | |
| `user_roles` | user_id+role_id unique | |
| `providers` | code unique (kd1s/mock), api_url, is_active, last_sync_at, last_error | name "KD1S" stored but never rendered to customers |
| `platforms` | code unique, name, icon, color, sort_order | |
| `categories` | platform_id, name, is_hidden | |
| `services` | **id (public i Store ID)**; `provider_id` + `provider_service_id` unique (internal, comment: "NEVER exposed publicly"); provider_rate, selling_price_usd, custom_price_usd, min/max, supports_refill/cancel, status (active/inactive/provider_unavailable), is_featured, flags JSON | provider_service_id never appears in any customer template (verified) |
| `pricing_rules` | scope (global/platform/category/service), scope_id, markup_percent, markup_fixed_usd | |
| `wallets` | user_id unique, balance_usd | |
| `transactions` | wallet_id, **type** (deposit/order_charge/refund/manual_adjustment/bonus/fee), **amount_usd signed**, balance_after_usd, currency, **exchange_rate** (stored per-tx), reference, note, created_by | immutable ledger (no update/delete paths in code) |
| `orders` | user_id, service_id, provider_id, **provider_order_id** (internal), **idempotency_key unique**, quantity, link, **input_data JSON** (extra fields), provider_cost_usd, customer_charge_usd, profit_usd, **currency + exchange_rate (purchase-time rate)**, status, start_count, remains | |
| `order_events` | order_id, event, old/new status, meta JSON | audit trail per order |
| `refills` | order_id, provider_refill_id, status | created on refill; **status never polled** |
| `notifications` | user_id, type, title, body, is_read | in-app only |
| `tickets` / `ticket_messages` | user/ticket threading, is_staff, internal_note | no admin-side routes |
| `audit_logs` | actor_id, action, target, meta, ip, user_agent | written on admin actions + logins |
| `system_settings` | key unique, value, is_secret | kd1s_api_key stored with is_secret=True |
| `sync_logs` | provider_id, found/added/updated/disabled/errors, duration | |
| `provider_health_logs` | provider_id, ok, latency_ms, error | |
| `jobs` | name, payload, status, run_after | **defined but never used** (worker uses in-process schedule, not the table) |

**REAL:** full commerce schema exists; public `services.id` vs internal `provider_service_id` separation is correctly modeled and respected. **MOCK/PLACEHOLDER:** `jobs` table unused. **MISSING:** favorites, comparison, recently-viewed (no models); no migration system.

---

## 3. Environment Variables (`config.py`)

| Var | Default | Purpose |
|---|---|---|
| `SESSION_SECRET` | `"dev-secret-change-me"` | Flask secret key |
| `DATABASE_URL` | `sqlite:////home/hatch/workspace/istore/istore.db` | DB connection |
| `APP_ENV` | `development` | enables Flask debug when `development` |
| `KD1S_API_KEY` | `""` | provider key (also readable from `system_settings.kd1s_api_key`) |
| `KD1S_API_URL` | `https://kd1s.com/api/v2` | provider endpoint |
| `PROVIDER_MODE` | `mock` | `mock` \| `kd1s` |
| `TELEGRAM_BOT_TOKEN` | `""` | bot token |
| `TELEGRAM_ADMIN_IDS` | `""` | comma-separated Telegram IDs |
| `DEFAULT_CURRENCY` | `USD` | |
| `USD_TO_IQD` | `1500` | Decimal; also mirrored to `system_settings.usd_to_iqd` |
| `SYNC_INTERVAL_MINUTES` | `60` | worker service sync cadence |
| `ORDER_POLL_INTERVAL_MINUTES` | `5` | worker status sync cadence |
| `RATELIMIT_LOGIN` | `10` | login attempts per 15 min per IP |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | (read directly via `os.environ` in `app.py:_bootstrap_admin`, not via Config) | first-run admin bootstrap |

**REAL:** all vars are actually read and used. **MOCK/PLACEHOLDER:** none. **MISSING:** no `REDIS_URL`/cache vars; no payment-gateway vars (correctly absent — no fake payments).

---

## 4. Authentication (`services/auth.py`)

- **Hashing:** bcrypt (`gensalt`), verified real.
- **Sessions:** Flask cookie sessions, `SESSION_COOKIE_HTTPONLY=True`, `SameSite=Lax`; `SECRET_KEY` from env. `login_user()` clears session (fixation-safe) and stores `user_id` + per-session `csrf` token.
- **Roles:** `user_roles()` reads `_role_names` attached in `current_user()` (loaded before expunge — the earlier detachment bug is fixed). `login_required` → redirect to login; `admin_required` → 403 unless SUPER_ADMIN/ADMIN.
- **Rate limiting:** in-memory dict, 10 login attempts / 15 min / IP (`app.py` login POST only). Resets on process restart; no register-rate-limit.
- **Weaknesses:**
  1. `SECRET_KEY` falls back to `"dev-secret-change-me"` if env missing — sessions forgeable on misconfigured deploys.
  2. No `SESSION_COOKIE_SECURE` flag (fine for http preview, must be set on https production).
  3. Rate limiter is per-process memory — ineffective behind multiple gunicorn workers (each worker has its own counter) and resets on restart.
  4. No email verification flow (`email_verified` column exists but nothing sets it).
  5. `totp_secret` column exists for admin 2FA but **no 2FA implementation**.
  6. Registration has no CAPTCHA/rate limit.

**REAL:** bcrypt, session handling, RBAC decorators. **MOCK/PLACEHOLDER:** `email_verified`, `totp_secret` columns with no logic. **MISSING:** 2FA, email verification, secure-cookie flag, shared rate-limit store.

---

## 5. Admin Implementation (`blueprints/admin.py` + `templates/admin/`)

Pages: dashboard, services, service_edit, orders, users, providers, settings, pricing. Base template `base_admin.html` is separate from customer UI.

- **Dashboard** (`admin/index.html`): all numbers from live aggregate queries (revenue/costs/profit summed from `orders`), 14-day order bar chart from real data, provider list. **Zero when empty — no fake stats.**
- **Services** (`admin/services.html`): real search/list; edit page allows custom price override, featured flag, status, sort order; price recompute via pricing engine; changes audit-logged.
- **Orders** (`admin/orders.html`): real list + status filter; refund button issues ledger `refund` + sets REFUNDED.
- **Users** (`admin/users.html`): list only + **manual wallet adjust form** (any signed amount → `manual_adjustment` transaction). **No suspend/activate, no per-user detail/orders/wallet view.**
- **Providers** (`admin/providers.html`): Test Connection + Check Balance buttons calling live provider API via fetch; results shown inline.
- **Settings** (`admin/settings.html`): site name, accent color, USD→IQD rate, maintenance mode, KD1S URL, sync interval, KD1S key (write-only). Also shows last 10 sync logs + 30 audit entries.
- **Pricing** (`admin/pricing.html`): global markup %; on save recomputes every non-custom selling price.

**REAL:** everything displayed comes from live queries; provider test/balance hit the real API. **MOCK/PLACEHOLDER:** none observed. **MISSING:** ticket management UI, user suspend/activate + detail view, per-platform/category pricing rules UI (model supports it, UI only exposes global), refill-status tracking, social-links settings, Telegram settings.

---

## 6. Telegram (`telegram_bot.py`, 150 lines)

- **Exists:** yes — standalone long-polling script (`python telegram_bot.py`), needs `TELEGRAM_BOT_TOKEN` env.
- **Actually implemented commands:** `/start`, `/balance`, `/orders` (last 5), `/services` (first 10), `/admin`, `/stats` (admin-only, role-checked server-side). Everything else falls through to a help string.
- **Documented but NOT implemented:** `/order`, `/support`, `/language`, `/help` (docstring claims them; code has no handlers).
- **DB wiring:** real — reads `users` (by `telegram_id`), `wallets`, `orders`, `services` from the same database.
- **Broken onboarding:** `/start` tells unlinked users to register on the website, but **nothing ever sets `User.telegram_id`** — no `/link` command, no website UI, no deep-link token. So for any real user the bot replies "no account found" forever.
- **Not wired to app events:** order creation writes in-app `Notification` rows only; **nothing pushes Telegram messages** for order created/completed/refund/support reply. No notification preferences.
- **No Telegram login** on the website.

**REAL:** polling loop, `/balance`, `/orders`, `/services`, admin stats — all reading the real DB. **MOCK/PLACEHOLDER:** `/order`, `/support`, `/language` advertised but absent. **MISSING:** account linking (critical — bot unusable without it), proactive notifications, Telegram login, webhook mode, token admin UI (only env var).

---

## 7. KD1S Integration (`providers/kd1s.py`, `providers/base.py`)

- **Endpoint:** `POST https://kd1s.com/api/v2` (configurable via `KD1S_API_URL`), 25s timeout, httpx with `trust_env=False`.
- **Key transport:** in POST body as `key` — never in URL, never logged (payload is key-masked before any logging path). Correct per SMM-panel convention.
- **Actions implemented:** `services`, `add`, `status` (single + batch `orders=`), `refill`, `refill_status`, `cancel`, `balance` — **all seven** the user asked about. Plus `health_check()` via balance call, 3-attempt exponential backoff, 429 handling, error normalization (`INVALID_API_KEY`, `INSUFFICIENT_PROVIDER_BALANCE`, etc.).
- **Order params:** `create_order(service_id, link, quantity, extra={})` — `extra` dict passes through any additional fields (comments/usernames/etc.) to the provider. Real.
- **Branding leak check:** `grep -ri kd1s templates/` → only `admin/settings.html` (admin-only). **Zero KD1S references in customer templates.** Provider name "KD1S" lives in DB `providers` row (internal).
- **Live verification status:** code-verified; live balance ($0.91) and 4,314-service sync were verified 2026-10-06 per ops history, not re-tested in this audit.

**REAL:** complete 7-action client, correct key handling. **MOCK/PLACEHOLDER:** none. **MISSING:** `get_refill_status` is implemented but never called by app/worker; no `dripfeed` action (not requested).

---

## 8. Service Sync (`services/sync.py`)

- New provider services → inserted; changed (name/rate/min/max/type/refill/cancel) → updated, selling price recomputed via pricing engine (custom-price override respected).
- Missing locally-active services → `status="provider_unavailable"` (**never deleted** — history preserved).
- Services that return → reactivated to `active`.
- Platform detection by keyword (`instagram/tiktok/youtube/telegram/facebook/twitter/x/other`); category from provider category string.
- Every run logged to `sync_logs` (found/added/updated/disabled/errors/duration); provider `last_sync_at`/`last_error` updated.
- Trigger: admin "Sync Now" button + worker every `SYNC_INTERVAL_MINUTES` (60).

**REAL:** full sync engine as specified. **MOCK/PLACEHOLDER:** none. **MISSING:** dry-run/preview mode; per-service sync exclusion; `description` field is never populated from provider (provider API has no description — correctly left as-is, template falls back to name).

---

## 9. Order Creation (`services/orders.py`)

Flow: validate → idempotency check → **reserve** (wallet `order_charge`, negative) → commit → provider `add` → on success: store `provider_order_id`, status PROCESSING; on failure: **auto-refund** (`refund` tx), status FAILED. Matches the required reserve→submit→finalize with release-on-failure.

- **Idempotency:** SHA-256 of `user_id|service_id|link|quantity|input_data` (32 hex chars), unique DB constraint; duplicate returns existing order. Protects double-click/refresh/retry.
- **Validation** (`validate_order_input`): min/max enforced; link must start with `http` except subscription/follower types. **Sloppy but functional:** `needs_link = "comment" not in stype or True` is always `True` (dead condition).
- **Order fields:** link, quantity, `input_data` JSON (collects `x_*` form fields). **Form is FIXED** (`dashboard/order.html`): link + quantity + live total + *only* a comments textarea when service_type contains "comment". **No dynamic per-type engine** — no username/min/max/posts/delay/expiry/runs/interval fields, no schema-driven rendering.
- **Status sync:** `sync_order_statuses()` batch-polls PENDING/PROCESSING/PARTIAL via `get_multiple_statuses`, maps provider statuses, records `start_count`/`remains`, creates in-app notifications, auto-refunds unfulfilled portion on PARTIAL.
- **Cancel flow (user.py):** calls provider `create_cancel` but **ignores the result**, immediately marks CANCELED + refunds FULL charge — can refund without confirmed provider cancellation (known risk).
- **No RECONCILIATION_REQUIRED state** exists anywhere in code.

**REAL:** reserve/finalize/refund state machine, idempotency, batch status sync, partial refunds. **MOCK/PLACEHOLDER:** none. **MISSING:** dynamic service-type form engine; reconciliation state; cancel-result verification; refill-status polling.

---

## 10. Wallet (`services/wallet.py`)

- **Ledger:** every change goes through `apply_transaction()` → creates immutable `Transaction` row with signed amount + `balance_after_usd`. No code path mutates balance directly (verified: only `apply_transaction` writes `balance_usd`).
- **Types used:** `deposit`, `order_charge`, `refund`, `manual_adjustment`, `bonus` (model supports `fee` too).
- **Safety:** raises `ValueError("insufficient_balance")` if a transaction would take balance negative; order flow catches it → "insufficient_balance" error, no order created.
- **Exchange rate:** stored per transaction (`exchange_rate`); order stores purchase-time rate — historical orders not recalculated.
- **Admin manual credit:** yes — `/admin/users/<uid>/adjust` (any signed Decimal, reason required, audit-logged).
- **User deposit flow:** **none** — no payment methods, no "coming soon" page; wallet page shows balance + history only.

**REAL:** complete ledger-backed wallet. **MOCK/PLACEHOLDER:** none. **MISSING:** user-facing deposit/top-up UI (intentionally deferred per spec — but no "coming soon" placeholder exists either).

---

## 11. Social Media Links

- `grep` across all templates for `t.me`, `telegram.me`, `instagram.com`, `tiktok.com`, `facebook.com`, `youtube.com`, `x.com`, `whatsapp`: **zero hits**.
- No `social` keys in `SystemSetting` defaults, no social fields in admin settings, no model for social links.
- Footer (`base.html`) has brand + terms/privacy/contact links only.

**REAL:** nothing — correctly absent rather than faked. **MOCK/PLACEHOLDER:** none. **MISSING:** entire social-links system (admin-configurable URLs + toggles + footer/contact/support/about display + SVG icons).

---

## 12. Mobile / Responsive

- **Service DETAILS page:** exists (`/service/<sid>` → `service_detail.html`) — shows name, description (falls back to name; provider gives no descriptions), min/max, price/1k, refill/cancel badges, flags, order/login CTA. **Missing vs requested:** no platform/category/type display, no "i Store Service #id", USD-only price, no IQD toggle, no dynamic order form, no requirements/notes sections.
- **Service cards** (`services.html`): name, min/max, refill/cancel badges, "from $X/1k", order button. Clean, not overloaded. Search (name only) + platform filter dropdown. **Missing:** category filter, price filter, sorting, favorites, comparison.
- **Bottom nav** (`dashboard/base_dash.html:17`): exists on dashboard pages — 5 emoji icons (📊➕📦💰👤). **Emoji-as-icons confirmed** in `base_dash.html` (7 uses) and `admin/base_admin.html` (📊📦). User explicitly requested SVG icons.
- **Header** (`base.html`): brand, nav links, **KU/AR/EN language switcher**, login/register or dashboard/logout, hamburger toggle. **No currency selector in header** (currency only in profile page).
- **Footer:** brand, footer_desc, terms/privacy/contact, copyright. No social icons, no quick-links services/about/support.
- **Responsive CSS:** `static/css/style.css` exists; grid classes (`grid-3`), viewport meta present. (Visual quality not re-verified in this audit.)

**REAL:** detail page, cards, bottom nav, i18n header. **MOCK/PLACEHOLDER:** none. **MISSING:** SVG icons (emoji used), header currency selector, footer social/quick links, filters/sorting/favorites, dynamic form.

---

## 13. Fake Content

- `grep -riE "testimonial|review|happy customer|trusted by|5 star"` in templates: **zero hits**. No fake testimonials, no invented customer names, no fake revenue claims.
- Homepage stats (`app.py` `index()`): **real counts** from DB (active services, completed orders, users). Shows 0 when empty — honest.
- Admin dashboard: real aggregates, 0 when empty.
- No invented delivery times or guarantees found in templates.

**REAL:** (honest — nothing fake to report). **MOCK/PLACEHOLDER:** none found. **MISSING:** nothing — this section is clean.

---

## 14. Security

| Check | Result |
|---|---|
| Secrets in code | **Clean** — no hardcoded keys; `grep` for key patterns found nothing; `.env` not in repo listing (gitignored) |
| SQL injection | **Clean** — zero raw SQL; all queries via SQLAlchemy ORM with bound params |
| XSS | **Clean** — no `\|safe` in any template; Jinja2 autoescaping active; security headers set (`nosniff`, `DENY`, referrer-policy) |
| CSRF | **WEAK** — token checked on exactly **one** route: `POST /dashboard/order`. **Missing on:** refill, cancel, ticket create/reply, profile update, notifications/read, login, register, and **all admin POST routes** (sync, refund, user_adjust, service_edit, settings, pricing) |
| Rate limiting | login only (10/15min/IP, in-memory per worker — ineffective across 2+ gunicorn workers); nothing on register, order creation, or API |
| RBAC | solid — `admin_required` on all `/admin/*` (403 otherwise); order/ticket/wallet routes scoped to `user_id`; `current_user()` filters `is_active` |
| AuthZ gaps | `/api/*` has no auth (public catalog/prices — low risk, prices are public anyway) |
| Session | HttpOnly + SameSite=Lax; **no Secure flag**; default `dev-secret-change-me` fallback is dangerous if env unset |
| Passwords | bcrypt, good |
| Provider key | server-side only, masked in logs, write-only admin field — good |
| 2FA / email verify | columns exist, **no implementation** |
| Cancel/refund logic | marks CANCELED + full refund **without confirming provider cancellation** — financial logic risk (flagged in §9) |
| `maintenance.html` | exists and is enforced in `before_request` |

**REAL:** ORM-only queries, bcrypt, RBAC, key hygiene. **MOCK/PLACEHOLDER:** `totp_secret`/`email_verified` dead columns. **MISSING:** CSRF on 10+ state-changing routes, Secure cookie flag, shared rate limiter, 2FA, cancel verification.

---

## Supplementary

**`worker.py`** — REAL: infinite-loop background worker with 3 scheduled jobs (service_sync @60min, order_status_sync @5min, provider_health @15min), uses `system_settings` overrides. NOTE: `jobs` DB table exists but worker does **not** use it (in-process `_last_run` dict instead). Single free web service can't run it on most free hosts.

**`test_acceptance.py`** — REAL script (not pytest): covers mock-provider sync, user creation, wallet deposit, order placement, provider order ID, charge/cost/profit math, ledger entries, idempotent duplicate, status polling. Covers the commerce loop on **mock only** — no live KD1S test, no wallet-concurrency test, no auth/RBAC test.

**Locales** — `ku.json`/`ar.json`/`en.json`: **164 keys each**, parity confirmed.

---

## Verdict Summary

| # | Area | Real | Mock/Placeholder | Missing |
|---|---|---|---|---|
| 1 | Routes | all wired to real queries | — | admin tickets, user suspend |
| 2 | Schema | full commerce model; public vs provider IDs separated | `jobs` table unused | favorites/compare |
| 3 | Env vars | all 14 read & used | — | — |
| 4 | Auth | bcrypt, sessions, RBAC | `totp_secret`, `email_verified` dead | 2FA, Secure flag, shared rate limit |
| 5 | Admin | real stats/services/orders/pricing/providers | — | ticket UI, suspend, social/Telegram settings |
| 6 | Telegram | polling bot, real DB reads | `/order` `/support` `/language` advertised, absent | **account linking (bot unusable)**, notifications push |
| 7 | KD1S | all 7 actions, correct key handling, no customer leak | — | refill-status polling |
| 8 | Sync | full engine, never-deletes, reactivation | — | dry-run |
| 9 | Orders | reserve→provider→finalize/refund, idempotency, partial refunds | — | **dynamic form engine**, reconciliation state, cancel verification |
| 10 | Wallet | immutable ledger, per-tx FX rate, admin credit | — | deposit UI (deferred by design) |
| 11 | Social links | — | — | **entire system** |
| 12 | Mobile UI | detail page, cards, bottom nav, i18n | emoji icons | SVG icons, header currency, filters/sort |
| 13 | Fake content | clean — zero found | — | — |
| 14 | Security | ORM-only, bcrypt, RBAC, key hygiene | — | **CSRF on 10+ routes**, Secure flag, cancel verification |
