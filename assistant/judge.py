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
               + (f"\nWhat I like most: {w['notes']}" if w["notes"] else "")
               + (f"\n\nMY VINTED FILTER ASKS FOR\n{w['filter_words']}" if w.get("filter_words") else "")}]
    for i, p in enumerate(w["images"], 1):
        tag = " (my favourite)" if w["favourite"] == p else ""
        blocks += [{"type": "text", "text": f"Reference photo R{i}{tag}:"}, _img(p)]
    return blocks


def _cand_blocks(cands: list[dict]) -> list:
    """Screening sees the catalog card: one photo, the title, the price."""
    blocks = []
    for c in cands:
        price = f"EUR {c['price']:.0f}" if c.get("price") else "price unknown"
        extra = " · ".join(x for x in (c.get("brand"), c.get("line2")) if x)
        blocks += [{"type": "text", "text":
                    f"LISTING {c['id']}: \"{c['title']}\" - {price}"
                    + (f" ({extra})" if extra else "")}, _img(c["path"])]
    return blocks


def _seller(pg: dict) -> str:
    n, r = pg.get("seller_reviews"), pg.get("seller_rating")
    if n is None:
        return "seller record not shown"
    return f"{n} reviews" + (f", rating {r * 5:.1f}/5" if r is not None and n else "")


def _full_blocks(cands: list[dict]) -> list:
    """The final judge sees the listing's own page: every photo, the
    description, the claimed condition and the seller's record."""
    blocks = []
    for c in cands:
        pg = c.get("page") or {}
        price = f"EUR {c['price']:.0f}" if c.get("price") else "price unknown"
        facts = [f"price {price}"]
        if c.get("price_ref"):
            facts.append(f"typical asking price for this brand and category {c['price_ref']}")
        for label, key in (("seller's condition", "condition"), ("size", "size"),
                           ("colour", "color"), ("material", "material"),
                           ("uploaded", "uploaded"), ("likes", "likes")):
            if pg.get(key) not in (None, ""):
                facts.append(f"{label}: {pg[key]}")
        facts.append(_seller(pg))
        if c.get("flags"):
            facts.append("note: " + "; ".join(c["flags"]))
        desc = (pg.get("description") or "").strip()
        text = (f"LISTING {c['id']}: \"{c['title']}\"\n" + "\n".join(facts)
                + (f"\nDescription: {desc[:1200]}" if desc else "\nDescription: (none)"))
        if not c.get("paths"):
            text += "\n(only the catalog thumbnail is available - the page could not be read)"
        blocks.append({"type": "text", "text": text})
        for i, path in enumerate(c.get("paths") or [c["path"]], 1):
            blocks += [{"type": "text", "text": f"{c['id']} photo {i}:"}, _img(path)]
    return blocks


MODES = """\
My written request defines what counts. The reference photos show what I \
mean; where they and my text differ, my text wins.
exact mode: I am hunting one specific item. It must be the same model as my \
references. If my text names a colourway, material or detail, that must match \
too; if it does not, judge by the model and treat colourway as a preference.
similar mode: I want things in the spirit of my references. Judge how well \
each listing fits the style, shape and details I describe, leaning on my \
favourite photo and on what I said I like most."""

SCREEN_SYS = f"""\
You pre-screen second-hand listings on Vinted for one buyer, from each \
listing's main photo and title; a stronger judge then reviews everything you \
keep, with all the photos. Your only job is to drop listings that are plainly \
not what is wanted: a different kind of item than the filter's category or my \
request, another brand, or a different model when my text names one. Sellers \
often file items in the wrong category, so judge the kind of item from the \
photo, not from where it was listed. Do NOT judge condition, finish, size or \
authenticity - leave those to the next judge. When unsure, keep it.
{MODES}
Answer only through the JSON schema, one verdict per listing."""

