"""Vinted's category tree, as served on vinted.it (2,920 categories).

ref/catalogs.json is the flat list the catalog page embeds, cut to the
fields used here; each node names its parent (`parentId`, -1 for a root).
Refresh it with `python -m assistant.taxonomy_probe` on Actions.

Ids are what Vinted filters on: a listing's page carries its leaf
`catalog_id`, and `catalog[]=<id>` in a search URL takes that category and
everything under it.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

DATA = Path(__file__).with_name("ref") / "catalogs.json"
BASE = "https://www.vinted.it"


@functools.cache
def tree() -> tuple[dict[int, dict], dict[int, list[int]]]:
    nodes = {n["id"]: n for n in json.loads(DATA.read_text())}
    kids: dict[int, list[int]] = {}
    for n in sorted(nodes.values(), key=lambda n: n["order"]):
        kids.setdefault(n["parentId"], []).append(n["id"])
    return nodes, kids


def node(cid: int) -> dict | None:
    return tree()[0].get(int(cid))


def path(cid: int) -> list[str]:
    """Titles from the root down, e.g. ['Donna', 'Accessori', 'Guanti']."""
    nodes, _ = tree()
    out, n = [], nodes.get(int(cid))
    while n:
        out.append(n["title"])
        n = nodes.get(n["parentId"])
    return out[::-1]


def descendants(cid: int) -> set[int]:
    """The category and everything under it - what `catalog[]=cid` matches."""
    _, kids = tree()
    out, stack = set(), [int(cid)]
    while stack:
        c = stack.pop()
        out.add(c)
        stack.extend(kids.get(c, []))
    return out


def leaves(root: int) -> list[int]:
    _, kids = tree()
    return [c for c in descendants(root) if not kids.get(c)]


def filter_url(cid: int, brand_ids: list[int], order: str = "newest_first") -> str:
    q = f"catalog[]={int(cid)}" + "".join(f"&brand_ids[]={b}" for b in brand_ids)
    return f"{BASE}/catalog?{q}&order={order}"
