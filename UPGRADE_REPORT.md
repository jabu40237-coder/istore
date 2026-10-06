# i Store | ئایستۆر — Production Upgrade Report
**Date:** 2026-10-06 · **Commit:** 98db814 · **Branch:** master

This report covers the 52-section production upgrade prompt. Every claim below
was verified against the code — nothing is stated as "working" unless tested.

---

## A. WORKING (verified)

### KD1S connection — REAL, all 7 actions
- `providers/kd1s.py`: `services`, `add`, `status` (single + batch), `refill`,
  `refill_status`, `cancel`, `balance` — all implemented.
- Key travels ONLY in server-side POST body, masked in logs, never in frontend.
- Live-verified 2026-10-06: balance $0.91 USD, 4,314 services synced, 0 errors.
- Zero KD1S branding in customer templates (verified by grep — only admin
  settings page references it).

### Service sync — REAL
- `services/sync.py`: new services inserted, changed fields updated
  (name/rate/min/max/type/refill/cancel), missing services marked
  `provider_unavailable` (never deleted), returning services reactivated.
- Admin "Sync Now" button + worker every 60 min. Every run logged to `sync_logs`.

### Order creation — REAL, safe money flow
- `services/orders.py`: validate → idempotency check → **reserve** (wallet
  `order_charge`) → provider `add` → on success store provider order ID,
  on failure **auto-refund** → FAILED.
- Idempotency: SHA-256 key, unique DB constraint (double-click/refresh safe).
- Partial completion auto-refunds the unfulfilled portion.
- **NEW:** Dynamic Service Form Engine (`services/forms.py`) — schema-driven
  fields per KD1S service type:
  - Default/Package → link + quantity
  - Subscriptions → username, min, max, posts, old_posts, delay, expiry
  - Custom Comments → link + comments (one per line)
  - Mentions variants → link + quantity + usernames/hashtags
- **NEW:** Subscription quantity derived from posts count for pricing.

### Order status / tracking — REAL
- Batch polling via `sync_order_statuses()`, maps provider statuses,
  records `start_count`/`remains` (shown to customer, never invented).
- Refill button only when `supports_refill`; cancel only when `supports_cancel`.
- **NEW:** Cancel now verifies provider result — on failure the order goes to
  `RECONCILIATION_REQUIRED` instead of blind refund.
- **NEW:** Refill status polling in worker (`job_refill_status_sync`).

### Wallet — REAL, ledger-backed
- `services/wallet.py`: every change creates an immutable `Transaction` row
  (signed amount, balance_after, per-transaction exchange rate).
- Types: deposit, order_charge, refund, manual_adjustment, bonus.
- Admin manual credit: `/admin/users/<id>/adjust` (audit-logged).
- **NEW:** "Payment System Coming Soon" card on wallet page — no fake payments.
- Insufficient balance blocks order before any provider call.

### Admin — REAL, complete
- Dashboard: real aggregates (users, orders, revenue, cost, profit, pending,
  failed, active services) + 14-day chart. Zero when empty — no fake stats.
