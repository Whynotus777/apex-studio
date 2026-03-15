#!/usr/bin/env python3
"""Send a message to Abdul via Telegram. Used by spawn-agent.sh.
Usage: python3 adapters/telegram/send_telegram.py "message text" [--buttons '[[{"text":"Approve","data":"approve:task-1"}]]']
"""
import os
import sys
import json
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

if not TOKEN or not CHAT_ID:
    print(f"[telegram-offline] {sys.argv[1] if len(sys.argv) > 1 else ''}")
    sys.exit(0)

text = sys.argv[1] if len(sys.argv) > 1 else ""
payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}

if len(sys.argv) > 3 and sys.argv[2] == "--buttons":
    try:
        buttons = json.loads(sys.argv[3])
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    except json.JSONDecodeError:
        pass

resp = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload)
if resp.status_code != 200:
    print(f"Telegram error: {resp.text}", file=sys.stderr)
