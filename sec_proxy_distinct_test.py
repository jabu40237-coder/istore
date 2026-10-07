"""Proxy-mode rate-limit test (distinct client IPs). Run in its OWN process
with TRUST_PROXY=1 so the in-memory rate-limit storage starts fresh.

10 logins with distinct CF-Connecting-IP values -> 10 separate buckets ->
no 429s (the legacy 10/15min auth limiter in services/auth.py keys on
remote_addr and allows 10, so it stays quiet here too).
"""
import os
import sys

sys.path.insert(0, "/home/hatch/workspace/istore")
os.chdir("/home/hatch/workspace/istore")
os.environ.pop("DB_ENCRYPTION_KEY", None)
os.environ["TRUST_PROXY"] = "1"

from app import create_app  # noqa: E402

app = create_app()
app.config["TESTING"] = True
client = app.test_client()

codes = [client.post("/login", data={"email": "x@x.x", "password": "y"},
                     headers={"CF-Connecting-IP": f"evil-{i}"}).status_code
         for i in range(10)]
ok = all(c == 401 for c in codes)
print(("PASS" if ok else "FAIL"),
      "TRUST_PROXY=1: distinct CF-Connecting-IP -> separate buckets (no 429s)",
      f"codes={codes}")
sys.exit(0 if ok else 1)
