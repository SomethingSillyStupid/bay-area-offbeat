#!/usr/bin/env python3
"""Stage one private Bay Area Offbeat draft as validated canonical event data.

This helper is intentionally not a publisher. It converts an editor's restricted
private draft into canonical JSON, validates it, and produces the deterministic
email preview that a later public-publish gate would use. All output is confined
to one private staging run directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from validate_events import deterministic_event_id


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGING_ROOT = Path.home() / ".hermes" / "offbeat-staging"
PUBLISHER = REPO_ROOT / "scripts" / "publish_daily.py"
PUBLISHER_DRY_RUN_SUCCESS = {
    "published": False,
    "dry_run": True,
    "message": "dry run passed",
}
DRAFT_EVENT_FIELDS = frozenset(
    {
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
)


class StageError(Exception):
    """A non-sensitive rejection intended for machine-readable reports."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep command-line failures compact and machine-readable."""

    def error(self, message: str) -> NoReturn:
        del message
        emit({"status": "rejected", "message": "invalid arguments"})
        raise SystemExit(2)


def emit(report: dict[str, object]) -> None:
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, help="private draft.json path")
    parser.add_argument("--run-dir", required=True, help="private per-run staging directory")
    parser.add_argument(
        "--staging-root",
        default=str(DEFAULT_STAGING_ROOT),
        help="private staging root that must contain --run-dir",
    )
    parser.add_argument(
        "--generated-at",
        help="canonical generated_at timestamp; defaults to current UTC",
    )
    parser.add_argument(
        "--now",
        help="optional validator time, used only for deterministic test/recovery runs",
    )
    parser.add_argument(
        "--publisher-dry-run",
        action="store_true",
        help="exercise the isolated site publisher without committing or pushing",
    )
    return parser.parse_args(argv)


def path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def path_has_symlink_component(value: Path) -> bool:
    """Reject private staging paths that traverse symlink components."""
    path = value.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def private_run_dir(value: str, staging_root_value: str) -> Path:
    configured_staging_root = Path(staging_root_value).expanduser()
    if path_has_symlink_component(configured_staging_root):
        raise StageError("private staging root must not be a symlink")
    staging_root = configured_staging_root.resolve(strict=False)
    run_dir = Path(value).expanduser()
    if path_has_symlink_component(run_dir) or run_dir.is_symlink() or not run_dir.is_dir():
        raise StageError("private run directory is unavailable")
    resolved_run_dir = run_dir.resolve(strict=True)
    if not path_is_within(resolved_run_dir, staging_root):
        raise StageError("run directory must be inside private staging")
    return resolved_run_dir


def exact_draft_path(value: str, run_dir: Path) -> Path:
    draft_path = Path(value).expanduser()
    expected = run_dir / "draft.json"
    if draft_path.is_symlink() or draft_path.resolve(strict=False) != expected:
        raise StageError("draft path must be the private run draft.json")
    if not draft_path.is_file():
        raise StageError("draft input is missing")
    return draft_path


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StageError("draft JSON has duplicate object members")
        result[key] = value
    return result


def load_draft(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except StageError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StageError("draft JSON is unreadable") from exc
    if not isinstance(value, dict) or set(value) != {"events"}:
        raise StageError("draft must contain only an events array")
    events = value.get("events")
    if not isinstance(events, list):
        raise StageError("draft events must be an array")
    for event in events:
        if not isinstance(event, dict):
            raise StageError("draft events must be objects")
        unexpected = set(event) - DRAFT_EVENT_FIELDS
        if unexpected:
            raise StageError("draft event has unexpected fields")
    return value


def generated_timestamp(value: str | None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def derive_candidate(draft: dict[str, Any], generated_at: str) -> dict[str, Any]:
    canonical_events: list[dict[str, Any]] = []
    for event in draft["events"]:
        title = event.get("title")
        starts_at = event.get("starts_at")
        official_url = event.get("official_url")
        if not all(isinstance(value, str) for value in (title, starts_at, official_url)):
            raise StageError("draft event is missing deterministic ID material")
        canonical_event = {field: event.get(field) for field in DRAFT_EVENT_FIELDS}
        canonical_event["id"] = deterministic_event_id(title, starts_at, official_url)
        canonical_events.append(canonical_event)
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "timezone": "America/Los_Angeles",
        "events": canonical_events,
    }


def atomic_write_json(destination: Path, value: object) -> None:
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise StageError("private staging output path is unavailable")
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def run_json_command(arguments: list[str]) -> tuple[int, dict[str, Any] | None]:
    result = subprocess.run(
        arguments,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None
    return result.returncode, payload if isinstance(payload, dict) else None


def run_publisher_dry_run(candidate_path: Path, now: str | None) -> dict[str, Any]:
    """Exercise the real publisher without allowing a public repository write."""
    arguments = [
        sys.executable,
        str(PUBLISHER),
        "--input",
        str(candidate_path),
        "--repo",
        str(REPO_ROOT),
        "--dry-run",
    ]
    if now:
        arguments.extend(["--now", now])
    publisher_exit, publisher_report = run_json_command(arguments)
    if (
        publisher_exit != 0
        or publisher_report is None
        or publisher_report != PUBLISHER_DRY_RUN_SUCCESS
    ):
        raise StageError("publisher dry run failed")
    return publisher_report


def rejected(message: str, run_dir: Path | None = None) -> int:
    report: dict[str, object] = {"status": "rejected", "message": message}
    if run_dir is not None:
        try:
            atomic_write_json(run_dir / "shadow-report.json", report)
        except StageError:
            pass
    emit(report)
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    run_dir: Path | None = None
    try:
        run_dir = private_run_dir(args.run_dir, args.staging_root)
        draft_path = exact_draft_path(args.draft, run_dir)
        draft = load_draft(draft_path)
        candidate = derive_candidate(draft, generated_timestamp(args.generated_at))
        candidate_path = run_dir / "candidate.json"
        atomic_write_json(candidate_path, candidate)

        validator_arguments = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_events.py"),
            str(candidate_path),
        ]
        if args.now:
            validator_arguments.extend(["--now", args.now])
        validator_exit, validation = run_json_command(validator_arguments)
        if validation is None:
            return rejected("candidate validation failed", run_dir)
        atomic_write_json(run_dir / "validation.json", validation)
        if validator_exit != 0 or validation.get("valid") is not True:
            return rejected("candidate validation failed", run_dir)

        render_exit, preview = run_json_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "render_email.py"),
                "--input",
                str(candidate_path),
                "--json",
            ]
        )
        if render_exit != 0 or preview is None:
            return rejected("email preview failed", run_dir)
        atomic_write_json(run_dir / "email.json", preview)

        if args.publisher_dry_run:
            atomic_write_json(
                run_dir / "publisher-dry-run.json",
                run_publisher_dry_run(candidate_path, args.now),
            )

        report: dict[str, object] = {
            "status": (
                "shadow_publish_dry_run_passed"
                if args.publisher_dry_run
                else "ready_for_shadow_publish_dry_run"
            ),
            "public_publish": False,
            "publisher_dry_run": args.publisher_dry_run,
            "event_count": len(candidate["events"]),
            "event_ids": [event["id"] for event in candidate["events"]],
            "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            "email_subject": preview.get("subject"),
            "email_counts": preview.get("counts"),
        }
        atomic_write_json(run_dir / "shadow-report.json", report)
        emit(report)
        return 0
    except StageError as exc:
        return rejected(str(exc), run_dir)
    except (OSError, UnicodeError, ValueError):
        return rejected("private staging failed safely", run_dir)
    except Exception:
        return rejected("private staging failed safely", run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
