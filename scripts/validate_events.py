#!/usr/bin/env python3
"""Validate the strict public Bay Area Offbeat canonical event document."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


PACIFIC = ZoneInfo("America/Los_Angeles")
TOP_LEVEL_FIELDS = {"schema_version", "generated_at", "timezone", "events"}
EVENT_FIELDS = {
    "id",
    "title",
    "starts_at",
    "ends_at",
    "all_day",
    "city",
    "neighborhood",
    "price_note",
    "official_url",
    "source_name",
    "why",
    "tags",
    "radar",
    "last_verified_at",
}
TEXT_LIMITS = {
    "title": 180,
    "city": 100,
    "neighborhood": 100,
    "price_note": 160,
    "source_name": 100,
    "why": 280,
    "tag": 48,
}
MAX_PUBLIC_DATA_AGE = timedelta(hours=36)
CANONICAL_AWARE_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$"
)


class JsonArgumentParser(argparse.ArgumentParser):
    """Emit the validator's stable JSON schema for command-line failures."""

    def error(self, message: str) -> NoReturn:
        del message
        print_report(
            ["invalid command line arguments"],
            0,
            utc_iso(datetime.now(timezone.utc)),
        )
        raise SystemExit(2)


class DuplicateJsonMemberError(ValueError):
    """Raised when a JSON object repeats a member name at any nesting level."""


def reject_duplicate_json_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMemberError(key)
        document[key] = value
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to a canonical event JSON document")
    parser.add_argument("--now", help="Validation time as an ISO timestamp")
    parser.add_argument(
        "--allow-http-host",
        action="append",
        default=[],
        help="exact hostname permitted to use legacy HTTP",
    )
    return parser


def parse_aware_timestamp(value: object) -> datetime | None:
    """Parse a browser-compatible timestamp safe for UTC and Pacific consumers."""
    if not isinstance(value, str) or not CANONICAL_AWARE_TIMESTAMP.fullmatch(value):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        if parsed.utcoffset() is None:
            return None
        parsed.astimezone(timezone.utc)
        # Canonical documents are rendered and bucketed in Pacific time. Reject
        # boundary timestamps that Python can parse but ZoneInfo cannot convert.
        parsed.astimezone(PACIFIC)
    except (ValueError, OverflowError):
        return None
    return parsed


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def deterministic_event_id(title: str, starts_at: str, official_url: str) -> str:
    material = "\n".join((normalize_text(title), starts_at, official_url))
    return "evt_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def plain_text_errors(
    value: object,
    path: str,
    *,
    limit: int,
    nullable: bool = False,
    minimum: int = 1,
) -> list[str]:
    if value is None and nullable:
        return []
    if not isinstance(value, str):
        return [f"{path} must be a string"]
    errors: list[str] = []
    if not value.strip():
        errors.append(f"{path} must be nonempty plain text")
    if "<" in value or ">" in value:
        errors.append(f"{path} must not contain markup")
    if any(unicodedata.category(character).startswith("C") for character in value):
        errors.append(f"{path} must not contain control characters")
    if len(value) > limit:
        errors.append(f"{path} exceeds {limit} characters")
    if len(value.strip()) < minimum:
        errors.append(f"{path} must contain at least {minimum} characters")
    return errors


def timestamp_error(value: object, path: str) -> tuple[datetime | None, list[str]]:
    parsed = parse_aware_timestamp(value)
    if parsed is None:
        return None, [f"{path} must be a valid timezone-aware ISO-8601 timestamp"]
    return parsed, []


def monday_for_pacific(moment: datetime) -> datetime:
    local_date = moment.astimezone(PACIFIC).date()
    return datetime.combine(
        local_date - timedelta(days=local_date.weekday()),
        datetime.min.time(),
        tzinfo=PACIFIC,
    )


def url_errors(value: object, path: str, allowed_http_hosts: set[str]) -> list[str]:
    if not isinstance(value, str):
        return [f"{path} must be an absolute HTTPS URL"]
    if (
        value != value.strip()
        or any(character.isspace() or unicodedata.category(character).startswith("C") for character in value)
    ):
        return [f"{path} must not contain whitespace or control characters"]
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port  # Force malformed/out-of-range port validation before accepting the URL.
    except ValueError:
        return [f"{path} must be an absolute HTTPS URL"]
    allowed_http = (
        parsed.scheme == "http"
        and hostname is not None
        and hostname.casefold() in allowed_http_hosts
    )
    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme != "https" and not allowed_http)
    ):
        return [f"{path} must be an absolute HTTPS URL or allowlisted HTTP host"]
    return []