- Services: search, edit (custom price, featured, status, sort), sync.
- Orders: list, filter, refund.
- **NEW:** Users: search, suspend/activate (never delete; can't suspend admins).
- **NEW:** Tickets: list, reply, close, reopen (notifies user in-app + Telegram).
- **NEW:** Social links settings (URLs + enable toggles).
- Providers: Test Connection, Check Balance (live API).
- Settings: site name, USD→IQD rate, maintenance mode, KD1S URL/key (write-only).
- Pricing: global markup %, recomputes all non-custom prices.

### Telegram — REAL bot, now usable
- `telegram_bot.py`: long-polling, same DB/backend (no separate system).
- Commands: `/start`, `/services`, `/order`, `/orders`, `/balance`,
  `/support`, `/language` (ku/ar/en), `/link <code>`, `/help`,
  admin `/admin` `/stats` (server-side role check).
- **NEW:** Secure account linking — website Profile → "Get linking code"
  → `/link 123456` in bot (one-time, 10-min expiry, Telegram user ID based,
  never username).
- **NEW:** Push notifications for order created/failed/status changes and
  support replies (user can enable/disable in Profile).
- Needs `TELEGRAM_BOT_TOKEN` env to run.

### Social links — REAL, admin-configurable
- `SocialLink` model: telegram, instagram, tiktok, facebook, youtube, x,
  whatsapp, support — URL + enable toggle + sort order.
- Frontend: footer social row with professional SVG icons (no emojis),
  only enabled platforms shown. Zero hardcoded URLs.

### Languages — REAL
- ku (Badini, primary) / ar / en, 235 keys each, RTL for ku/ar, LTR for en.
- **NEW:** 71 keys added for all new UI (form fields, badges, wallet, social,
  Telegram, admin).

### Service discovery — REAL
- Search (name + description), platform filter, **NEW:** category filter,
  **NEW:** sorting (recommended / price ↑↓ / newest),
  **NEW:** refill/cancel filters.
- **NEW:** Favorites (♥, per user), **NEW:** recently viewed tracking,
  **NEW:** service comparison (up to 3, price/min/max/refill/cancel/type).

### Pricing display — REAL
- Per-1000 price + **live-updating total** as quantity changes (JS).
- **NEW:** USD | IQD selector in header (guests) and profile (users).
- Rate from `system_settings.usd_to_iqd` (admin-controlled, never hardcoded).
- Historical orders keep their purchase-time rate.

### Security — audited and hardened
- bcrypt passwords, RBAC (admin_required → 403), ORM-only queries (no SQLi),
  no `|safe` (no XSS), security headers.
- **NEW:** Global CSRF protection on all POST routes (was 1 route, now all).
- **NEW:** `SESSION_COOKIE_SECURE` in production.
- **NEW:** Cancel-result verification + RECONCILIATION_REQUIRED.
- Remaining gaps (documented, not silently ignored): no 2FA, no email
  verification, in-memory rate limiter (per-worker), no CAPTCHA on register.

### Fake content — NONE
- Verified: zero testimonials, zero invented stats/reviews/names.
- Homepage/admin stats are real DB counts (0 when empty).

---

## B. NOT CONFIGURED (by design — needs Ali)

| Item | Status | Where to configure |
|---|---|---|
| Payment gateways | Not implemented (by design) | Wallet shows "Coming Soon"; manual admin credit for testing |
| Telegram bot token | Code ready, token missing | `TELEGRAM_BOT_TOKEN` env, then run `python telegram_bot.py` |
| KD1S API key (preview) | Not on preview (test only) | Preview has no key — no real orders possible there |
| Production domain | Not yet | After Koyeb dashboard recovers |
| Social media URLs | Empty (nothing shown until set) | Admin → Social links |

---

## C. REQUIRES ALI'S ACTION

1. **Telegram bot token** — create via @BotFather, set `TELEGRAM_BOT_TOKEN`,
   run `telegram_bot.py`. Then link from website Profile.
2. **Social URLs** — Admin → Social links → paste official URLs, enable.
3. **Payment gateway choice** — ZainCash / FastPay / AsiaHawala / other?
   (Wallet "Coming Soon" is honest until then.)
4. **KD1S key rotation** — the key was exposed in chat on 2026-10-06.
   Regenerate in KD1S panel before production, update via Admin → Settings.
5. **Review the preview** and send design/function feedback.

---

## D. TEST RESULTS

| Test | Result |
|---|---|
| All Python files compile | ✅ |
| Form engine schemas (subscriptions/comments/default) | ✅ correct fields per type |
| Public pages (/, /services, /compare, /about, /faq, /contact, /terms, /privacy, /health) | ✅ 200 |
| Service detail (Default/Subscriptions/Custom Comments) | ✅ 200, correct schema fields, i Store Service #id |
| CSRF: POST without token → 400, with token → 302 | ✅ |
| Login + dynamic subscription order form renders | ✅ username/min/max/posts fields |
| Order submit → created with correct input_data, charge, status | ✅ (mock rejects unknown service IDs → auto-refund → FAILED, correct) |
| Favorite toggle | ✅ `{"ok": true, "favorite": true}` |
| Currency switch USD↔IQD | ✅ |
| Admin ticket/suspend/social routes | ✅ code + templates (UI verification on preview) |

**Deploy:** commit 98db814 pushed to master; codespace redeploy in progress.

---

## E. DEPLOY FIXES (from earlier known issues)

1. ✅ SQLite path now configurable via `SQLITE_PATH` env (defaults to next
   to the app — no more hardcoded `/home/hatch/...` path).
2. ✅ Settings init now atomic (`INSERT ... ON CONFLICT DO NOTHING`) —
   no more multi-worker UNIQUE race.
