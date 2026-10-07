# i Store — Security operations notes

## Secret encryption at rest (`services/secrets.py`)

* Key env var: `DB_ENCRYPTION_KEY` (Fernet, urlsafe base64, 44 chars).
  Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
* Missing key → app boots with an ephemeral key + loud warning; anything
  written then is unreadable after restart. Always set it in production
  before saving secrets.
* Ciphertexts are version-tagged (`v1:<token>`). Rotation is NOT automatic:
  with the OLD key configured, read each secret → set the NEW key → re-save
  each secret so it is re-encrypted with the new key.
* Never encrypt passwords (bcrypt hashes live in `services/auth.py`).

## Proxy trust (`TRUST_PROXY`)

* `TRUST_PROXY=1` enables `werkzeug` ProxyFix with exactly one hop
  (`x_for=1, x_proto=1`) — correct when one reverse proxy (Koyeb /
  Cloudflare) terminates TLS in front of the app.
* Default is OFF. Enabling it without a real proxy lets clients spoof
  `X-Forwarded-For` (rate-limit / audit evasion) and can cause
  http↔https redirect loops (Talisman `force_https` in production relies on
  the proto header).
* Rate-limit key: `CF-Connecting-IP` when `TRUST_PROXY=1` (set by
  Cloudflare, not spoofable through it), else `request.remote_addr`.
  Bare `X-Forwarded-For` is never trusted for rate limiting.

## Backups (production, Postgres)

1. `chmod 600` on any SQLite file used on a server (`istore.db`); the file
   must never be world-readable and is excluded from git (`*.db`).
2. Postgres dumps must be encrypted before leaving the host, e.g. with
   [age](https://github.com/FiloSottile/age):
   `pg_dump "$DATABASE_URL" | age -r <recipient-pubkey> -o istore-$(date +%F).sql.age`
3. Keep the age recipient private key OUT of the repo (Secure Vault /
   deploy-time secret), rotate it with the same discipline as
   `DB_ENCRYPTION_KEY`.
4. Test restores on a schedule; an untested backup is not a backup.

## Secret scanning (CI suggestion)

* Run [gitleaks](https://github.com/gitleaks/gitleaks) in CI on every push:
  `gitleaks detect --source . --redact` — blocks accidental commits of
  API keys / tokens. `.gitignore` already excludes `.env`, `*.db`,
  `backups/`, `*.log`.
* If a secret ever lands in git history, rotate it immediately — removing
  the commit is not enough (history is copied by every clone).

## Security event logging

Structured logs go to the `istore.security` logger (stdlib `logging`):
`login_success`, `login_failed`, `logout`, `admin_session_expired`,
`wallet_tx`, `rate_limit_hit`. They carry ids/amounts/paths only — NEVER
passwords, TOTP codes, API keys, tokens, or session values.
Optional future hook: route these events into the existing user
notification infra as Telegram alerts for the admin (not built yet).
