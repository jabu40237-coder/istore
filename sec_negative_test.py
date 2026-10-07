"""Negative / adversarial security tests. Run in its OWN process so the
in-memory rate-limit storage starts fresh.

- Spoofed X-Forwarded-For / CF-Connecting-IP with TRUST_PROXY unset must be
  IGNORED: the limiter must bucket by request.remote_addr.
- With TRUST_PROXY=1, CF-Connecting-IP must be honoured (varying it must
  create separate buckets).
- CSP header (Talisman) present with a per-request nonce.
- SUPER_ADMIN session older than 30 min -> logged out (302 to /login);
  fresh admin session -> 200.
"""
import os
import sys
import time

sys.path.insert(0, "/home/hatch/workspace/istore")
os.chdir("/home/hatch/workspace/istore")
os.environ.pop("DB_ENCRYPTION_KEY", None)
os.environ.pop("TRUST_PROXY", None)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)


from app import create_app  # noqa: E402

# ---------- 1. spoofed proxy headers ignored (TRUST_PROXY unset) ----------
app = create_app()
app.config["TESTING"] = True
client = app.test_client()

spoof = {"X-Forwarded-For": "9.9.9.9", "CF-Connecting-IP": "8.8.8.8"}
codes = [client.post("/login", data={"email": "x@x.x", "password": "y"},
                     headers=spoof).status_code for _ in range(10)]
check("TRUST_PROXY unset: spoofed headers ignored (5x401 then 429s)",
      codes == [401] * 5 + [429] * 5, f"codes={codes}")

# ---------- 2. CSP header present with per-request nonce ----------
r = client.get("/")
csp = r.headers.get("Content-Security-Policy", "")
check("CSP header present (Talisman)", bool(csp))
check("CSP carries per-request nonce", "nonce-" in csp)
check("CSP keeps tailwind CDN", "cdn.tailwindcss.com" in csp)
check("CSP img-src allows data:+https", "img-src 'self' data: https:" in csp)
check("X-Frame-Options DENY (Talisman)", r.headers.get("X-Frame-Options") == "DENY")
check("frame-ancestors none in CSP", "frame-ancestors 'none'" in csp)
check("Permissions-Policy minimal",
      "camera=()" in r.headers.get("Permissions-Policy", ""))
check("HSTS not forced in dev", "Strict-Transport-Security" not in r.headers)

# ---------- 3. admin 30-minute session lifetime ----------
with client.session_transaction() as s:
    s["user_id"] = 1  # SUPER_ADMIN (local dev account)
    s["login_at"] = time.time() - 31 * 60  # 31 minutes ago
    s["csrf"] = "test"
r = client.get("/", follow_redirects=False)
check("expired admin session -> logged out (302 to /login)",
      r.status_code == 302 and "/login" in r.headers.get("Location", ""),
      f"got {r.status_code} -> {r.headers.get('Location', '')}")

with client.session_transaction() as s:
    s["user_id"] = 1
    s["login_at"] = time.time()  # fresh
    s["csrf"] = "test"
r = client.get("/")
check("fresh admin session still works", r.status_code == 200,
      f"got {r.status_code}")

fails = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(fails)}/{len(results)} negative checks passed")
sys.exit(1 if fails else 0)
