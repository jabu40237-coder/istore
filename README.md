# i Store | ئایستۆر — Premium Digital Services Platform

A production-ready SaaS foundation for a digital services store.
KD1S is integrated as **backend provider #1 only** — it never appears
in the customer-facing interface.

## Quick start (development)

```bash
cd ~/workspace/istore
cp .env.example .env          # then edit secrets
./venv/bin/python setup_admin.py admin@istore.local admin StrongPass123
./venv/bin/python app.py      # web  -> http://127.0.0.1:5050
./venv/bin/python worker.py   # background jobs (separate terminal)
./venv/bin/python telegram_bot.py  # needs TELEGRAM_BOT_TOKEN
```

Open http://127.0.0.1:5050 — default language is Kurdish (Badînî), RTL.
Switch with `?lang=ku|ar|en` or from your profile.

## First-run checklist

1. Create admin: `setup_admin.py` (above). No default password is shipped.
2. Log in → **Admin → Settings**:
   - Paste your **KD1S API key** (it is stored server-side, shown masked,
     never sent to the browser, never logged).
   - Set **USD → IQD** rate, accent color, site name.
3. **Admin → Services → Sync Now** — pulls the live catalog from KD1S.
4. **Admin → Pricing** — set your global markup % (default 30%).
   Prices recompute automatically on every sync.
5. Switch production: in `.env` set `PROVIDER_MODE=kd1s`
   (development default is `mock`, a fake provider for testing).

## Architecture

```
browser ──> Flask (app.py + blueprints/) ──> SQLite/PostgreSQL (SQLAlchemy)
                    │                              │
                    ├─ providers/kd1s.py ──> https://kd1s.com/api/v2 (server-side only)
                    ├─ providers/mock.py     (dev, no key needed)
                    ├─ services/sync.py      (catalog sync engine)
                    ├─ services/pricing.py   (markup rules)
                    ├─ services/orders.py    (order engine, idempotent)
                    ├─ services/wallet.py    (immutable ledger)
                    ├─ worker.py             (service sync, status polling, health)
                    └─ telegram_bot.py       (customer + admin bot)
```

**Money safety**
- All amounts are `Numeric(18,6)` / `Decimal` — never float.
- Wallet changes always write a `transactions` row (deposit, order_charge,
  refund, manual_adjustment, bonus, fee).
- Order flow: **reserve wallet → provider request → finalize**.
  If the provider call fails, the reservation is auto-refunded.
- Idempotency keys: double-clicking "Place Order" creates one order.
- Profit stores the exchange rate **at purchase time**.

**Security**
- bcrypt passwords, signed sessions, HttpOnly/SameSite cookies,
  security headers, login rate limiting, RBAC
  (SUPER_ADMIN / ADMIN / SUPPORT / CUSTOMER), audit log.
- Secrets only in env / masked settings. Nothing secret reaches templates,
  logs, or the Telegram bot.

## Payments

`services/payments.py` is the abstraction point (to be added when you pick
a gateway). Planned providers: ZainCash, AsiaHawala, FIB, USDT, cards.
Until then, top-ups are manual admin adjustments
(**Admin → Users → amount/reason**), fully audited.

## Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN`.
3. Run `telegram_bot.py`.
4. Customers link it from their **Profile** (paste their Telegram user ID),
   then `/start` in the bot.
5. Admins: `/stats` (server-side role check — never trusts username).

## Production (VPS)

- `DATABASE_URL=postgresql+psycopg2://...` (needs `pip install psycopg2-binary`)
- `APP_ENV=production`, strong `SESSION_SECRET`
- Run behind nginx/caddy with TLS; `app.py` via gunicorn:
  `gunicorn -w 4 "app:create_app()"`
- `worker.py` as a systemd service (restart=always)
- Daily DB backup: `pg_dump` / sqlite3 `.backup` + retention (see docs)

## Project layout

| path | purpose |
|---|---|
| `app.py` | app factory, public pages, auth |
| `blueprints/` | user dashboard, admin panel, internal JSON API |
| `models.py` | all tables (users→jobs) |
| `providers/` | provider interface + KD1S + mock |
| `services/` | sync, pricing, orders, wallet, auth |
| `locales/` | ku.json (primary), ar.json, en.json |
| `templates/` / `static/` | RTL-aware dark premium UI, Three.js hero |
| `worker.py` / `telegram_bot.py` | background jobs / bot |
| `test_acceptance.py` | end-to-end commerce loop test |

## Acceptance status (2026-10-06, mock provider)

- [x] register / login / admin login
- [x] service sync (7 services, pricing auto-computed)
- [x] search, service detail, dynamic order form, live price
- [x] wallet deduct (ledger), order reaches provider, provider ID stored
- [x] idempotency (no double charge), validation, auto-refund on failure
- [x] status sync worker, refill/cancel buttons (capability-gated)
- [x] ku/ar/en + RTL/LTR, admin analytics, audit log, sync logs
- [ ] KD1S live key (admin pastes it in Settings)
- [ ] Telegram bot token (from @BotFather)
- [ ] payment gateway (you pick: ZainCash / AsiaHawala / FIB / other)
