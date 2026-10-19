"""Extract Vinted's category tree and the ten brands' ids.

All three planned features need them: the filter curator checks that a listing
sits in the category you filtered for, the steals tracker files each sale
under its category, and the price reference is one row per brand x category.

Searching by brand id is also far more precise than the free-text search the
sold-tracker uses ("gucci shoes"), which pulls in listings that merely mention
the brand.

The tree is embedded in every catalog page as JSON inside the page's script
payloads; this decodes those payloads and walks the nested `catalogs` arrays.
Writes taxonomy.json (Vinted's own public category list) as the artifact.
"""

from __future__ import annotations

import json
import re
import sys
import time

from assistant.vinted import session

BRANDS = ["Gucci", "Chanel", "Hermes", "Christian Louboutin", "Dior", "Prada",
          "Saint Laurent", "Valentino", "Golden Goose", "Bottega Veneta"]
PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


def flight(html: str) -> str:
    """Concatenate the decoded script payloads the page streams its data in."""
    out = []
    for lit in PUSH_RE.findall(html):
        try:
            out.append(json.loads(lit))
        except ValueError:
            pass
    return "".join(out)


def balanced(text: str, start: int) -> str | None:
    """The JSON array or object beginning at text[start], brackets matched."""
    open_ch = text[start]
    close_ch = {"[": "]", "{": "}"}[open_ch]
    depth, i, in_str, esc = 0, start, False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def find_tree(data: str) -> list | None:
    """The largest parseable `catalogs` array - the full tree, not a submenu."""
    best = None
    for m in re.finditer(r'"catalogs":\[', data):
        blob = balanced(data, m.end() - 1)
        if not blob or (best and len(blob) <= len(best)):
            continue
        try:
            json.loads(blob)
            best = blob
        except ValueError:
            pass
    return json.loads(best) if best else None


def flatten(nodes: list, path: list, out: list) -> None:
    for n in nodes:
        here = path + [n.get("title", "?")]
        kids = n.get("catalogs") or []
        out.append({"id": n.get("id"), "code": n.get("code"), "title": n.get("title"),
                    "path": " > ".join(here), "depth": len(here),
                    "leaf": not kids, "url": n.get("url")})
        flatten(kids, here, out)


def brand_id(s, name: str) -> dict:
    """Read the brand id off a real listing whose brand field is this brand."""
    r = s.get("https://www.vinted.it/catalog",
              params={"search_text": name, "order": "relevance"}, timeout=60)
    for iid, slug in re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,80})", r.text)[:6]:
        time.sleep(5)
        page = s.get(f"https://www.vinted.it/items/{iid}-{slug}", timeout=60).text
        m = re.search(r'\\?"brand_id\\?":(\d+)', page)
        t = re.search(r'\\?"url\\?":\\?"/brand/(\d+)-([a-z0-9-]+)\\?"', page)
        if m and t and t.group(1) == m.group(1):
            want = re.sub(r"[^a-z]", "", name.lower())
            got = re.sub(r"[^a-z]", "", t.group(2))
            if want in got or got in want:
                return {"brand": name, "id": int(m.group(1)), "slug": t.group(2)}
    return {"brand": name, "id": None, "slug": None}


def main() -> int:
    s = session()
    html = s.get("https://www.vinted.it/catalog", params={"search_text": "gucci"},
                 timeout=60).text
    data = flight(html)
    print(f"page {len(html)//1024} KB -> {len(data)//1024} KB decoded payload")
    tree = find_tree(data)
    if not tree:
        print("could not locate the catalogs tree")
        return 1
    flat = []
    flatten(tree, [], flat)
    print(f"tree: {len(tree)} roots, {len(flat)} categories, "
          f"{sum(f['leaf'] for f in flat)} leaves")
    for f in flat:
        if f["depth"] <= 2:
            print(f"  {f['id']:>6}  {f['code'] or '':<28} {f['path']}")

    brands = []
    for b in BRANDS:
        time.sleep(5)
        brands.append(brand_id(s, b))
        print(f"  brand {brands[-1]}", flush=True)

    json.dump({"tree": flat, "brands": brands}, open("taxonomy.json", "w"), indent=1)
    print("wrote taxonomy.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
