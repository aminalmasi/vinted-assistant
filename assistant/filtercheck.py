"""Does a listing actually satisfy the watch's Vinted filter?

Vinted's results are not the filter. A free-text search returns listings that
merely mention the words; promoted listings are mixed into the feed; and
sellers file items under the wrong category, which no field can reveal. So
conformance is checked in two layers:

  here, free    the listing's own page states its brand id, category id,
                condition and price. A filter that names any of these is
                checked against them exactly, before any model is paid for.
  the judges    whether the photos show what the filter's category and the
                watch's text describe - the mis-filed bag in a shoe filter.

`describe` turns the filter URL into words for the judges, so they check
the listing against what was asked rather than against a URL.
"""

from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlparse

from assistant import taxonomy

# Vinted's condition ids, as used by status_ids[] in a filter URL. Not yet
# confirmed against a live search (the page carries no id table), so a
# mismatch on condition is reported to the judge, never used to drop.
STATUS = {6: "Nuovo con cartellino", 1: "Nuovo senza cartellino", 2: "Ottime",
          3: "Buone", 4: "Discrete"}
BRANDS = os.path.join(os.path.dirname(__file__), "ref", "brands.json")


def _brand_names() -> dict[int, str]:
    try:
        groups = json.load(open(BRANDS))
    except (OSError, ValueError):
        return {}
    return {int(v["id"]): v["title"] for vs in groups.values() for v in vs}


def parse(url: str) -> dict:
    q = parse_qs(urlparse(url).query)

    def ints(key):
        out = []
        for k in (key, key.replace("[]", "")):
            for v in q.get(k, []):
                out += [int(x) for x in v.split(",") if x.strip().isdigit()]
        return out

    def num(key):
        try:
            return float(q[key][0])
        except (KeyError, ValueError, IndexError):
            return None

    return {"catalog": ints("catalog[]"), "brands": ints("brand_ids[]"),
            "status": ints("status_ids[]"), "sizes": ints("size_ids[]"),
            "colors": ints("color_ids[]"), "materials": ints("material_ids[]"),
            "price_from": num("price_from"), "price_to": num("price_to"),
            "text": (q.get("search_text") or [""])[0].strip()}


def describe(f: dict) -> str:
    names = _brand_names()
    lines = []
    if f["catalog"]:
        lines.append("Category: " + "; ".join(" > ".join(taxonomy.path(c)) or f"id {c}"
                                              for c in f["catalog"]))
    if f["brands"]:
        lines.append("Brand: " + ", ".join(names.get(b, f"brand id {b}") for b in f["brands"]))
    if f["status"]:
        lines.append("Condition: " + ", ".join(STATUS.get(s, f"id {s}") for s in f["status"]))
    if f["price_from"] is not None or f["price_to"] is not None:
        hi = "any" if f["price_to"] is None else f"{f['price_to']:.0f}"
        lines.append(f"Price: {f['price_from'] or 0:.0f} to {hi} EUR")
    if f["text"]:
        lines.append(f'Search words: "{f["text"]}"')
    if f["sizes"]:
        lines.append("A size is selected in the filter; check the listing's size "
                     "against the size in my request")
    return "\n".join(lines) or "(no structured filter)"


def violations(f: dict, card: dict, page: dict) -> list[str]:
    """Hard mismatches between the filter and the listing's own page."""
    out = []
    if page.get("state") in ("sold", "reserved", "gone"):
        out.append(f"already {page['state']}")
    if f["brands"] and page.get("brand_id") and page["brand_id"] not in f["brands"]:
        out.append(f"brand id {page['brand_id']} not in the filter")
    if f["catalog"] and page.get("catalog_id"):
        allowed = set().union(*(taxonomy.descendants(c) for c in f["catalog"]))
        if page["catalog_id"] not in allowed:
            out.append("filed under " + " > ".join(taxonomy.path(page["catalog_id"]))
                       + ", outside the filter's category")
    price = card.get("price")
    if price is not None:
        if f["price_from"] is not None and price < f["price_from"]:
            out.append(f"price {price:.0f} below the filter")
        if f["price_to"] is not None and price > f["price_to"]:
            out.append(f"price {price:.0f} above the filter")
    return out


def soft_flags(f: dict, page: dict) -> list[str]:
    """Mismatches worth telling the judge but not worth dropping on."""
    out = []
    if f["status"] and page.get("condition"):
        allowed = {STATUS.get(s) for s in f["status"]}
        if page["condition"] not in allowed:
            out.append(f"seller's condition '{page['condition']}' is not one the filter asked for")
    return out
