# Bay Area Offbeat

A selective daily guide to strange, independent, and genuinely worthwhile things to do around the San Francisco Bay Area.

This is deliberately not a comprehensive event calendar. It favors small venues, experimental film/music/performance, local history, odd walks, independent art, cheap or free gatherings, and the occasional art-tech / cyberculture rabbit hole worth leaving the house for.

## How it works

1. Private discovery gathers RSS, newsletters, community calendars, and targeted research leads.
2. Each recommendation is independently checked against a current organizer, venue, or ticket page.
3. A deterministic validator rejects empty, expired, stale, duplicate, malformed, unsafe, or insufficiently verified public records.
4. The existing daily curator performs that research once. In private shadow mode, a restricted draft is normalized into canonical JSON, then the deterministic email renderer and site-publisher dry run consume that same payload; there is no second site-research agent.
5. After shadow-mode parity passes and public publication is explicitly enabled, the same validated JSON will render this site and the daily Bay Area Offbeat email.
6. The public page groups events in `America/Los_Angeles` as:
   - This week (Monday–Sunday)
   - Next week (Monday–Sunday)
   - On the radar (selectively chosen farther-out events)

Raw newsletter bodies, social posts, research notes, model reasoning, account data, and private staging files never belong in this repository.

## Local verification

This project intentionally uses only Python's standard library and plain HTML/CSS/JavaScript.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_events.py data/current.json
python3 scripts/build_site.py --input data/current.json --out dist
python3 -m http.server 8765 --directory dist --bind 127.0.0.1
```

Then open http://127.0.0.1:8765/.

## Editorial notes

- Event details change. Always check the linked organizer before heading out.
- A listing is an independent editorial recommendation, not an affiliation, guarantee, ticket seller, or availability promise.
- No tracking, advertising, accounts, email capture, user submissions, affiliate links, calendar subscription, or copied event imagery are part of v1.

## Publishing

GitHub Actions builds and deploys only the static `dist/` artifact to GitHub Pages. The daily publisher is designed to fail closed: validation, build, Git, or deployment failures leave the last known good public page intact.

The initial data workflow runs in private shadow mode before daily public updates are enabled. For the exact editorial rules, safe preview/publish procedure, correction path, and pause policy, see [docs/EDITORIAL_AND_OPERATIONS.md](docs/EDITORIAL_AND_OPERATIONS.md).
