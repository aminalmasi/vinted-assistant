"""What does Vinted actually expose per listing?

Three planned features depend on this and none should be designed on a guess:

  - filter accuracy:   which structured fields (brand, size, category,
                       condition) a listing carries, so a mismatch with the
                       filter can be caught for free before any model looks
  - interest signals:  whether likes, views or offers are public at all
  - condition checks:  whether the listing page carries every photo and the
                       seller's description, not just the catalog thumbnail

Prints key NAMES and a few sample values only - this repository's logs are
public. Three requests in total.
"""

from __future__ import annotations

import collections
import re
import sys
import time

from assistant.vinted import session

KEY_RE = re.compile(r'\\?"([a-z][a-z0-9_]{2,40})\\?":')
INTEREST = ("favourite", "like", "view", "offer", "interest", "watch", "count")
CONDITION = ("status", "condition", "description", "size", "brand", "catalog",
             "color", "material", "photo")


def keys_near(text: str, anchor: str, span: int = 6000) -> collections.Counter:
    i = text.find(anchor)
    return collections.Counter(KEY_RE.findall(text[i:i + span])) if i >= 0 else collections.Counter()


def sample(text: str, key: str, n: int = 3) -> list[str]:
    pat = re.compile(r'\\?"' + re.escape(key) + r'\\?":\s*\\?"?([^,}\\"]{0,60})')
    return list(dict.fromkeys(m.strip() for m in pat.findall(text)))[:n]


def main() -> int:
    s = session()
    url = "https://www.vinted.it/catalog?search_text=gucci&order=newest_first"
    cat = s.get(url, timeout=60).text
    ids = re.findall(r"/items/(\d{6,12})-([a-z0-9-]{3,80})", cat)
    print(f"catalog page: {len(cat)//1024} KB, {len(set(i for i, _ in ids))} listings")
    first = ids[0][0]
    ck = keys_near(cat, f'"id\\":{first}') or keys_near(cat, f'"id":{first}')
    print(f"\nkeys around one listing in the CATALOG ({len(ck)}):")
    print("  " + ", ".join(sorted(ck)))

    time.sleep(4)
    iid, slug = ids[0]
    item = s.get(f"https://www.vinted.it/items/{iid}-{slug}", timeout=60).text
    allk = collections.Counter(KEY_RE.findall(item))
    print(f"\nlisting page: {len(item)//1024} KB, {len(allk)} distinct keys")
    for label, words in (("INTEREST signals", INTEREST), ("CONDITION / filter fields", CONDITION)):
        hits = sorted(k for k in allk if any(w in k for w in words))
        print(f"\n{label}:")
        for k in hits[:40]:
            print(f"  {k:<34} {sample(item, k)}")
    photos = set(re.findall(r"https://images\d*\.vinted\.net/[^\"\\\s]+/f800/[^\"\\\s]+", item))
    print(f"\nf800 photo URLs on the listing page: {len(photos)}")

    time.sleep(4)
    # The category tree: Vinted category objects carry an upper-case `code`.
    codes = sorted(set(re.findall(r'\\?"code\\?":\\?"([A-Z][A-Z0-9_]{3,40})\\?"', cat + item)))
    print(f"\ncategory codes seen ({len(codes)}): {codes[:60]}")
    cids = sorted(set(re.findall(r'catalog_id\\?":(\d{1,5})', cat + item)), key=int)
    print(f"catalog ids seen: {cids[:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
