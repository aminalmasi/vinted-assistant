"""Telegram delivery: the listing photo with a short caption.

Reuses the bot and chat from the original Vinted setup (~/.config/vinted.env).
The photo is uploaded from the local copy rather than passed as a URL, so an
alert does not depend on Telegram's servers being able to fetch Vinted's CDN.
"""

from __future__ import annotations

import html
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _creds() -> tuple[str | None, list[str]]:
    env = {}
    path = os.path.expanduser("~/.config/vinted.env")
    if os.path.exists(path):
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    token = os.environ.get("VA_TELEGRAM_TOKEN") or env.get("VINTED_TELEGRAM_BOT_TOKEN")
    raw = os.environ.get("VA_TELEGRAM_CHAT") or env.get("VINTED_TELEGRAM_CHAT_ID") or ""
    return token, [c.strip() for c in raw.split(",") if c.strip()]


def caption(w: dict, c: dict, v: dict) -> str:
    e = html.escape
    price = f"EUR {c['price']:.0f}" if c.get("price") else "price ?"
    tag = "🎯" if w["mode"] == "exact" else "✨"
    lines = [f"{tag} <b>{e(v.get('headline') or c['title'])}</b>",
             f"{e(price)} · match {v.get('score', 0):.2f} · {e(w['name'])}",
             e(v.get("reason", "")),
             f'<a href="{e(c["url"])}">open on Vinted</a>']
    return "\n".join(lines)[:1024]


def send(w: dict, c: dict, v: dict, dry: bool = False) -> bool:
    text = caption(w, c, v)
    if dry:
        print("  [dry-run alert]\n    " + text.replace("\n", "\n    "), flush=True)
        return True
    token, chats = _creds()
    if not token or not chats:
        print("  no Telegram credentials - alert not sent", flush=True)
        return False
    ok = False
    for chat in chats:
        try:
            with open(c["path"], "rb") as fh:
                r = requests.post(API.format(token=token, method="sendPhoto"),
                                  data={"chat_id": chat, "caption": text,
                                        "parse_mode": "HTML"},
                                  files={"photo": fh}, timeout=60)
            ok = ok or r.ok
            if not r.ok:
                print(f"  telegram {r.status_code}: {r.text[:200]}", flush=True)
        except (OSError, requests.RequestException) as exc:
            print(f"  telegram error: {exc}", flush=True)
    return ok
