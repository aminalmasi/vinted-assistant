"""One tick on the cluster: poll due watches via Actions, then judge locally.

    1. sync each watch's filter URL into the repo's WATCH_FILTERS secret
    2. dispatch the poll workflow (sealed request) and wait     ~1-2 min
       - it reads each NEW listing's own page too: every photo, the
         description, the claimed condition, the seller's record
    3. keep only listings never handled before for that watch
    4. filter check against the listing's page                  free
    5. SigLIP2 floor on CPU                                     free
    6. screen batch on your plan (Haiku), main photo only       ~$0.003 quota / listing
    7. final batch on your plan (Opus), every photo: matches the filter,
       condition honesty, authenticity risk, seller record
    8. Telegram the alerts

A listing that passes 4-6 without its page (a first poll's backlog, or a
burst of uploads past the per-poll cap) waits for the page, which the next
poll fetches on request; only screen survivors cost a page request at all.

Run from cron every 5 minutes via scheduler/tick.sh, which holds a lock so a
slow tick is skipped rather than stacked.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time

import requests
from PIL import Image

from assistant import config as C
from assistant import filtercheck, judge, notify, priceref
from assistant.seal import seal, unseal
from assistant.watches import load as load_watches

STATE_FILE = os.path.join(C.STATE, "state.json")


# ------------------------------------------------------------------ state

def load_state() -> dict:
    try:
        return json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return {"watches": {}}


def save_state(st: dict) -> None:
    os.makedirs(C.STATE, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE_FILE)


def wstate(st: dict, wid: str) -> dict:
    ws = st["watches"].setdefault(wid, {"seen": [], "pending": [], "last_poll": 0,
                                        "stats": {}, "alerted": []})
    ws.setdefault("await", {})      # id -> card, screened in, waiting for its page
    ws.setdefault("max_id", None)   # highest id handled: the poll reads pages above it
    return ws


# What is kept of a listing between ticks.
CARD_KEYS = ("id", "slug", "title", "price", "photo", "url", "likes", "brand", "line2",
             "promoted", "page", "path")


def keep(c: dict) -> dict:
    return {k: c[k] for k in CARD_KEYS if k in c}


# ----------------------------------------------------------------- github

def gh(*args: str, stdin: str | None = None, check: bool = True) -> str:
    p = subprocess.run([C.GH, *args], input=stdin, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {p.stderr.strip()[:300]}")
    return p.stdout


def sync_filters(watches: list[dict], st: dict) -> None:
    """Push filter URLs to the repo secret only when they change.

    The secret is the one place the URLs exist outside the cluster; they never
    appear in the public repository, the workflow inputs or the run logs.
    """
    payload = json.dumps({w["id"]: w["filter"] for w in watches}, sort_keys=True)
    sha = hashlib.sha256(payload.encode()).hexdigest()
    if st.get("filters_sha") == sha:
        return
    gh("secret", "set", "WATCH_FILTERS", "-R", C.REPO, stdin=payload)
    st["filters_sha"] = sha
    print(f"  filters secret updated ({len(watches)} watch(es))", flush=True)


def poll(req: dict) -> dict:
    """Dispatch the poll workflow, wait for it, return its unsealed results."""
    t0 = time.time()
    gh("workflow", "run", C.WORKFLOW, "-R", C.REPO, "-f", "req=" + seal(req))
    run_id = None
    for _ in range(30):                       # find the run we just started
        time.sleep(4)
        runs = json.loads(gh("run", "list", "-R", C.REPO, "--workflow", C.WORKFLOW,
                             "--limit", "5", "--json", "databaseId,createdAt"))
        for r in runs:
            created = dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
            if created.timestamp() >= t0 - 15:
                run_id = r["databaseId"]
                break
        if run_id:
            break
    if not run_id:
        raise RuntimeError("dispatched poll run never appeared")
    deadline = time.time() + 9 * 60
    while time.time() < deadline:
        v = json.loads(gh("run", "view", str(run_id), "-R", C.REPO,
                          "--json", "status,conclusion"))
        if v["status"] == "completed":
            if v["conclusion"] != "success":
                raise RuntimeError(f"poll run {run_id} {v['conclusion']}")
            break
        time.sleep(8)
    else:
        raise RuntimeError(f"poll run {run_id} did not finish in time")
    with tempfile.TemporaryDirectory() as d:
        gh("run", "download", str(run_id), "-R", C.REPO, "-n", "results", "-D", d)
        return unseal(open(os.path.join(d, "results.enc")).read())


# ----------------------------------------------------------------- photos

def _download(url: str | None, path: str) -> str | None:
    """Local JPEG copy of one photo (Vinted's CDN, not Vinted itself)."""
    if os.path.exists(path):
        return path
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.vinted.it/"})
        if r.status_code != 200 or not r.content:
            return None
        # Telegram's sendPhoto wants JPEG/PNG; Vinted serves webp.
        Image.open(io.BytesIO(r.content)).convert("RGB").save(path, "JPEG", quality=90)
        return path
    except (requests.RequestException, OSError):
        return None


def fetch_photo(wid: str, c: dict) -> str | None:
    """The catalog photo, which screening uses."""
    folder = os.path.join(C.CACHE, wid)
    os.makedirs(folder, exist_ok=True)
    return _download(c.get("photo"), os.path.join(folder, f"{c['id']}.jpg"))


def fetch_photos(wid: str, c: dict) -> list[str]:
    """Every photo on the listing's page, for the final judge."""
    folder = os.path.join(C.CACHE, wid)
    os.makedirs(folder, exist_ok=True)
    urls = ((c.get("page") or {}).get("photos") or [])[:C.MAX_PHOTOS]
    paths = [_download(u, os.path.join(folder, f"{c['id']}_{i}.jpg")) for i, u in enumerate(urls)]
    return [p for p in paths if p]


# ---------------------------------------------------------------- cascade

def query_embedding(w: dict):
    """Reference-photo embeddings, cached on disk until a photo changes."""
    import torch
    from assistant.embed import embed
    sig = "|".join(f"{p}:{os.path.getmtime(p)}" for p in w["images"])
    path = os.path.join(C.STATE, f"{w['id']}_query.pt")
    if os.path.exists(path):
        saved = torch.load(path)
        if saved.get("sig") == sig:
            return saved["emb"]
    emb = embed(w["images"])
    torch.save({"sig": sig, "emb": emb}, path)
    return emb


def stage_one(w: dict, cands: list[dict], first: bool = False) -> list[dict]:
    from assistant.embed import embed, scores
    q = query_embedding(w)
    fav = w["images"].index(w["favourite"]) if w["favourite"] in w["images"] else None
    s = scores(q, embed([c["path"] for c in cands]), w["mode"], fav)
    for c, x in zip(cands, s):
        c["s1"] = round(float(x), 4)
    keep = [c for c in cands if c["s1"] >= C.FLOOR[w["mode"]]]
    keep.sort(key=lambda c: -c["s1"])
    return keep[:C.FIRST_SCREEN_MAX if first else C.MAX_SCREEN]


def run_judge(fn, model: str, w: dict, cands: list[dict], stats: dict, key: str,
              chunk: int = C.CHUNK):
    from concurrent.futures import ThreadPoolExecutor
    chunks = [cands[i:i + chunk] for i in range(0, len(cands), chunk)]
    verdicts = {}
    with ThreadPoolExecutor(max_workers=C.JUDGE_PARALLEL) as pool:
        # A QuotaExhausted from any chunk propagates out of .result() and stops
        # the cascade, so the whole batch is queued rather than half-judged.
        for chunk, (v, meta) in zip(chunks, pool.map(lambda ch: fn(model, w, ch), chunks)):
            verdicts.update(v)
            stats[f"{key}_quota_usd"] = round(stats.get(f"{key}_quota_usd", 0)
                                              + (meta.get("quota_usd") or 0), 4)
            stats[f"{key}_calls"] = stats.get(f"{key}_calls", 0) + 1
            if meta.get("error"):
                print(f"    {key} call error: {meta['error'][:160]}", flush=True)
            missing = [c["id"] for c in chunk if c["id"] not in v]
            if missing:
                print(f"    {key}: no verdict for {len(missing)} listing(s)", flush=True)
    return verdicts


def log_verdicts(w: dict, rows: list[dict]) -> None:
    """Every listing's path through the cascade, so rejections can be audited."""
    with open(os.path.join(C.LOGS, "verdicts.jsonl"), "a") as fh:
        for r in rows:
            fh.write(json.dumps({"at": int(time.time()), "watch": w["name"], **r}) + "\n")


def process(w: dict, ws: dict, cands: list[dict], resumed: list[dict], dry: bool,
            first: bool = False) -> dict:
    """cands: listings new this tick, some with their page attached.
    resumed: listings that passed screening earlier and whose page has now
    arrived - they go straight to the final judge."""
    stats = {"new": len(cands), "resumed": len(resumed)}
    f = filtercheck.parse(w["filter"])
    w = dict(w, filter_words=filtercheck.describe(f))
    trail: dict[str, dict] = {}

    def note(c, stage, **kw):
        trail.setdefault(c["id"], {"id": c["id"], "title": c["title"], "price": c.get("price"),
                                   "url": c["url"]}).update(stage=stage, **kw)

    def conforms(c) -> bool:
        if not c.get("page"):
            return True
        bad = filtercheck.violations(f, c, c["page"])
        if bad:
            note(c, "filter_mismatch", why=bad)
            return False
        c["flags"] = filtercheck.soft_flags(f, c["page"])
        return True

    n0 = len(cands) + len(resumed)
    cands, resumed = [c for c in cands if conforms(c)], [c for c in resumed if conforms(c)]
    stats["filter_mismatch"] = n0 - len(cands) - len(resumed)

    with_photo = []
    for c in cands:
        c["path"] = c.get("path") or fetch_photo(w["id"], c)
        if c["path"]:
            with_photo.append(c)
    stats["with_photo"] = len(with_photo)
    s1 = stage_one(w, with_photo, first) if with_photo else []
    stats["stage1"] = len(s1)
    s1_ids = {c["id"] for c in s1}
    floor = C.FLOOR[w["mode"]]
    # Floor and cap are logged apart on purpose: the first test lost ~50 valid
    # listings to the cap, and calling those "below the floor" would have hidden it.
    for c in with_photo:
        if c["id"] not in s1_ids:
            note(c, "dropped_floor" if (c.get("s1") or 0) < floor else "dropped_cap", s1=c.get("s1"))

    try:
        s2 = []
        if s1:
            sv = run_judge(judge.screen, C.SCREEN_MODEL, w, s1, stats, "screen")
            for c in s1:
                v = sv.get(c["id"], {})
                note(c, "screened_in" if v.get("keep") else "screened_out", s1=c.get("s1"),
                     screen_score=v.get("score"), screen_reason=v.get("reason"))
            s2 = [c for c in s1 if sv.get(c["id"], {}).get("keep")]
        stats["stage2"] = len(s2)
        # No page yet: wait for one rather than judge condition from a thumbnail.
        for c in s2:
            if not c.get("page"):
                ws["await"][c["id"]] = dict(keep(c), asks=0)
                note(c, "awaiting_page")
        ready = [c for c in s2 if c.get("page")] + resumed
        stats["final_in"] = len(ready)
        for c in ready:
            c["path"] = c.get("path") or fetch_photo(w["id"], c)
            c["paths"] = fetch_photos(w["id"], c)
            pg = c.get("page") or {}
            c["price_ref"] = priceref.lookup(pg.get("brand_id"), pg.get("catalog_id"))
        ready = [c for c in ready if c.get("path") or c.get("paths")]
        fv = run_judge(judge.final, C.FINAL_MODEL, w, ready, stats, "final",
                       chunk=C.FINAL_CHUNK) if ready else {}
    except judge.QuotaExhausted as exc:
        # Queue rather than drop: these listings were never actually judged,
        # and treating "out of quota" as "no match" would silently lose them.
        ws["pending"] = [keep(c) for c in s1 + resumed]
        stats["deferred"] = len(s1) + len(resumed)
        stats["quota_exhausted"] = str(exc)[:120]
        log_verdicts(w, [dict(t, stage="deferred_quota") for t in trail.values()])
        return stats

    bar = w["min_score"] if w["min_score"] is not None else (C.MIN_SCORE[w["mode"]] or 0.0)
    hits = []
    for c in ready:
        v = fv.get(c["id"]) or {}
        # The judge's own alert decision, with its four checks enforced as well:
        # a listing it calls a match despite a turn-off defect or a high fake
        # risk is still held back.
        held = [why for ok, why in ((v.get("matches", True), "does not match"),
                                    (not v.get("turn_off"), "turn-off defect"),
                                    (v.get("fake_risk") != "high", "high fake risk"))
                if not ok]
        passes = bool(v.get("alert")) and (v.get("score") or 0) >= bar and not held
        note(c, "alerted" if passes else ("held_back" if v.get("alert") else "final_rejected"),
             final_score=v.get("score"), final_alert=v.get("alert"), held=held or None,
             headline=v.get("headline"), final_reason=v.get("reason"),
             condition_claimed=(c.get("page") or {}).get("condition"),
             condition_seen=v.get("condition_seen"), defects=v.get("defects"),
             photo_gaps=v.get("photo_gaps"), fake_risk=v.get("fake_risk"),
             fake_cues=v.get("fake_cues"), seller_risk=v.get("seller_risk"),
             price_ref=c.get("price_ref"))
        if passes and c["id"] not in ws["alerted"]:
            hits.append((c, v))

    alerts = 0
    if hits and (first or len(hits) > C.DIGEST_OVER):
        why = "already listed" if first else "new this tick"
        if notify.digest(w, hits, why, dry=dry):
            alerts = len(hits)
    else:
        for c, v in hits:
            if notify.send(w, c, v, dry=dry):
                alerts += 1
    if alerts:
        ws["alerted"] = (ws["alerted"] + [c["id"] for c, _ in hits])[-500:]
    stats["alerts"] = alerts
    log_verdicts(w, list(trail.values()))
    return stats


# ------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print alerts instead of sending, and save no state")
    ap.add_argument("--results", help="use a local results.json instead of polling")
    ap.add_argument("--watch", help="only this watch name")
    ap.add_argument("--force", action="store_true", help="ignore every_min cadence")
    a = ap.parse_args()

    for d in (C.STATE, C.CACHE, C.LOGS):
        os.makedirs(d, exist_ok=True)
    st = load_state()
    now = time.time()
    watches = [w for w in load_watches() if w["active"]
               and (not a.watch or w["name"] == a.watch)]
    if not watches:
        print("no active watches", flush=True)
        return 0
    due = [w for w in watches if a.force or
           now - wstate(st, w["id"])["last_poll"] >= w["every_min"] * 60 - 60]
    if not due:
        return 0
    print(f"{dt.datetime.now():%H:%M} {len(due)} watch(es) due", flush=True)

    req = {"ids": [w["id"] for w in due], "since": {}, "detail": {}}
    for w in due:
        ws = wstate(st, w["id"])
        if ws["max_id"]:
            req["since"][w["id"]] = ws["max_id"]
        asked = [[i, c["url"]] for i, c in ws["await"].items()
                 if c["asks"] < C.PAGE_ASKS][:C.PAGE_ASK_MAX]
        if asked:
            req["detail"][w["id"]] = asked
    if a.results:
        results = json.load(open(a.results))
    else:
        sync_filters(watches, st)
        results = poll(req)

    log = {"at": int(now), "watches": {}}
    for w in due:
        ws = wstate(st, w["id"])
        r = results.get("watches", {}).get(w["id"])
        if not r or r.get("status") != 200:
            # Not marking it polled means the next tick tries again at once.
            print(f"  {w['name']}: poll {r.get('status') if r else 'missing'}"
                  f" - retrying next tick", flush=True)
            log["watches"][w["name"]] = {"poll": r.get("status") if r else None}
            continue
        listings, pages = r["listings"], r.get("details") or {}
        seen = set(ws["seen"])
        first = not ws["seen"]
        new = [l for l in listings if l["id"] not in seen]
        if first and not C.BACKFILL:
            new = []
        for c in new:
            if c["id"] in pages:
                c["page"] = pages[c["id"]]
        ws["seen"] = (ws["seen"] + [l["id"] for l in listings
                                    if l["id"] not in seen])[-C.SEEN_KEEP:]
        organic = [int(l["id"]) for l in listings if not l.get("promoted")]
        if organic:
            ws["max_id"] = max(organic + ([ws["max_id"]] if ws["max_id"] else []))
        # Awaited listings: resume the ones whose page came back; after
        # PAGE_ASKS failed requests, judge from the thumbnail with that noted.
        resumed = []
        for i, c in list(ws["await"].items()):
            if i in pages:
                resumed.append(dict(ws["await"].pop(i), page=pages[i]))
            elif [i, c["url"]] in req["detail"].get(w["id"], []):
                c["asks"] += 1
                if c["asks"] >= C.PAGE_ASKS:
                    resumed.append(ws["await"].pop(i))
        queued, ws["pending"] = ws.get("pending", []), []
        stats = process(w, ws, queued + new, resumed, a.dry_run, first=first)
        stats["listings"] = len(listings)
        stats["first_poll"] = first
        ws["last_poll"] = now
        for k, v in stats.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                ws["stats"][k] = ws["stats"].get(k, 0) + v
        log["watches"][w["name"]] = stats
        print(f"  {w['name']}: {stats}", flush=True)

    if not a.dry_run:
        save_state(st)
    with open(os.path.join(C.LOGS, "ticks.jsonl"), "a") as fh:
        fh.write(json.dumps(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
