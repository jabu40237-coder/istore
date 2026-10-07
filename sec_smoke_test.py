"""Security-hardening smoke tests (run against the real sqlite dev DB).

Never prints secret values. Uses throwaway key '_test_probe' and deletes it.
"""
import os
import sys

sys.path.insert(0, "/home/hatch/workspace/istore")
os.chdir("/home/hatch/workspace/istore")
os.environ.pop("DB_ENCRYPTION_KEY", None)  # force ephemeral-key path

from app import create_app  # noqa: E402
from db import get_session  # noqa: E402
from models import SystemSetting  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)


app = create_app()
app.config["TESTING"] = True
client = app.test_client()

# 1. route smoke
for path in ["/", "/services", "/about", "/health"]:
    r = client.get(path)
    check(f"GET {path} == 200", r.status_code == 200, f"got {r.status_code}")

# 2. security headers on a 200
r = client.get("/")
check("CSP header present", "Content-Security-Policy" in r.headers)
check("CSP keeps tailwind CDN",
      "cdn.tailwindcss.com" in r.headers.get("Content-Security-Policy", ""))
check("X-Frame-Options DENY", r.headers.get("X-Frame-Options") == "DENY")
check("X-Content-Type-Options nosniff",
      r.headers.get("X-Content-Type-Options") == "nosniff")
check("Referrer-Policy", r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin")
pp = r.headers.get("Permissions-Policy", "")
check("Permissions-Policy minimal",
      "camera=()" in pp and "microphone=()" in pp and "geolocation=()" in pp)

# 3. 404 page renders (no exception, friendly page, no traceback)
r = client.get("/this-url-does-not-exist-12345")
check("404 status", r.status_code == 404)
body = r.data.decode("utf-8", "replace")
check("404 friendly page", "Page not found" in body or "نەدۆزرایەوە" in body)
check("404 no traceback", "Traceback" not in body)

# 4. 429 handler registered
check("429 handler registered", 429 in app.error_handler_spec.get(None, {}))

# 5. CSRF enforced on a POST route (set-currency, no token -> 400)
r = client.post("/set-currency", data={"currency": "IQD"})
check("POST /set-currency w/o CSRF -> 400", r.status_code == 400,
      f"got {r.status_code}")

# 6. session hardening config
check("PERMANENT_SESSION_LIFETIME == 12h",
      app.config.get("PERMANENT_SESSION_LIFETIME").total_seconds() == 12 * 3600)
check("SESSION_COOKIE_SAMESITE Lax", app.config.get("SESSION_COOKIE_SAMESITE") == "Lax")
check("SESSION_COOKIE_HTTPONLY", app.config.get("SESSION_COOKIE_HTTPONLY") is True)

# 7. rate limit: hammer /login POST 10x -> expect 429s after 5
codes = []
for _ in range(10):
    rr = client.post("/login", data={"email": "x@x.x", "password": "y"})
    codes.append(rr.status_code)
n429 = sum(1 for c in codes if c == 429)
check("login rate limit 429s after 5 (10 posts)", n429 >= 4, f"codes={codes}")
check("429 page friendly", "Too many requests" in rr.data.decode("utf-8", "replace"))

# 8. secrets.py unit checks
from services.secrets import (  # noqa: E402
    encrypt_value, decrypt_value, get_secret, set_secret)

tok = encrypt_value("hello-probe")
check("encrypt->decrypt roundtrip", decrypt_value(tok) == "hello-probe")
check("ciphertext version-tagged v1:", tok.startswith("v1:"))
check("legacy untagged token still decrypts",
      decrypt_value(tok[len("v1:"):]) == "hello-probe")

db = get_session()
try:
    db.query(SystemSetting).filter(SystemSetting.key.in_(["_test_probe", "_test_probe_key"])).delete(
        synchronize_session=False)
    db.commit()
    # non-sensitive plaintext: returned as-is, NOT encrypted
    db.add(SystemSetting(key="_test_probe", value="plaintext-value"))
    # sensitive-pattern plaintext: returned AND migrated to encrypted in place
    db.add(SystemSetting(key="_test_probe_key", value="plaintext-value", is_secret=True))
    db.commit()
    got = get_secret(db, "_test_probe", default="MISSING")
    check("get_secret returns plaintext value", got == "plaintext-value")
    row = db.query(SystemSetting).filter_by(key="_test_probe").first()
    check("non-sensitive plaintext left alone", row.value == "plaintext-value")
    got = get_secret(db, "_test_probe_key", default="MISSING")
    check("get_secret migrates sensitive plaintext, returns value", got == "plaintext-value")
    db.commit()  # persist the migration flush
    row = db.query(SystemSetting).filter_by(key="_test_probe_key").first()
    check("stored value is now encrypted",
          row.value != "plaintext-value" and row.value.startswith("v1:"))
    check("second read decrypts", get_secret(db, "_test_probe_key") == "plaintext-value")
    set_secret(db, "_test_probe2", "top-secret-value")
    db.commit()
    row2 = db.query(SystemSetting).filter_by(key="_test_probe2").first()
    check("set_secret stores encrypted", row2.value.startswith("v1:")
          and row2.value != "top-secret-value")
    check("set_secret roundtrip", get_secret(db, "_test_probe2") == "top-secret-value")
    check("get_secret default for missing", get_secret(db, "_no_such_key", default="D") == "D")
    # cleanup throwaway keys
    db.query(SystemSetting).filter(
        SystemSetting.key.in_(["_test_probe", "_test_probe_key", "_test_probe2"])).delete(
        synchronize_session=False)
    db.commit()
    check("throwaway keys deleted",
          db.query(SystemSetting).filter_by(key="_test_probe").first() is None)
finally:
    db.close()

# 9. CSP nonce consistency: every page's inline <script> carries the same
# nonce that the CSP header advertises; no bare <script> tags remain.
import re
for _p in ["/", "/services", "/about", "/login", "/register"]:
    _r = client.get(_p)
    _csp = _r.headers.get("Content-Security-Policy", "")
    _m = re.search(r"'nonce-([^']+)'", _csp)
    _html = _r.data.decode("utf-8", "replace")
    _nonces = set(re.findall(r'nonce="([^"]+)"', _html))
    _bare = len(re.findall(r"<script>(?!</)", _html))
    check(f"nonce consistent on {_p}",
          _r.status_code == 200 and _m and _nonces == {_m.group(1)} and _bare == 0)

# 10. order-create POST 20/min (CSRF-valid, unauthenticated: limiter runs
# before login_required, so first 20 -> 302, then 429s)
client.get("/")
with client.session_transaction() as _s:
    _csrf = _s["csrf"]
_ocodes = [client.post("/dashboard/order",
                       data={"service_id": "1", "csrf": _csrf}).status_code
           for _ in range(25)]
check("order-create POST 20/min",
      _ocodes[:20] == [302] * 20 and _ocodes[20:] == [429] * 5,
      f"boundary={_ocodes[19]}/{_ocodes[20]}")

fails = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(fails)}/{len(results)} checks passed")
sys.exit(1 if fails else 0)
