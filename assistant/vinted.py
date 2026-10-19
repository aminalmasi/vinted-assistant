"""Fetch one Vinted catalog page and pull the listings out of it.

Runs on GitHub Actions only. Polling Vinted from the cluster is ruled out: it
would expose the university network to a site that rate-limits hard.

The parsing is lifted from the sold-item tracker (vinted-watcher), where it has
run against tens of thousands of pages. Vinted's public /api/v2/ is gone for
every IP, so this reads the listing data embedded in the catalog HTML.
"""

from __future__ import annotations

import json
import random
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

ITEM_RE = re.compile(r"/items/(\d{6,12})-([a-z0-9-]{3,80})")
PRICE_RE = re.compile(r'\\?"id\\?":(\d{6,12}),.{0,400}?\\?"amount\\?":\\?"([\d.]+)')
PHOTO_RE = re.compile(
    r'\\?"id\\?":(\d{6,12}),.{0,3000}?'
    r'(https://images\d*\.vinted\.net/[^"\\\s]+/f800/[^"\\\s]+)')


def newest_first(url: str) -> str:
    """Force newest-first ordering.

    A watch only needs what appeared since the last poll, and newest-first
    puts all of it on page one. Any other order would scatter new listings
    across pages we never read.
    """
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if k not in ("order", "page")]
    q.append(("order", "newest_first"))
    return urlunparse(p._replace(query=urlencode(q, doseq=True)))


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    try:
        s.get("https://www.vinted.it/", timeout=45)
    except requests.RequestException:
        pass
    return s


def parse(html: str) -> list[dict]:
    """Listings in feed order: id, slug, title, price, photo, url."""
    prices = dict(PRICE_RE.findall(html))
    photos = dict(PHOTO_RE.findall(html))
    out, seen = [], set()
    for iid, slug in ITEM_RE.findall(html):
        if iid in seen:
            continue
        seen.add(iid)
        try:
            price = float(prices[iid]) if iid in prices else None
        except ValueError:
            price = None
        out.append({
            "id": iid,
            "slug": slug,
            # The slug is the seller's title, lowercased and hyphenated - good
            # enough to show a human and to give the judge some text to use.
            "title": slug.replace("-", " "),
            "price": price,
            "photo": photos.get(iid),
            "url": f"https://www.vinted.it/items/{iid}-{slug}",
        })
    return out


def fetch(s: requests.Session, url: str) -> tuple[int, list[dict]]:
    """One page. Returns (http_status, listings); status 0 on network error.

    Read as structured cards; the loose regex reading is kept as a fallback
    so a change to the page's data layout degrades the fields, not the poll.
    """
    time.sleep(random.uniform(2.5, 3.5))
    try:
        r = s.get(newest_first(url), timeout=60)
    except requests.RequestException:
        return 0, []
    if r.status_code != 200:
        return r.status_code, []
    return 200, cards(r.text) or parse(r.text)


# ------------------------------------------------- structured page reading
#
# The regexes above read a catalog page loosely. The page's data is in fact
# structured JSON, streamed inside script payloads, and reading it as such
# gives what the loose reading cannot: brand, condition, size and like count
# per card, and the promoted flag that marks an old listing pushed into a
# newest-first feed.

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
CARD_RE = re.compile(r'\{"id":(\d{6,12}),"productItem":')


def flight(html: str) -> str:
    out = []
    for lit in PUSH_RE.findall(html):
        try:
            out.append(json.loads(lit))
        except ValueError:
            pass
    return "".join(out)


def cards(html: str) -> list[dict]:
    """Every listing card on a catalog page, in feed order."""
    data, dec, out = flight(html), json.JSONDecoder(), []
    for m in CARD_RE.finditer(data):
        try:
            obj, _ = dec.raw_decode(data, m.start())
        except ValueError:
            continue
        p = obj.get("productItem") or {}
        box = p.get("itemBox") or {}
        photos = p.get("photos") or [{}]
        try:
            price = float((p.get("price") or {}).get("amount"))
        except (TypeError, ValueError):
            price = None
        iid = str(p.get("id") or obj["id"])
        url = p.get("url") or f"/items/{iid}"
        out.append({
            "id": iid,
            "slug": url.rsplit("/", 1)[-1].split("-", 1)[-1],
            "title": p.get("title") or "",
            "price": price,
            "photo": photos[0].get("url") if photos else None,
            "url": "https://www.vinted.it" + url,
            "likes": p.get("favouriteCount"),
            "brand": box.get("firstLine"),
            "line2": box.get("secondLine"),         # "M · Ottime", or just "Buone"
            "business": (p.get("user") or {}).get("isBusiness"),
            "promoted": bool(p.get("isPromoted")) or str(
                (obj.get("catalogTracking") or {}).get("contentSource", "")).endswith("promoted_items"),
        })
    return out


def _attributes(html: str) -> dict:
    """The attribute block: Condizioni, Taglia, Colore, Materiale, Caricato."""
    out = {}
    for m in re.finditer(r'data-testid="item-attributes-([a-z_]+)"', html):
        start = html.find(">", m.end()) + 1
        seg = html[start:start + 900]
        parts = [x.strip() for x in re.sub(r"<[^>]+>", "|", seg).split("|") if x.strip()]
        if len(parts) >= 2:
            out[m.group(1)] = parts[1]
    return out


def detail(s: requests.Session, url: str) -> tuple[int, dict]:
    """A listing's own page: every photo, the description, the attributes, the
    seller's record and whether it can still be bought."""
    time.sleep(random.uniform(2.5, 3.5))
    try:
        r = s.get(url, timeout=60)
    except requests.RequestException:
        return 0, {}
    if r.status_code != 200:
        return r.status_code, {}
    raw = r.text
    t = raw.replace('\\"', '"')

    def num(key, cast=int):
        m = re.search(r'"' + key + r'":"?([\d.]+)', t)
        try:
            return cast(m.group(1)) if m else None
        except ValueError:
            return None

    photos = []
    m = re.search(r'"photos":\[', t)
    if m:
        try:
            arr, _ = json.JSONDecoder().raw_decode(t, m.end() - 1)
            photos = [p["url"] for p in arr if isinstance(p, dict) and p.get("url")]
        except ValueError:
            pass
    desc = None
    m = re.search(r'"description":"((?:[^"\\]|\\.){0,4000})"', t)
    if m:
        try:
            desc = json.loads('"' + m.group(1) + '"')
        except ValueError:
            desc = m.group(1)
    can = re.search(r'"can_buy":(true|false)', t)
    res = re.search(r'"is_reserved":(true|false)', t)
    if can is None:
        state = "gone" if '"item_id"' not in t else "unknown"
    elif can.group(1) == "true":
        state = "live"
    else:
        state = "reserved" if res and res.group(1) == "true" else "sold"
    a = _attributes(raw)
    return 200, {
        "state": state, "photos": photos[:12], "description": desc,
        "catalog_id": num("catalog_id"), "brand_id": num("brand_id"),
        "likes": num("favourite_count"), "seller_reviews": num("feedback_count"),
        "seller_rating": num("feedback_reputation", float),
        "condition": a.get("status"), "size": a.get("size"), "color": a.get("color"),
        "material": a.get("material"), "uploaded": a.get("upload_date"),
    }
