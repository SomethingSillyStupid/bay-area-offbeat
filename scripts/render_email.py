#!/usr/bin/env python3
"""Render a plain-text Bay Area Offbeat email from canonical event JSON.

This script deliberately performs presentation only. `validate_events.py` owns the
public-data validity gate; callers should validate first and render the exact same
canonical payload they publish to the site.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def parse_timestamp(value: str) -> datetime:
    """Return an aware timestamp, accepting a trailing UTC ``Z``."""
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return timestamp


def local_day_label(timestamp: datetime) -> str:
    """Format a Pacific timestamp as a compact human event-day label."""
    local = timestamp.astimezone(PACIFIC)
    return f"{local.strftime('%A, %b')} {local.day}"


def local_time_label(event: dict[str, Any]) -> str:
    """Format an event's verified start as an all-day or local time label."""
    start = parse_timestamp(event["starts_at"]).astimezone(PACIFIC)
    if event.get("all_day"):
        return f"{local_day_label(start)} · all day"
    clock = start.strftime("%I:%M %p").lstrip("0")
    return f"{local_day_label(start)} · {clock}"


def bucket_events(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group canonical events by the current/next Pacific calendar weeks."""
    generated = parse_timestamp(document["generated_at"]).astimezone(PACIFIC)
    this_monday = generated.date() - timedelta(days=generated.weekday())
    next_monday = this_monday + timedelta(days=7)
    radar_start = this_monday + timedelta(days=14)
    buckets: dict[str, list[dict[str, Any]]] = {
        "this_week": [],
        "next_week": [],
        "radar": [],
    }

    for event in document.get("events", []):
        start = parse_timestamp(event["starts_at"]).astimezone(PACIFIC)
        local_date = start.date()
        if this_monday <= local_date < next_monday:
            buckets["this_week"].append(event)
        elif next_monday <= local_date < radar_start:
            buckets["next_week"].append(event)
        elif local_date >= radar_start and event.get("radar") is True:
            buckets["radar"].append(event)

    for events in buckets.values():
        events.sort(key=lambda event: (parse_timestamp(event["starts_at"]), event["id"]))
    return buckets


def format_event(event: dict[str, Any]) -> list[str]:
    """Render one validated event into a small readable plain-text block."""
    location = event["city"]
    if event.get("neighborhood"):
        location = f"{event['neighborhood']}, {location}"
    details: list[str] = [f"• {event['title']}", f"  {local_time_label(event)} · {location}"]
    if event.get("price_note"):
        details[-1] += f" · {event['price_note']}"
    details.append(f"  Why it’s on the list: {event['why']}")
    details.append(f"  Details: {event['official_url']}")
    return details


def format_section(title: str, events: list[dict[str, Any]]) -> list[str]:
    """Render one email section without inventing a substitute event."""
    lines = [title]
    if not events:
        lines.append("Nothing verified for this window yet — check back after the next update.")
        return lines
    for event in events:
        lines.extend(format_event(event))
    return lines


def render(document: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic subject/body/counts for a canonical event document."""
    generated = parse_timestamp(document["generated_at"]).astimezone(PACIFIC)
    buckets = bucket_events(document)
    sections = [
        format_section("THIS WEEK", buckets["this_week"]),
        format_section("NEXT WEEK", buckets["next_week"]),
        format_section("ON THE RADAR", buckets["radar"]),
    ]
    body_lines = [
        "Bay Area Offbeat",
        "A selective daily guide to strange, independent, worthwhile things around the Bay.",
        "",
    ]
    for index, section in enumerate(sections):
        if index:
            body_lines.append("")
        body_lines.extend(section)
    body_lines.extend(
        [
            "",
            "Details change. Please check the organizer before heading out.",
            "Independent editorial recommendations; no affiliation or availability guarantee.",
        ]
    )
    subject = f"Bay Area offbeat best-of — {generated.strftime('%a %b')} {generated.day}"
    return {
        "subject": subject,
        "body": "\n".join(body_lines),
        "counts": {name: len(events) for name, events in buckets.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/current.json", help="canonical event JSON")
    parser.add_argument("--json", action="store_true", help="emit subject/body/counts as JSON")
    args = parser.parse_args(argv)
    try:
        document = json.loads(Path(args.input).read_text(encoding="utf-8"))
        rendered = render(document)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"render failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rendered, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Subject: {rendered['subject']}\n\n{rendered['body']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
