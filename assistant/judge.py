"""Stages 2 and 3: headless Claude Code on your subscription, not the API.

Each call is `claude -p` with the photos sent inline in a single message, no
tools loaded and a short system prompt. That shape was measured against the
alternatives on the same five listings:

    one listing per call, Read tool       $0.122 of plan quota each
    batched, Read tool per photo          $0.013 each
    batched, photos inline, no tools      $0.003 each     <- this

The dollar figures are what Claude Code reports; on a subscription they are
not billed, they are the size of the bite out of your usage limits. The
harness (system prompt, tool definitions) is paid once per call, so batching
is what makes this affordable.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import tempfile

from PIL import Image

from assistant.config import CLAUDE, IMG_SIDE


class QuotaExhausted(RuntimeError):
    """The plan's usage limit is reached; judging must wait for the reset."""


def _img(path: str) -> dict:
    # Downscaled: the judge needs to recognise a shoe, not count stitches, and
    # smaller photos cost fewer tokens of your quota.
    im = Image.open(path).convert("RGB")
    im.thumbnail((IMG_SIDE, IMG_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(buf.getvalue()).decode()}}


def _run(model: str, system: str, content: list, schema: dict,
         timeout: int = 420) -> tuple[dict | None, dict]:
    msg = {"type": "user", "message": {"role": "user", "content": content}}
    with tempfile.NamedTemporaryFile("w", suffix=".json") as mcp:
        json.dump({"mcpServers": {}}, mcp)
        mcp.flush()
        cmd = [CLAUDE, "-p", "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--model", model, "--system-prompt", system,
               "--tools", "", "--strict-mcp-config", "--mcp-config", mcp.name,
               "--no-session-persistence", "--json-schema", json.dumps(schema)]
        try:
            p = subprocess.run(cmd, input=json.dumps(msg) + "\n", text=True,
                               capture_output=True, timeout=timeout,
                               cwd=tempfile.gettempdir())
        except subprocess.TimeoutExpired:
            return None, {"error": "timeout"}

    result = None
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            result = ev
    if result is None:
        return None, {"error": (p.stderr or "no result event")[:300]}

    meta = {"quota_usd": result.get("total_cost_usd") or 0.0,
            "turns": result.get("num_turns"),
            "ms": result.get("duration_ms"),
            "models": list((result.get("modelUsage") or {}).keys())}
    if result.get("is_error"):
        text = str(result.get("result", ""))
        # Hitting the plan's usage limit must stop the cascade cleanly rather
        # than read as "nothing matched" - the candidates are queued instead.
        if "limit" in text.lower():
            raise QuotaExhausted(text[:200])
        meta["error"] = text[:300]
        return None, meta
    return result.get("structured_output"), meta


def _query_blocks(w: dict) -> list:
    blocks = [{"type": "text", "text":
               f"WHAT I AM LOOKING FOR ({w['mode']} mode)\n{w['prompt']}"
               + (f"\nWhat I like most: {w['notes']}" if w["notes"] else "")}]
    for i, p in enumerate(w["images"], 1):
        tag = " (my favourite)" if w["favourite"] == p else ""
        blocks += [{"type": "text", "text": f"Reference photo R{i}{tag}:"}, _img(p)]
    return blocks


def _cand_blocks(cands: list[dict]) -> list:
    blocks = []
    for c in cands:
        price = f"EUR {c['price']:.0f}" if c.get("price") else "price unknown"
        blocks += [{"type": "text", "text":
                    f"LISTING {c['id']}: \"{c['title']}\" - {price}"}, _img(c["path"])]
    return blocks


MODES = """\
exact mode: I want one specific shoe. Match only the same model and \
colourway; a different model from the same brand is a no, however close.
similar mode: I want things in the spirit of my reference photos. Judge how \
well each listing fits the style, shape and details I describe, leaning on my \
favourite photo and on what I said I like most."""

SCREEN_SYS = f"""\
You pre-screen second-hand shoe listings on Vinted for one buyer. You see the \
buyer's request and reference photos, then a batch of listings, each with one \
photo. Keep any listing that plausibly fits, and drop the clearly unrelated - \
a stricter judge reviews whatever you keep, so when unsure, keep it.
{MODES}
Answer only through the JSON schema, one verdict per listing."""

FINAL_SYS = f"""\
You decide whether a second-hand shoe listing on Vinted is worth alerting one \
buyer about. Each alert interrupts them on their phone, so alert only when the \
listing genuinely fits what they asked for; a near miss is not an alert.
{MODES}
For each listing, write a headline of at most 12 words saying what it is and \
why it fits, and a one-sentence reason. Answer only through the JSON schema."""

SCREEN_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["verdicts"], "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "keep", "score", "reason"],
        "properties": {"id": {"type": "string"}, "keep": {"type": "boolean"},
                       "score": {"type": "number"}, "reason": {"type": "string"}}}}}}

FINAL_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["verdicts"], "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "alert", "score", "headline", "reason"],
        "properties": {"id": {"type": "string"}, "alert": {"type": "boolean"},
                       "score": {"type": "number"}, "headline": {"type": "string"},
                       "reason": {"type": "string"}}}}}}


def _judge(model, system, schema, w, cands, ask) -> tuple[dict, dict]:
    content = _query_blocks(w) + [{"type": "text", "text": "LISTINGS TO JUDGE:"}] \
        + _cand_blocks(cands) + [{"type": "text", "text": ask}]
    out, meta = _run(model, system, content, schema)
    by_id = {}
    for v in (out or {}).get("verdicts", []):
        by_id[str(v.get("id", "")).strip()] = v
    return by_id, meta


def screen(model: str, w: dict, cands: list[dict]) -> tuple[dict, dict]:
    return _judge(model, SCREEN_SYS, SCREEN_SCHEMA, w, cands,
                  "Give one verdict per listing, using its LISTING id.")


def final(model: str, w: dict, cands: list[dict]) -> tuple[dict, dict]:
    return _judge(model, FINAL_SYS, FINAL_SCHEMA, w, cands,
                  "Give one verdict per listing, using its LISTING id.")
