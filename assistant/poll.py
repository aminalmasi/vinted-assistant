"""Actions entrypoint: poll the requested watches' filters, write results.enc.

The filter URLs arrive in the WATCH_FILTERS secret as {opaque_id: url}. What
the cluster asks for this run arrives sealed in WATCH_REQ (see seal.py):

    ids      which watches to poll
    since    per watch, the highest listing id already handled - anything
             above it is new, and gets its own page read here
    detail   per watch, listings the cluster wants pages for: older ones that
             passed the cheap screens on their thumbnail alone

A listing's own page is what the condition and authenticity checks need - all
of its photos, the description, the seller's record - and the catalog card
has none of it. Pages are capped per run so a burst of uploads cannot turn
one poll into a hundred requests; the rest wait for the next tick.

Results go back sealed too: this repository's artifacts are public, and the
listings a filter matched say what is being hunted. Nothing below prints a
URL, a title or a price.
"""

from __future__ import annotations

import json
import os
import sys
import time

from assistant.seal import seal, unseal
from assistant.vinted import detail, fetch, session

GIVE_UP = 3        # consecutive refusals before abandoning the rest of this poll
DETAIL_NEW = 10    # new listings per watch per poll whose page is read
DETAIL_ASKED = 15  # listings the cluster asked for, per poll in total


def main() -> int:
    filters = json.loads(os.environ.get("WATCH_FILTERS") or "{}")
    req = unseal(os.environ["WATCH_REQ"]) if os.environ.get("WATCH_REQ") else {}
    ids = [w for w in req.get("ids", []) if w in filters] or list(filters)
    since, asked = req.get("since") or {}, req.get("detail") or {}
    print(f"polling {len(ids)} watch(es)", flush=True)

    s = session()
    out, refusals, asked_left = {}, 0, DETAIL_ASKED

    def refused(code: int) -> bool:
        nonlocal refusals
        if code in (403, 429):
            refusals += 1
            # A refused request is not lost work: the cluster asks again next
            # tick. Backing off keeps one bad address from burning the rest.
            time.sleep(20)
            return True
        refusals = 0
        return False

    for wid in ids:
        if refusals >= GIVE_UP:
            out[wid] = {"status": "skipped", "listings": [], "details": {}}
            continue
        status, listings = fetch(s, filters[wid])
        refused(status)
        details = {}
        if status == 200:
            floor = since.get(wid)
            # No floor yet means this is the watch's first poll: everything is
            # "new", so pages are left to the cluster's backfill requests,
            # which only ask for what survives the thumbnail screens.
            fresh = [] if floor is None else \
                [c for c in listings if int(c["id"]) > int(floor)][:DETAIL_NEW]
            wanted = [(c["id"], c["url"]) for c in fresh]
            extra = [tuple(x) for x in asked.get(wid, [])][:asked_left]
            asked_left -= len(extra)
            for iid, url in wanted + extra:
                if refusals >= GIVE_UP:
                    break
                code, d = detail(s, url)
                if not refused(code) and code == 200:
                    details[str(iid)] = d
        out[wid] = {"status": status, "listings": listings, "details": details}
        print(f"  {wid}: HTTP {status}, {len(listings)} listings, "
              f"{len(details)} pages read", flush=True)

    with open("results.enc", "w") as fh:
        fh.write(seal({"at": int(time.time()), "watches": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
