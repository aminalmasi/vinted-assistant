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

# Stages 2 and 3 run as headless Claude Code on your subscription, not the API.
SCREEN_MODEL = os.environ.get("VA_SCREEN_MODEL", "haiku")
FINAL_MODEL = os.environ.get("VA_FINAL_MODEL", "opus")
CHUNK = 8                  # candidates per judge call; one message, photos inline

# A watch's first poll sees everything already listed. For an exact hunt that is
# the point - the shoe may already be up - so the first page is judged too.
BACKFILL = True
SEEN_KEEP = 4000           # per-watch memory of listing ids already handled

IMG_SIDE = 768             # photos are downscaled before being sent to a judge
