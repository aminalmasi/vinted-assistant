#!/usr/bin/env bash
# Cron entry point for the Vinted assistant. Runs one tick.
#
# flock -n skips this tick if the previous one is still running, rather than
# queueing behind it - a pile-up of waiting ticks is how a slow cycle turns
# into a stall. Nothing is lost by skipping: the next tick picks up whatever
# is due.
#
# Kill switch: touch /extra/malmasik/vinted_assistant_data/STOP
set -uo pipefail

DATA=/extra/malmasik/vinted_assistant_data
REPO_DIR=/extra/malmasik/vinted-assistant
PY=/home/malmasik/.venvs/a100_new/bin/python
LOG="$DATA/logs/tick.log"

[[ -f "$DATA/STOP" ]] && exit 0
mkdir -p "$DATA/logs"

# cron runs with a bare PATH; claude and gh live in ~/.local/bin
export PATH="/home/malmasik/.local/bin:/extra/malmasik/.local/bin:$PATH"
# CPU only: SigLIP2 is fast enough here, and this must never take a GPU
export CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=4 VA_THREADS=4

cd "$REPO_DIR" || exit 1
exec flock -n "$DATA/tick.lock" \
  timeout 14m "$PY" -m assistant.tick "$@" >> "$LOG" 2>&1