def validate_tags(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{path} must be an array"]
    errors: list[str] = []
    if len(value) > 6:
        errors.append(f"{path} must contain at most 6 tags")
    seen: set[str] = set()
    for index, tag in enumerate(value):
        tag_path = f"{path}[{index}]"
        errors.extend(plain_text_errors(tag, tag_path, limit=TEXT_LIMITS["tag"]))
        if isinstance(tag, str):
            normalized = normalize_text(tag)
            if normalized in seen:
                errors.append(f"{tag_path} duplicates another tag")
            seen.add(normalized)
    return errors


def validate_event(
    event: object,
    index: int,
    now: datetime,
    generated_at: datetime | None,
    allowed_http_hosts: set[str],
    seen_ids: set[object],
    seen_material: set[tuple[str, datetime, str, str]],
    this_monday: datetime,
    next_sunday_end: datetime,
) -> list[str]:
    prefix = f"events[{index}]"
    if not isinstance(event, dict):
        return [f"{prefix} must be an object"]

    errors: list[str] = []
    missing = sorted(EVENT_FIELDS - event.keys())
    unexpected = sorted(event.keys() - EVENT_FIELDS)
    if missing:
        errors.append(f"{prefix} missing fields: {', '.join(missing)}")
    if unexpected:
        errors.append(f"{prefix} has unexpected fields: {', '.join(unexpected)}")
    if missing:
        return errors

    errors.extend(plain_text_errors(event["title"], f"{prefix}.title", limit=TEXT_LIMITS["title"]))
    errors.extend(plain_text_errors(event["city"], f"{prefix}.city", limit=TEXT_LIMITS["city"]))
    errors.extend(
        plain_text_errors(
            event["neighborhood"],
            f"{prefix}.neighborhood",
            limit=TEXT_LIMITS["neighborhood"],
            nullable=True,
        )
    )
    errors.extend(
        plain_text_errors(
            event["price_note"],
            f"{prefix}.price_note",
            limit=TEXT_LIMITS["price_note"],
            nullable=True,
        )
    )
    errors.extend(
        plain_text_errors(
            event["source_name"],
            f"{prefix}.source_name",
            limit=TEXT_LIMITS["source_name"],
        )
    )
    errors.extend(
        plain_text_errors(
            event["why"],
            f"{prefix}.why",
            limit=TEXT_LIMITS["why"],
            minimum=20,
        )
    )
    errors.extend(validate_tags(event["tags"], f"{prefix}.tags"))
    errors.extend(url_errors(event["official_url"], f"{prefix}.official_url", allowed_http_hosts))

    if type(event["all_day"]) is not bool:
        errors.append(f"{prefix}.all_day must be a boolean")
    if type(event["radar"]) is not bool:
        errors.append(f"{prefix}.radar must be a boolean")
    if not isinstance(event["id"], str):
        errors.append(f"{prefix}.id must be a string")

    starts_at, start_errors = timestamp_error(event["starts_at"], f"{prefix}.starts_at")
    errors.extend(start_errors)
    ends_at: datetime | None = None
    if event["ends_at"] is not None:
        ends_at, end_errors = timestamp_error(event["ends_at"], f"{prefix}.ends_at")
        errors.extend(end_errors)
    verified_at, verify_errors = timestamp_error(
        event["last_verified_at"], f"{prefix}.last_verified_at"
    )
    errors.extend(verify_errors)

    if starts_at is not None:
        if starts_at <= now:
            errors.append(f"{prefix}.starts_at must be later than validation time")
        if ends_at is not None and ends_at <= starts_at:
            errors.append(f"{prefix}.ends_at must be later than starts_at")
        if event["all_day"] is True:
            local_start = starts_at.astimezone(PACIFIC)
            if any((local_start.hour, local_start.minute, local_start.second, local_start.microsecond)):
                errors.append(f"{prefix}.all_day starts_at must be Pacific local midnight")
        if type(event["radar"]) is bool:
            if starts_at > next_sunday_end and event["radar"] is not True:
                errors.append(f"{prefix} after next Sunday must set radar true")
            if this_monday <= starts_at.astimezone(PACIFIC) <= next_sunday_end and event["radar"] is not False:
                errors.append(f"{prefix} within the next two weeks must set radar false")
    if verified_at is not None:
        if generated_at is not None and verified_at > generated_at:
            errors.append(f"{prefix}.last_verified_at must not be later than generated_at")
        if verified_at > now:
            errors.append(f"{prefix}.last_verified_at must not be later than validation time")
        elif now - verified_at > MAX_PUBLIC_DATA_AGE:
            errors.append(f"{prefix}.last_verified_at must be fresh within 36 hours")

    event_id = event["id"]
    if isinstance(event_id, str):
        if event_id in seen_ids:
            errors.append(f"{prefix}.id is a duplicate id")
        seen_ids.add(event_id)

    if (
        isinstance(event["title"], str)
        and isinstance(event["starts_at"], str)
        and isinstance(event["official_url"], str)
        and isinstance(event["id"], str)
    ):
        expected_id = deterministic_event_id(event["title"], event["starts_at"], event["official_url"])
        if event["id"] != expected_id:
            errors.append(f"{prefix}.id must equal the deterministic id")

    if starts_at is not None and isinstance(event["title"], str) and isinstance(event["city"], str):
        neighborhood = event["neighborhood"] if isinstance(event["neighborhood"], str) else ""
        material_key = (
            normalize_text(event["title"]),
            starts_at.astimezone(timezone.utc),
            normalize_text(event["city"]),
            normalize_text(neighborhood),
        )
        if material_key in seen_material:
            errors.append(f"{prefix} is a material duplicate")
        seen_material.add(material_key)

    return errors


def validate_document(
    document: object, now: datetime, allowed_http_hosts: set[str]
) -> list[str]:
    if not isinstance(document, dict):
        return ["document must be an object"]

    errors: list[str] = []
    missing = sorted(TOP_LEVEL_FIELDS - document.keys())
    unexpected = sorted(document.keys() - TOP_LEVEL_FIELDS)
    if missing:
        errors.append(f"missing top-level keys: {', '.join(missing)}")
    if unexpected:
        errors.append(f"unexpected top-level keys: {', '.join(unexpected)}")
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
        errors.append("schema_version must be the integer 1")
    if document.get("timezone") != "America/Los_Angeles":
        errors.append("timezone must equal America/Los_Angeles")

    generated_at, generated_errors = timestamp_error(document.get("generated_at"), "generated_at")
    errors.extend(generated_errors)
    if generated_at is not None:
        if generated_at > now:
            errors.append("generated_at must not be later than validation time")
        elif now - generated_at > MAX_PUBLIC_DATA_AGE:
            errors.append("generated_at must be fresh within 36 hours")

    events = document.get("events")
    if not isinstance(events, list):
        if "events" in document:
            errors.append("events must be an array")
        return errors
    if not events:
        errors.append("events must contain at least one verified future event")
        return errors

    reference_time = generated_at if generated_at is not None else now
    try:
        this_monday = monday_for_pacific(reference_time)
        next_sunday_end = this_monday + timedelta(days=14) - timedelta(microseconds=1)
    except OverflowError:
        errors.append("Pacific week window cannot be calculated safely")
        return errors
    seen_ids: set[object] = set()
    seen_material: set[tuple[str, datetime, str, str]] = set()
    for index, event in enumerate(events):
        errors.extend(
            validate_event(
                event,
                index,
                now,
                generated_at,
                allowed_http_hosts,
                seen_ids,
                seen_material,
                this_monday,
                next_sunday_end,
            )
        )
    return errors


def print_report(errors: list[str], event_count: int, validated_at: str) -> None:
    print(
        json.dumps(
            {
                "valid": not errors,
                "errors": errors,
                "event_count": event_count,
                "validated_at": validated_at,
            },
            separators=(",", ":"),
        )
    )


def validation_now(value: str | None) -> tuple[datetime | None, datetime, list[str]]:
    report_now = datetime.now(timezone.utc)
    if value is None:
        return report_now, report_now, []
    parsed = parse_aware_timestamp(value)
    if parsed is None:
        return None, report_now, ["--now must be a valid timezone-aware ISO-8601 timestamp"]
    return parsed, parsed, []


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now, report_now, now_errors = validation_now(args.now)
    try:
        with open(args.input, encoding="utf-8") as handle:
            document: object = json.load(
                handle,
                object_pairs_hook=reject_duplicate_json_members,
            )
    except DuplicateJsonMemberError:
        print_report(["input contains duplicate JSON member"], 0, utc_iso(report_now))
        return 1
    except RecursionError:
        print_report(["input JSON nesting exceeds safe parser limits"], 0, utc_iso(report_now))
        return 1
    except json.JSONDecodeError:
        print_report(["input is not valid JSON"], 0, utc_iso(report_now))
        return 1
    except (OSError, UnicodeError):
        print_report(["could not read input file"], 0, utc_iso(report_now))
        return 1

    event_count = (
        len(document.get("events", []))
        if isinstance(document, dict) and isinstance(document.get("events"), list)
        else 0
    )
    if now_errors:
        print_report(now_errors, event_count, utc_iso(report_now))
        return 1
    assert now is not None
    errors = validate_document(
        document,
        now,
        {host.casefold() for host in args.allow_http_host if isinstance(host, str)},
    )
    print_report(errors, event_count, utc_iso(now))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
