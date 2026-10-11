"""Fetch one Vinted catalog page and pull the listings out of it.

Runs on GitHub Actions only. Polling Vinted from the cluster is ruled out: it
would expose the university network to a site that rate-limits hard.

The parsing is lifted from the sold-item tracker (vinted-watcher), where it has
run against tens of thousands of pages. Vinted's public /api/v2/ is gone for
every IP, so this reads the listing data embedded in the catalog HTML.
"""

from __future__ import annotations

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
    """One page. Returns (http_status, listings); status 0 on network error."""
    time.sleep(random.uniform(2.5, 3.5))
    try:
        r = s.get(newest_first(url), timeout=60)
    except requests.RequestException:
        return 0, []
    if r.status_code != 200:
        return r.status_code, []
    return 200, parse(r.text)
