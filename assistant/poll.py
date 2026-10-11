"""Actions entrypoint: poll the requested watches' filters, write results.json.

The filter URLs arrive in the WATCH_FILTERS secret as {opaque_id: url}. The
ids are opaque on purpose - this repository and its run logs are public, and a
watch name like "gucci-horsebit-size-38" would publish what you are hunting
for. Nothing below prints a URL, a title or a price.

The cluster dispatches this workflow, waits for it, and downloads the results
as an artifact. All judging happens there.
"""

from __future__ import annotations

import json
import os
import sys
import time

from assistant.vinted import fetch, session

GIVE_UP = 3   # consecutive refusals before abandoning the rest of this poll


def main() -> int:
    filters = json.loads(os.environ.get("WATCH_FILTERS") or "{}")
    wanted = [w for w in (os.environ.get("WATCH_IDS") or "").split(",") if w]
    ids = [w for w in wanted if w in filters] if wanted else list(filters)
    print(f"polling {len(ids)} watch(es)", flush=True)

    s = session()
    out, refusals = {}, 0
    for wid in ids:
        if refusals >= GIVE_UP:
            out[wid] = {"status": "skipped", "listings": []}
            continue
        status, listings = fetch(s, filters[wid])
        if status in (403, 429):
            refusals += 1
            # A refused poll is not lost work: the cluster simply asks again
            # next tick. Backing off here keeps one bad address from burning
            # the remaining watches' requests too.
            time.sleep(20)
        else:
            refusals = 0
        out[wid] = {"status": status, "listings": listings}
        print(f"  {wid}: HTTP {status}, {len(listings)} listings", flush=True)

    json.dump({"at": int(time.time()), "watches": out},
              open("results.json", "w"), separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
