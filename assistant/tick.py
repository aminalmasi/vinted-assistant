"""One tick on the cluster: poll due watches via Actions, then judge locally.

    1. sync each watch's filter URL into the repo's WATCH_FILTERS secret
    2. dispatch the poll workflow and wait for it            (~1-2 min)
    3. keep only listings never handled before for that watch
    4. SigLIP2 floor on CPU                                  free
    5. screen batch on your plan (Haiku)                     ~$0.003 quota / listing
    6. final batch on your plan (Opus)
    7. Telegram the alerts

Run from cron every 15 minutes via scheduler/tick.sh, which holds a lock so a
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
from assistant import judge, notify
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
    return st["watches"].setdefault(wid, {"seen": [], "pending": [], "last_poll": 0,
                                          "stats": {}, "alerted": []})


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


def poll(ids: list[str]) -> dict:
    """Dispatch the poll workflow, wait for it, return results.json."""
    t0 = time.time()
    gh("workflow", "run", C.WORKFLOW, "-R", C.REPO, "-f", "ids=" + ",".join(ids))
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
        return json.load(open(os.path.join(d, "results.json")))


# ----------------------------------------------------------------- photos

def fetch_photo(wid: str, c: dict) -> str | None:
    """Local JPEG copy of a listing's catalog photo (Vinted's CDN, not Vinted)."""
    folder = os.path.join(C.CACHE, wid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{c['id']}.jpg")
    if os.path.exists(path):
        return path
    if not c.get("photo"):
        return None
    try:
        r = requests.get(c["photo"], timeout=30,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.vinted.it/"})
        if r.status_code != 200 or not r.content:
            return None
        # Telegram's sendPhoto wants JPEG/PNG; Vinted serves webp.
        Image.open(io.BytesIO(r.content)).convert("RGB").save(path, "JPEG", quality=90)
        return path
    except (requests.RequestException, OSError):
        return None


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


def stage_one(w: dict, cands: list[dict]) -> list[dict]:
    from assistant.embed import embed, scores
    q = query_embedding(w)
    fav = w["images"].index(w["favourite"]) if w["favourite"] in w["images"] else None
    s = scores(q, embed([c["path"] for c in cands]), w["mode"], fav)
    for c, x in zip(cands, s):
        c["s1"] = round(float(x), 4)
    keep = [c for c in cands if c["s1"] >= C.FLOOR[w["mode"]]]
    keep.sort(key=lambda c: -c["s1"])
    return keep[:C.MAX_SCREEN]


def run_judge(fn, model: str, w: dict, cands: list[dict], stats: dict, key: str):
    verdicts = {}
    for i in range(0, len(cands), C.CHUNK):
        chunk = cands[i:i + C.CHUNK]
        v, meta = fn(model, w, chunk)
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


def process(w: dict, ws: dict, cands: list[dict], dry: bool) -> dict:
    stats = {"new": len(cands)}
    with_photo = []
    for c in cands:
        c["path"] = c.get("path") or fetch_photo(w["id"], c)
        if c["path"]:
            with_photo.append(c)
    stats["with_photo"] = len(with_photo)
    if not with_photo:
        return stats

    s1 = stage_one(w, with_photo)
    stats["stage1"] = len(s1)
    if not s1:
        return stats

    try:
        sv = run_judge(judge.screen, C.SCREEN_MODEL, w, s1, stats, "screen")
        s2 = [c for c in s1 if sv.get(c["id"], {}).get("keep")]
        stats["stage2"] = len(s2)
        if not s2:
            return stats
        fv = run_judge(judge.final, C.FINAL_MODEL, w, s2, stats, "final")
    except judge.QuotaExhausted as exc:
        # Queue rather than drop: these listings were never actually judged,
        # and treating "out of quota" as "no match" would silently lose them.
        ws["pending"] = [{k: c[k] for k in ("id", "slug", "title", "price", "photo",
                                             "url", "path")} for c in s1]
        stats["deferred"] = len(s1)
        stats["quota_exhausted"] = str(exc)[:120]
        return stats

    alerts = 0
    for c in s2:
        v = fv.get(c["id"])
        if v and v.get("alert") and c["id"] not in ws["alerted"]:
            if notify.send(w, c, v, dry=dry):
                alerts += 1
                ws["alerted"] = (ws["alerted"] + [c["id"]])[-500:]
    stats["alerts"] = alerts
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

    if a.results:
        results = json.load(open(a.results))
    else:
        sync_filters(watches, st)
        results = poll([w["id"] for w in due])

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
        listings = r["listings"]
        seen = set(ws["seen"])
        first = not ws["seen"]
        new = [l for l in listings if l["id"] not in seen]
        if first and not C.BACKFILL:
            new = []
        ws["seen"] = (ws["seen"] + [l["id"] for l in listings
                                    if l["id"] not in seen])[-C.SEEN_KEEP:]
        queued, ws["pending"] = ws.get("pending", []), []
        stats = process(w, ws, queued + new, a.dry_run)
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
