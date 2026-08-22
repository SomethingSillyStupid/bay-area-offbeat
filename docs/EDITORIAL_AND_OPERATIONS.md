# Bay Area Offbeat: editorial and operations guide

Bay Area Offbeat is a small, selective guide—not a comprehensive calendar and not an event-submission service.

## Editorial contract

A public listing needs all of the following:

- a current direct organizer, venue, or ticket page;
- an exact Pacific date/time (or a clearly supported all-day record);
- a direct HTTPS details link;
- a concise, original reason it belongs on the list;
- a plain-language location and only price/access claims verified on the source page.

Do not publish a feed publish-date as an event date. Do not publish raw newsletter copy, social posts, account data, private research notes, model reasoning, browser traces, credentials, copied images, or speculative availability claims.

The public view is deliberately limited to:

- **This week:** the Pacific Monday–Sunday block containing `generated_at`;
- **Next week:** the following Pacific Monday–Sunday block;
- **On the radar:** later events explicitly marked `radar: true`.

If the field is thin, publish a thin list. No padding, no synthetic “events,” no invented prices.

## Canonical data boundary

`data/current.json` is the only public event input. It has one normalized event list and is the source for both the site and the email renderer.

Before it can be published, run:

```sh
python3 scripts/validate_events.py data/current.json
python3 scripts/render_email.py --input data/current.json --json
python3 scripts/build_site.py --input data/current.json --out dist
```

The validator rejects malformed, excessively nested, or empty data; duplicate JSON object members; unexpected fields; unsafe text; invalid URL/date records; expired records; duplicate records; editorial takes over 280 characters; more than six tags; stale payloads or source-verification timestamps (older than 36 hours); verification timestamps later than their payload generation time; and invalid week/radar placement. Public timestamps must use browser-compatible canonical ISO-8601 form: `YYYY-MM-DDTHH:MM:SS[.sss](Z|±HH:MM)` and be safely convertible to both UTC and Pacific time. Equal `starts_at` values sort by the validated ASCII event ID in both renderers, never locale-dependent title collation. The renderer is presentation-only; it does not bless data on its own. The browser independently hides an edition that exceeds the same 36-hour freshness budget.

## Safe manual preview

Use an input that has already passed validation:

```sh
python3 scripts/publish_daily.py --input /path/to/validated-events.json --dry-run
```

A dry run runs the publisher’s gate checks and isolated build without staging, committing, pushing, or changing the source repository.

To inspect the built page locally:

```sh
python3 scripts/build_site.py --input data/current.json --out dist
python3 -m http.server 8765 --directory dist --bind 127.0.0.1
```

## Publication rules

`scripts/publish_daily.py` is the only supported writer for public event data. It is designed to:

1. require `--repo` to name the canonical Git checkout root, then reject dirty/diverged/unexpected repositories before work begins;
2. require every effective `origin` fetch and push URL to match the explicitly expected remote;
3. create an isolated worktree from the expected remote `main` branch;
4. copy only validated event JSON and an optional dated snapshot;
5. rerun validation, tests, and static build in that worktree;
6. stage only allowlisted `data/` paths;
7. push a normal fast-forward commit—never a force push.

The publisher lock path must be outside the canonical checkout root and must name a regular non-symlink file. An empty lock file may remain after a run; closing its advisory lock releases it, so a stale file does not block the next publisher invocation.

If any gate fails, the previous public build stays live. Fix the candidate or environment, then retry from the original candidate file. Do not hand-copy a private candidate into `data/current.json` in the long-lived checkout as a workaround.

## Correcting a listing

1. Re-check the organizer page and create a corrected private candidate.
2. Re-run validation and inspect the email/site render against that exact candidate.
3. Run publisher dry-run, then the normal publisher only after it passes.
4. If an already-published record must be removed immediately, publish a corrected validated payload; retain private provenance for the correction.

Git history provides a straightforward code/data rollback, but a new validated correction is preferred for event facts because time keeps moving in its customary rude manner.

## Automation status

The original daily email runs through one curator job; no second site-research job is permitted. It creates a restricted private draft, derives canonical JSON with deterministic IDs, validates it, renders the email preview from that exact payload, and invokes `stage_daily_candidate.py --publisher-dry-run`. That explicit flag writes a private `publisher-dry-run.json` receipt only when the isolated publisher passes without committing or pushing.

The scheduled daily job `5b08e6700274` runs this sequence through `/root/.hermes/scripts/bay_area_offbeat_shadow_collect.py`, then sends the deterministic email through the private sender with duplicate suppression and Sent-folder proof. Only after that proof does it invoke `publish_daily.py` with the exact same private candidate. The publisher repeats validation/build/test gates in an isolated worktree and can stage only public data paths before a normal non-force push.

A failed draft, validation, sender, or publisher gate leaves the public site unchanged. A verified email followed by publisher failure must not be resent automatically; the next daily run creates a fresh candidate. To temporarily pause public publishing while retaining the email, return the cron prompt to its `--publisher-dry-run` shadow stop rather than pausing the whole daily job.

## Local verification suite

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/*.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/validate_events.py data/current.json
python3 scripts/build_site.py --input data/current.json --out dist
```

GitHub Actions repeats the compile, test, validation, and build gates before deploying the artifact to GitHub Pages.