FINAL_SYS = f"""\
You review second-hand listings on Vinted for one buyer and decide which are \
worth an alert on their phone. Alert only when a listing passes all four \
checks below; each alert interrupts them, and a fake or a worn-out item \
described as "very good" is exactly what they are paying you to catch.

1. MATCHES. It is what my filter and my request describe: the right kind of \
item (judge from the photos - sellers mis-file), the right brand, and any \
model, size, colour or detail my text names.

2. CONDITION. Sellers routinely overstate condition. Grade what the photos \
show, independently of the seller's claim, on Vinted's own scale: \
new_tags (Nuovo con cartellino: unworn, tags attached), new (Nuovo senza \
cartellino: never worn or used, no tags), very_good (Ottime: lightly used, \
flaws only visible up close), good (Buone: clearly used, visible wear that \
does not affect use), satisfactory (Discrete: heavy wear or defects). \
List every visible defect. Look where wear shows on this kind of item - \
shoes: outsole and heel wear, heel tips, toe-box creasing, scuffs, insole \
darkening, yellowed soles, peeling glue; bags and small leather goods: \
corners, handle and strap darkening, edge-paint cracking, hardware scratches \
and plating loss, interior stains, pen marks, lost shape; clothing: pilling, \
fading, stains, holes, stretched collars and cuffs; glasses: lens and frame \
scratches, bent arms; jewellery and watches: scratches, tarnish, stones, \
clasps. Turn-off defects - set turn_off to true for any of them unless my \
request says I accept it: stains, holes or tears, cracked or peeling leather \
or coating, broken or missing hardware or parts, heavy sole or heel wear, \
repainting or re-dyeing, mould or smell mentioned, heavy odour of use. If the \
photos do not show the areas that matter (no sole for shoes, no interior or \
corners for a bag, everything far away or filtered), say so in photo_gaps: \
missing evidence is a reason for doubt, not for trust.

3. AUTHENTICITY. Weigh the cues for and against: logo and lettering shape and \
spacing, stitching quality, hardware engraving and weight, heat stamps, date \
codes or serial tags, material, lining, packaging, and a price far below the \
typical asking price. Photos cannot prove an item genuine, so give a risk - \
low, medium or high - never a certainty, and name the cues behind it.

4. SELLER. The seller's review count and rating are given. Few or no reviews \
together with a low price and any authenticity doubt is a pattern to flag.

{MODES}
Then decide: alert, an overall score from 0 to 1, a headline of at most 12 \
words saying what it is and why it fits, and a one-sentence reason that \
mentions the condition you see. Answer only through the JSON schema."""

SCREEN_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["verdicts"], "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "keep", "score", "reason"],
        "properties": {"id": {"type": "string"}, "keep": {"type": "boolean"},
                       "score": {"type": "number"}, "reason": {"type": "string"}}}}}}

GRADES = ["new_tags", "new", "very_good", "good", "satisfactory", "cannot_tell"]
RISK = ["low", "medium", "high"]
FINAL_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["verdicts"], "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "matches", "match_reason", "condition_seen", "defects",
                     "turn_off", "photo_gaps", "fake_risk", "fake_cues", "seller_risk",
                     "alert", "score", "headline", "reason"],
        "properties": {
            "id": {"type": "string"},
            "matches": {"type": "boolean"}, "match_reason": {"type": "string"},
            "condition_seen": {"type": "string", "enum": GRADES},
            "defects": {"type": "array", "items": {"type": "string"}},
            "turn_off": {"type": "boolean"}, "photo_gaps": {"type": "string"},
            "fake_risk": {"type": "string", "enum": RISK}, "fake_cues": {"type": "string"},
            "seller_risk": {"type": "string", "enum": RISK},
            "alert": {"type": "boolean"}, "score": {"type": "number"},
            "headline": {"type": "string"}, "reason": {"type": "string"}}}}}}


def _judge(model, system, schema, w, blocks, ask) -> tuple[dict, dict]:
    content = _query_blocks(w) + [{"type": "text", "text": "LISTINGS TO JUDGE:"}] \
        + blocks + [{"type": "text", "text": ask}]
    out, meta = _run(model, system, content, schema)
    by_id = {}
    for v in (out or {}).get("verdicts", []):
        by_id[str(v.get("id", "")).strip()] = v
    return by_id, meta


def screen(model: str, w: dict, cands: list[dict]) -> tuple[dict, dict]:
    return _judge(model, SCREEN_SYS, SCREEN_SCHEMA, w, _cand_blocks(cands),
                  "Give one verdict per listing, using its LISTING id.")


def final(model: str, w: dict, cands: list[dict]) -> tuple[dict, dict]:
    return _judge(model, FINAL_SYS, FINAL_SCHEMA, w, _full_blocks(cands),
                  "Give one verdict per listing, using its LISTING id.")
