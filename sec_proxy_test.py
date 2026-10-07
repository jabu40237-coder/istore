"""Proxy-mode rate-limit test (same client IP hammer). Run in its OWN process
with TRUST_PROXY=1 so the in-memory rate-limit storage starts fresh.

Same CF-Connecting-IP hammered 10x -> Flask-Limiter's 5/min auth guard
fires: first 5 are 401s, then 429s. (The legacy 10/15min auth limiter in
services/auth.py allows 10, so it stays quiet here.)
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
                     headers={"CF-Connecting-IP": "same-evil"}).status_code
         for _ in range(10)]
ok = codes == [401] * 5 + [429] * 5
print(("PASS" if ok else "FAIL"),
      "TRUST_PROXY=1: same CF-Connecting-IP rate-limited (5x401 then 429s)",
      f"codes={codes}")
sys.exit(0 if ok else 1)
