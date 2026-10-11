# vinted-assistant

Watches Vinted filters for a specific shoe, or for things like it, and sends a
Telegram alert when something genuinely fits. Checking a search every few
minutes by hand is exhausting; this does it and only interrupts you when it
matters.

This repository is **public and holds code only.** Your watches, prompts,
reference photos and state live on the cluster in
`/extra/malmasik/vinted_assistant_data/`. Filter URLs reach GitHub Actions only
through the `WATCH_FILTERS` secret, keyed by opaque ids, so nothing public
reveals what you are hunting for.

## How a tick works

Every 15 minutes, cron on the cluster runs `scheduler/tick.sh`:

1. **Poll** - the cluster dispatches the `poll` workflow; GitHub Actions fetches
   each due watch's filter page, newest first, and returns the listings.
   Polling never happens from the cluster itself.
2. **New only** - listings already handled for that watch are skipped.
3. **SigLIP2 floor** - on the cluster's CPU, drops listings plainly unrelated to
   your photos. Free.
4. **Screen** - Claude (Haiku) drops the plainly wrong shoe. Runs as headless
   Claude Code **on your subscription**, not the API.
5. **Final judge** - Claude (Opus) decides what is worth interrupting you for,
   and writes a headline and a reason. Also on your subscription.
6. **Telegram** - one message per match, or a single ranked digest when there
   are more than 5 at once (always, on a watch's first poll).

## Adding a watch

Make a folder under `vinted_assistant_data/watches/`, put 2-5 photos of what you
want in it, and add a `watch.yaml`:

```yaml
name: ggdb-superstar-white-38
filter: https://www.vinted.it/catalog?search_text=golden%20goose%20superstar&size_ids[]=...&price_to=250
mode: exact               # exact = this specific shoe; similar = things like these
prompt: >
  Golden Goose Superstar in white leather with the black star and a glitter
  heel tab, size 38. Only that colourway - not the Ball Star or Hi-Star.
images: [q1.jpg, q2.jpg, q3.jpg]
favourite: q1.jpg         # optional; in similar mode, weighted most
notes: I like the worn, distressed finish most     # optional
every_min: 15             # optional
min_score: 0.85           # optional; leave out until you have seen real scores
active: true
```

The next tick picks it up. Its first poll judges everything already listed -
for an exact hunt that is the point, since the shoe may already be up - and
reports as one digest.

Build the `filter` URL by setting the filters you want on vinted.it and copying
the address bar. Sort order does not matter; newest-first is forced.

## Writing a request that works

**Your written request defines what counts.** The photos show what you mean;
where they and your text disagree, the text wins.

- **Say the colourway if it matters.** In `exact` mode a request that names
  only the model will match every colourway of that model. The first test asked
  for "the Ball Star" and correctly matched 66 of the 74 Ball Stars listed.
- **Name the near-misses you don't want.** "Not the Ball Star or Hi-Star" is
  the cheapest way to sharpen an exact hunt; the test rejected all 7 sibling
  models it was shown.
- **In `similar` mode, say what you like** in `notes`, and mark a `favourite`
  photo.

## Tuning

Every listing's path through the cascade is logged to
`vinted_assistant_data/logs/verdicts.jsonl`: dropped by the floor or the cap,
screened out, rejected by the final judge, below `min_score`, or alerted -
each with the judges' scores and reasons. Read it before changing anything.

`min_score` has no default on purpose. The final judge's score for the same
listing moved 0.82 -> 0.76 across identical runs, so a bar set from one run sits
inside that noise. Set one per watch only after seeing how that watch's scores
actually fall.

## What it costs

Nothing in API money - both judges run on your Claude subscription. They do use
its usage limit, which is shared with your interactive Claude Code sessions:

| | plan quota |
|---|---|
| per listing screened | ~$0.003-0.007 |
| per listing given a final judgement | ~$0.015 |
| a watch's first poll (96 listings) | ~$1.65, once |
| a narrow watch in steady state | a few cents a day |

The dollar figures are what Claude Code reports, not charges. If the limit is
reached, candidates are queued and judged after the reset - never dropped as
"no match".

## Operating

| | |
|---|---|
| stop everything | `touch /extra/malmasik/vinted_assistant_data/STOP` |
| pause one watch | `active: false` in its `watch.yaml` |
| who gets alerts | `vinted_assistant_data/telegram.env` (you only, by default) |
| tick log | `vinted_assistant_data/logs/tick.log` |
| per-tick stats | `vinted_assistant_data/logs/ticks.jsonl` |
| run one tick by hand | `scheduler/tick.sh --force --dry-run` |

## Known limits

- **Polls share GitHub's Azure address pool with the sold-item tracker.** Vinted
  refuses some requests from it; a refused poll is retried next tick.
- **The final judge sees one photo per listing** - the catalog thumbnail - plus
  title and price. It does not open the listing.
- **`haiku` resolves to Haiku 4.5** on the installed Claude Code (2.1.220).
- **A first poll takes about 7-8 minutes** on a 96-listing filter, mostly
  SigLIP2 on CPU. Steady-state ticks take seconds.
