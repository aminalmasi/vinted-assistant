"""Typical asking price for a listing's brand and category.

Read from the price reference the vinted-watcher repository builds
(scripts/vinted_prices.py -> data/prices/<date>/<brand>.json), using the
newest snapshot. A category with no row of its own falls back to the nearest
ancestor that has one, so a rare leaf still gets its item type's range.

It is an ASKING-price reference, so it tells the judge when a price is
suspiciously low - a fake, or a steal - not what the item will sell for.
"""

from __future__ import annotations

import functools
import glob
import json
import os

from assistant import taxonomy

PRICES = os.environ.get("VA_PRICES", "/extra/malmasik/vinted/data/prices")
BRANDS = os.path.join(os.path.dirname(__file__), "ref", "brands.json")


@functools.cache
def _table() -> dict[tuple[str, int], dict]:
    days = sorted(glob.glob(os.path.join(PRICES, "*")))
    rows: dict[tuple[str, int], dict] = {}
    for day in days:                       # newer days overwrite older ones
        for f in glob.glob(os.path.join(day, "*.json")):
            try:
                d = json.load(open(f))
            except (OSError, ValueError):
                continue
            for r in d.get("rows", []):
                if r.get("asking"):
                    rows[(d["brand"], int(r["catalog_id"]))] = r
    return rows


@functools.cache
def _group_of() -> dict[int, str]:
    try:
        groups = json.load(open(BRANDS))
    except (OSError, ValueError):
        return {}
    return {int(v["id"]): g for g, vs in groups.items() for v in vs}


def lookup(brand_id: int | None, catalog_id: int | None) -> str | None:
    group = _group_of().get(int(brand_id)) if brand_id else None
    if not group or not catalog_id:
        return None
    nodes, _ = taxonomy.tree()
    cid = int(catalog_id)
    while cid in nodes:
        r = _table().get((group, cid))
        if r and r["asking"].get("n", 0) >= 8:
            a = r["asking"]
            return (f"median EUR {a['p50']:.0f}, middle half EUR {a['p25']:.0f}-{a['p75']:.0f} "
                    f"({a['n']} recent listings, {r['path'].split(' > ')[-1]})")
        cid = nodes[cid]["parentId"]
    return None
