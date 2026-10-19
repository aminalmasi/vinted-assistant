"""Find every Vinted brand id under which the ten brands are listed.

Sellers pick a brand from Vinted's list, and the list has variants: "Saint
Laurent" next to "Yves Saint Laurent", "Hermes" next to "Hermès", "Dior" next
to "Christian Dior". A tracker that searches one id per brand silently misses
every sale filed under another, so this collects them all:

  1. one free-text search per brand; the catalog cards name each listing's
     brand (itemBox.firstLine), so the variants appear at no extra cost
  2. one listing page per variant, where the brand id sits in the breadcrumb
  3. one search BY that id, to confirm it returns that brand and to read how
     many listings it holds (Vinted caps the count it reports at 960)

Writes brands.json. About sixty requests at a five-second gap.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
import time
import unicodedata

from assistant.vinted import session

BRANDS = {
    "Gucci": ["gucci"],
    "Chanel": ["chanel"],
    "Hermes": ["hermes"],
    "Christian Louboutin": ["louboutin"],
    "Dior": ["dior"],
    "Prada": ["prada"],
    "Saint Laurent": ["saintlaurent", "ysl"],
    "Valentino": ["valentino"],
    "Golden Goose": ["goldengoose", "ggdb"],
    "Bottega Veneta": ["bottegaveneta", "bottega"],
}
GAP = 5.0
# Probe a subset: VA_BRANDS="Dior,Golden Goose"
ONLY = [b.strip() for b in os.environ.get("VA_BRANDS", "").split(",") if b.strip()]
CARD_RE = re.compile(r'"url":"/items/(\d{6,12})-([a-z0-9-]{1,80})".{0,4000}?"firstLine":"([^"]{1,60})"', re.S)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def cards(html: str) -> list[tuple[str, str, str]]:
    """(item id, slug, brand title) for each listing card on a catalog page."""
    text = html.replace('\\"', '"')
    seen, out = set(), []
    for iid, slug, brand in CARD_RE.findall(text):
        if iid not in seen:
            seen.add(iid)
            out.append((iid, slug, brand))
    return out


def total(html: str) -> int | None:
    m = re.search(r'"total_entries":(\d+)', html.replace('\\"', '"'))
    return int(m.group(1)) if m else None


def get(s, url: str, **params) -> str:
    time.sleep(GAP)
    r = s.get(url, params=params or None, timeout=60)
    print(f"    {r.status_code} {len(r.text)//1024} KB", flush=True)
    return r.text if r.status_code == 200 else ""


def main() -> int:
    s = session()
    out = {}
    for brand, keys in BRANDS.items():
        if ONLY and brand not in ONLY:
            continue
        print(f"{brand}:", flush=True)
        html = get(s, "https://www.vinted.it/catalog", search_text=brand, order="newest_first")
        cs = cards(html)
        names = collections.Counter(b for _, _, b in cs)
        variants = [n for n in names if any(k in norm(n) for k in keys)]
        print(f"  {len(cs)} cards, brand titles: {dict(names.most_common(8))}")
        found = []
        for name in variants:
            # Up to three listings per variant: the first may have been
            # deleted since the search (a 404 lost Dior and Golden Goose).
            m = None
            for iid, slug in [(i, sl) for i, sl, b in cs if b == name][:3]:
                page = get(s, f"https://www.vinted.it/items/{iid}-{slug}")
                m = (re.search(r'\\?"brand_id\\?":\\?"?(\d+)', page)
                     or re.search(r'/brand/(\d+)-[a-z0-9-]+', page))
                if m:
                    break
            if not m:
                print(f"  {name!r}: no brand id on its listing page")
                continue
            bid = int(m.group(1))
            check = get(s, "https://www.vinted.it/catalog", **{"brand_ids[]": bid, "order": "newest_first"})
            got = collections.Counter(b for _, _, b in cards(check))
            share = got[name] / max(1, sum(got.values()))
            found.append({"title": name, "id": bid, "listings": total(check),
                          "share_on_id_search": round(share, 2), "seen_in_search": names[name]})
            print(f"  {name!r}: id {bid}, {total(check)} listings, {share:.0%} of an id search is this title;"
                  f" the id search returns {dict(got.most_common(4))}")
        out[brand] = found
    json.dump(out, open("brands.json", "w"), indent=1, ensure_ascii=False)
    print("wrote brands.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
