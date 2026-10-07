# i Store — Telegram Bot Deployment Guide

**Status (2026-10-07): token NOT yet verified end-to-end.** A `getMe` check was
attempted from this VM using the sanctioned vault-surrogate method
(`dynamic_credentials.url_with_surrogate_path_segment` → `custom.telegram-bot`):
authd issued the surrogate fine, but the egress proxy did not substitute it into
the TLS-tunneled path to `api.telegram.org`, so Telegram returned
`404 {"ok":false,"error_code":404}` (it received the literal surrogate, not the
token). Bearer-header surrogate auth (Promoat) works from this VM, so the
mechanism is alive — this is specific to path-segment placement on this host.
Do not treat the bot as "working" until `getMe` succeeds on the deploy host.

## 1. What the bot does

`telegram_bot.py` — long-polling bot (httpx only, no aiogram).
Commands: `/start /services /order /orders /balance /support /link /language /help`;
admin-only: `/admin /stats` (user must have `SUPER_ADMIN`/`ADMIN` role server-side).
It also serves outbound notifications (`notify_user`) for order status changes.

## 2. Prerequisites on the permanent host

1. Python 3.11+ with the project venv (`pip install -r requirements.txt`).
2. The bot token from @BotFather in the environment — **never in git**:
   ```bash
   export TELEGRAM_BOT_TOKEN="<token-from-BotFather>"
   ```
   On systemd, put it in an `EnvironmentFile=/etc/istore/bot.env` with `chmod 600`.
3. **Same `DATABASE_URL` as the web app** (Neon Postgres in production).
   The bot calls `init_db()` on start and reads `User.telegram_id`,
   `NotificationPreference`, `Order`, `Service` from the shared DB. Users link
   their account via `/link <code>` (code shown on the website profile page).
4. `SITE_URL` env (optional) so `/order` and `/support` hints include the web URL.

## 3. Run it — fix these two bugs first

Two real bugs were found in code review (2026-10-07) that break production:

- **(a) `run_bot_vault.py` never polls.** It overrides the API base with the
  vault surrogate and calls `telegram_bot.main()` — but `main()` starts with
  `if not Config.TELEGRAM_BOT_TOKEN: print("...disabled"); return`. In the vault
  flow the env var is intentionally empty, so the runner exits after printing
  "connected". Fix: make the guard check the effective `API` base
  (e.g. `if "/bot" not in API or API.endswith("/bot")`), or give
  `run_bot_vault.py` its own polling entry point instead of calling `main()`.
  The same guard also silently disables `notify_telegram()` under vault auth
  (`if not Config.TELEGRAM_BOT_TOKEN: return False`) — server notifications
  would never send. Guard on the resolved API base instead.
- **(b) `/admin` and `/stats` crash.** `handle()` uses a `User` object whose
  session was already closed in `find_user()`; accessing the lazy-loaded
  `user.roles` relationship raises `DetachedInstanceError`. Fix: eager-load
  roles in `find_user` (`joinedload(User.roles)`) or re-query roles in a fresh
  session inside the admin branch.

After fixing, sanity-check locally before deploying:

```bash
cd ~/workspace/istore
TELEGRAM_BOT_TOKEN="<token>" DATABASE_URL="<same-as-app>" ./venv/bin/python - <<'EOF'
import telegram_bot
info = telegram_bot.api("getMe")          # real token path, no polling
print(info)
assert info.get("ok"), info
print("getMe OK as @%s" % info["result"]["username"])
EOF
```

## 4. Webhook vs polling — use polling

- **Polling (recommended):** one `getUpdates` long-poll loop, zero inbound
  network requirements, works behind NAT, no TLS cert needed. Idle traffic is
  one request per ~25 s. This bot is low-volume (account commands +
  notifications); polling is simpler and entirely sufficient.
- **Webhook:** would need a public HTTPS endpoint on the Flask app, a
  `setWebhook` call, and a secret-token check — more moving parts, no benefit
  at this scale. Not implemented; don't add it unless message volume grows
  ~100× or latency <1 s becomes a requirement.

Before starting the poller, make sure no webhook is registered, or `getUpdates`
will return `409 Conflict` forever:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/deleteWebhook?drop_pending_updates=true"
```

## 5. Process supervision (systemd example)

One instance only — never run two pollers (duplicate replies, offset races).

```ini
# /etc/systemd/system/istore-bot.service
[Unit]
Description=i Store Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=istore
WorkingDirectory=/opt/istore
EnvironmentFile=/etc/istore/bot.env        # TELEGRAM_BOT_TOKEN, DATABASE_URL, SITE_URL (0600)
ExecStart=/opt/istore/venv/bin/python telegram_bot.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/istore/bot.log
StandardError=append:/var/log/istore/bot.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now istore-bot
sudo journalctl -u istore-bot -f
```

Docker alternative: same env vars, `restart: unless-stopped`.

**Free-tier caveat (Koyeb):** the free tier sleeps after ~1 h idle and runs no
background worker — a poller would stop with the container. For a 24/7 bot,
use a host that stays up (VPS, Render/Railway worker, or this VM), or accept
that the bot only lives while the web dyno is awake.

## 6. Verify it's live

1. `journalctl -u istore-bot` shows `[bot] polling...` with no `poll error` lines.
2. In Telegram, open the bot (username from @BotFather) and send `/start`:
   - unlinked account → "No account found…" message (proves the bot receives and replies);
   - after linking via `/link <code>` from the website profile → welcome message.
3. `/help` lists commands; `/balance` shows the wallet balance from the shared DB.
4. As an admin user, `/stats` returns order/user/revenue counts (after bug (b) is fixed).
5. Trigger a test order-status change on the website and confirm the Telegram notification arrives.

## 7. Operational notes

- **Rate limits:** the poller idles at ~2–3 req/min; Telegram's limit is ~30 msg/s
  per bot — no risk at this volume.
- **Token rotation:** revoke in @BotFather (`/revoke`), update the env file,
  `systemctl restart istore-bot`. The old token dies immediately.
- **Security:** token lives only in the env file / process environment — never
  in git, logs, or chat. `config.py` already reads it from the env (no hardcoded
  secret found in review). Keep `bot.env` at `0600` owned by the service user.
- **Backoff gap (known):** if the token is revoked while running, `getUpdates`
  returns `{"ok": false}` without raising, and the loop retries immediately in a
  tight loop. Consider checking `data.get("ok")` and exiting/backing off so
  systemd's restart policy (or an alert) catches it instead of hammering Telegram.
