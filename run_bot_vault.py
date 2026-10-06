#!/usr/bin/env python3
"""Run the i Store Telegram bot using the vault-stored token (surrogate auth).

The token never touches disk — authd injects a surrogate into the API URL.
Usage: ./venv/bin/python run_bot_vault.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")

from dynamic_credentials import url_with_surrogate_path_segment  # noqa: E402

import telegram_bot  # noqa: E402

CREDENTIAL = "custom.telegram-bot"
ALLOWED = ["api.telegram.org"]


def main():
    url = url_with_surrogate_path_segment(
        "https://api.telegram.org/bot{}/",
        CREDENTIAL,
        allowed_hosts=ALLOWED,
    )
    telegram_bot.set_api_base(url)
    # sanity check: getMe
    info = telegram_bot.api("getMe")
    if not info.get("ok"):
        print("getMe failed:", info, flush=True)
        sys.exit(1)
    me = info["result"]
    print(f"[bot] connected as @{me.get('username')} (id {me.get('id')})",
          flush=True)
    telegram_bot.main()


if __name__ == "__main__":
    main()
