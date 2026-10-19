"""Seal what crosses GitHub: the poll request and the poll results.

This repository is public, and a public repository's run logs, workflow
inputs and artifacts are readable by anyone signed in to GitHub. The filter
URLs were already kept in a secret, but the results artifact listed every
listing the filters matched - which says exactly what is being hunted - and
the next step, asking Actions for particular listings' pages, would have put
item ids in the inputs.

So both directions are encrypted with one key: the WATCH_KEY repository
secret on the Actions side, DATA/watch.key (mode 600) on the cluster. The
openssl command line does the work, being present on both machines, so
nothing has to be installed on either.
"""

from __future__ import annotations

import json
import os
import subprocess

ARGS = ["-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256", "-a", "-A",
        "-pass", "env:VA_KEY"]


def _key() -> str:
    key = os.environ.get("VA_KEY")
    if key:
        return key
    from assistant.config import DATA
    with open(os.path.join(DATA, "watch.key")) as fh:
        return fh.read().strip()


def _openssl(extra: list[str], data: str) -> str:
    env = dict(os.environ, VA_KEY=_key())
    p = subprocess.run(["openssl", "enc", *extra, *ARGS], input=data, text=True,
                       capture_output=True, env=env)
    if p.returncode != 0:
        # Never echo the input: it is the thing being kept private.
        raise RuntimeError(f"openssl failed ({p.returncode}): {p.stderr.strip()[:120]}")
    return p.stdout


def seal(obj) -> str:
    return _openssl(["-salt"], json.dumps(obj, separators=(",", ":"))).strip()


def unseal(text: str):
    return json.loads(_openssl(["-d"], text.strip() + "\n"))
