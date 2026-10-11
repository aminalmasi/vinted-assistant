"""Paths and tunables for the cluster side.

Everything personal - watches, prompts, query images, what has been seen and
judged - lives under DATA, outside the repository, because the repository is
public.
"""

from __future__ import annotations

import os

DATA = os.environ.get("VA_DATA", "/extra/malmasik/vinted_assistant_data")
WATCHES = os.path.join(DATA, "watches")
STATE = os.path.join(DATA, "state")
CACHE = os.path.join(DATA, "cache")
LOGS = os.path.join(DATA, "logs")

REPO = os.environ.get("VA_REPO", "aminalmasi/vinted-assistant")
WORKFLOW = "poll.yml"
GH = os.environ.get("VA_GH", "/extra/malmasik/.local/bin/gh")
CLAUDE = os.environ.get("VA_CLAUDE", "/home/malmasik/.local/bin/claude")

SIGLIP = os.environ.get(
    "VA_SIGLIP", "/extra/malmasik/hf_models/SigLIP2/google_siglip2_so400m_patch16_384")

# Stage 1, SigLIP2. These are loose floors, not decisions: different shoes
# still score 0.50-0.63 against each other in this embedding space, so a tight
# threshold here would be miscalibrated guesswork. The floor only drops what is
# plainly unrelated; the judges below make the actual call.
FLOOR = {"exact": 0.60, "similar": 0.55}
MAX_SCREEN = 24            # stage-1 survivors passed to the screen per watch per tick
# ...except on a watch's FIRST poll, which is uncapped. The test watch's filter
# returned 74 genuine Ball Stars and the cap of 24 cut ~50 of them before any
# judge saw them. For an exact hunt the first poll is precisely when the shoe
# may already be listed, so it is screened in full - a one-time cost of roughly
# $0.007 of plan quota per listing.
FIRST_SCREEN_MAX = 200

# No default score bar. One was tried (0.80 for exact) and immediately proved
# wrong: the planted target scored 0.82 on one run and 0.76 on an identical
# re-run, about +-0.06 of judge noise, so a bar set from a single run sat right
# on that noise and suppressed the very listing the watch existed to find.
# The final judge's own alert decision is used; set `min_score:` on a watch
# only once you have seen how its scores actually fall.
MIN_SCORE = {"exact": None, "similar": None}

# Judge calls are independent processes, so they run side by side. The first
# test ran 17 calls in series and took 20 minutes, past the tick timeout.
JUDGE_PARALLEL = 3

# Stages 2 and 3 run as headless Claude Code on your subscription, not the API.
SCREEN_MODEL = os.environ.get("VA_SCREEN_MODEL", "haiku")
FINAL_MODEL = os.environ.get("VA_FINAL_MODEL", "opus")
CHUNK = 8                  # candidates per judge call; one message, photos inline

# A watch's first poll sees everything already listed. For an exact hunt that is
# the point - the shoe may already be up - so the first page is judged too.
BACKFILL = True
SEEN_KEEP = 4000           # per-watch memory of listing ids already handled

IMG_SIDE = 768             # photos are downscaled before being sent to a judge

# More alerts than this in one tick arrive as a single ranked digest instead of
# separate messages; a watch's first poll always does.
DIGEST_OVER = 5
