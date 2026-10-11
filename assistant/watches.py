"""Watch specs: one folder per watch under DATA/watches/<name>/.

    watch.yaml   the spec (below)
    q1.jpg ...   2-5 photos of what you are looking for

    name: ggdb-superstar-38
    filter: https://www.vinted.it/catalog?search_text=golden%20goose&...
    mode: exact            # exact = this specific shoe; similar = things like these
    prompt: >
      Golden Goose Superstar, white leather, black star, glitter heel tab.
      Size 38. Not the Ball Star or Hi-Star.
    images: [q1.jpg, q2.jpg]
    favourite: q1.jpg      # optional - in similar mode, weighted most
    notes: I like the worn, distressed finish most   # optional
    every_min: 15          # optional, default 15
    min_score: 0.85        # optional - alert only above this final-judge score
    active: true           # optional, default true
"""

from __future__ import annotations

import glob
import hashlib
import os

import yaml

from assistant.config import WATCHES


def opaque_id(name: str) -> str:
    """Stable id that does not reveal the watch's name in public logs."""
    return "w" + hashlib.sha256(name.encode()).hexdigest()[:10]


def load() -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(WATCHES, "*", "watch.yaml"))):
        folder = os.path.dirname(path)
        try:
            w = yaml.safe_load(open(path)) or {}
        except yaml.YAMLError as exc:
            print(f"  skip {path}: bad yaml ({exc})", flush=True)
            continue
        name = w.get("name") or os.path.basename(folder)
        problems = []
        if not str(w.get("filter", "")).startswith("https://www.vinted."):
            problems.append("filter must be a vinted catalog URL")
        mode = w.get("mode", "similar")
        if mode not in ("exact", "similar"):
            problems.append("mode must be exact or similar")
        imgs = [os.path.join(folder, i) for i in (w.get("images") or [])]
        missing = [i for i in imgs if not os.path.exists(i)]
        if not imgs:
            problems.append("at least one query image is needed")
        if missing:
            problems.append(f"missing images: {[os.path.basename(m) for m in missing]}")
        if problems:
            print(f"  skip watch '{name}': {'; '.join(problems)}", flush=True)
            continue
        fav = w.get("favourite")
        out.append({
            "name": name,
            "id": opaque_id(name),
            "folder": folder,
            "filter": w["filter"],
            "mode": mode,
            "prompt": str(w.get("prompt", "")).strip(),
            "notes": str(w.get("notes", "")).strip(),
            "images": imgs,
            "favourite": os.path.join(folder, fav) if fav else None,
            "every_min": int(w.get("every_min", 15)),
            "min_score": float(w["min_score"]) if w.get("min_score") is not None else None,
            "active": bool(w.get("active", True)),
        })
    return out
