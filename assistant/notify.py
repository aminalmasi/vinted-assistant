"""Telegram delivery: the listing photo with a short caption.

Reuses the bot and chat from the original Vinted setup (~/.config/vinted.env).
The photo is uploaded from the local copy rather than passed as a URL, so an
alert does not depend on Telegram's servers being able to fetch Vinted's CDN.
"""

from __future__ import annotations

import html
import os
import re

import requests

from assistant.config import DATA

API = "https://api.telegram.org/bot{token}/{method}"


def _creds() -> tuple[str | None, list[str]]:
    """Bot token from the original Vinted setup; recipients from this assistant's
    own list.

    The two are separate on purpose. ~/.config/vinted.env addresses the sold-item
    tracker's audience, which is more than one person - and a personal shoe hunt
    is nobody else's business unless you add them. Recipients therefore come from
    DATA/telegram.env (VA_TELEGRAM_CHAT=...), which starts with you alone.
    """
    def read(path):
        env = {}
        if os.path.exists(path):
            for line in open(path):
                line = line.split("#", 1)[0].strip()   # inline comments are common here
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        return env
    shared = read(os.path.expanduser("~/.config/vinted.env"))
    own = read(os.path.join(DATA, "telegram.env"))
    token = os.environ.get("VA_TELEGRAM_TOKEN") or shared.get("VINTED_TELEGRAM_BOT_TOKEN")
    raw = os.environ.get("VA_TELEGRAM_CHAT") or own.get("VA_TELEGRAM_CHAT") or ""
    # A malformed id is a silent failure at send time, so reject it here.
    chats = [c.strip() for c in raw.split(",") if re.fullmatch(r"-?\d{5,20}", c.strip())]
    return token, chats


GRADE = {"new_tags": "new with tags", "new": "new", "very_good": "very good",
         "good": "good", "satisfactory": "satisfactory", "cannot_tell": "can't tell"}


def caption(w: dict, c: dict, v: dict) -> str:
    e = html.escape
    pg = c.get("page") or {}
    price = f"EUR {c['price']:.0f}" if c.get("price") else "price ?"
    tag = "🎯" if w["mode"] == "exact" else "✨"
    lines = [f"{tag} <b>{e(v.get('headline') or c['title'])}</b>",
             f"{e(price)} · match {v.get('score', 0):.2f} · {e(w['name'])}"]
    if c.get("price_ref"):
        lines.append(f"typical: {e(c['price_ref'])}")
    if v.get("condition_seen"):
        claimed = pg.get("condition") or "?"
        lines.append(f"condition: seller says <i>{e(claimed)}</i>, photos show "
                     f"<b>{e(GRADE.get(v['condition_seen'], v['condition_seen']))}</b>")
    if v.get("defects"):
        lines.append("defects: " + e("; ".join(v["defects"][:3])))
    if v.get("photo_gaps"):
        lines.append("not shown: " + e(v["photo_gaps"][:120]))
    if v.get("fake_risk"):
        seller = f"{pg['seller_reviews']} reviews" if pg.get("seller_reviews") is not None else "seller ?"
        lines.append(f"fake risk {e(v['fake_risk'])} · {e(seller)}"
                     + (f" · {pg['likes']} likes" if pg.get("likes") is not None else ""))
    lines += [e(v.get("reason", "")), f'<a href="{e(c["url"])}">open on Vinted</a>']
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


def digest(w: dict, hits: list[tuple[dict, dict]], why: str, dry: bool = False,
           top: int = 10) -> bool:
    """One message for many matches.

    A watch's first poll judges everything already listed, and a model-level
    request on a busy filter genuinely matches dozens - the test watch produced
    73 correct alerts in one go. Correct, but 73 phone notifications is not an
    assistant. So a flood arrives as one ranked message instead.
    """
    e = html.escape
    hits = sorted(hits, key=lambda cv: -(cv[1].get("score") or 0))
    lines = [f"{'🎯' if w['mode'] == 'exact' else '✨'} <b>{len(hits)} matches</b> "
             f"for <b>{e(w['name'])}</b> ({e(why)}) - best {min(top, len(hits))}:"]
    for c, v in hits[:top]:
        price = f"EUR {c['price']:.0f}" if c.get("price") else "?"
        lines.append(f'• {v.get("score", 0):.2f} · {e(price)} · '
                     f'<a href="{e(c["url"])}">{e((v.get("headline") or c["title"])[:70])}</a>')
    if len(hits) > top:
        lines.append(f"…and {len(hits) - top} more in the verdict log.")
    text = "\n".join(lines)[:4000]
    if dry:
        print("  [dry-run digest]\n    " + text.replace("\n", "\n    "), flush=True)
        return True
    token, chats = _creds()
    if not token or not chats:
        print("  no Telegram credentials - digest not sent", flush=True)
        return False
    ok = False
    for chat in chats:
        try:
            r = requests.post(API.format(token=token, method="sendMessage"),
                              data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}, timeout=60)
            ok = ok or r.ok
            if not r.ok:
                print(f"  telegram {r.status_code}: {r.text[:200]}", flush=True)
        except requests.RequestException as exc:
            print(f"  telegram error: {exc}", flush=True)
    return ok
