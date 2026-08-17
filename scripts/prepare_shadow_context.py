#!/usr/bin/env python3
"""Run the existing discovery collector and prepare one private shadow-run folder."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn


DEFAULT_COLLECTOR = Path.home() / ".hermes" / "scripts" / "bay_area_offbeat_collect.py"
DEFAULT_STAGING_ROOT = Path.home() / ".hermes" / "offbeat-staging"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ContextError(Exception):
    """A compact non-sensitive failure safe for scheduler output."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        emit({"status": "error", "message": "invalid arguments"})
        raise SystemExit(2)


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--collector", default=str(DEFAULT_COLLECTOR))
    parser.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    parser.add_argument("--run-id")
    return parser.parse_args(argv)


def duplicate_free_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ContextError("collector output is invalid")
        output[key] = value
    return output


def default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return f"{stamp}-{os.getpid()}"


def safe_run_id(value: str) -> str:
    if not RUN_ID_RE.fullmatch(value) or ".." in value:
        raise ContextError("invalid run identifier")
    return value


def path_has_symlink_component(value: Path) -> bool:
    """Reject a configured private root that traverses any symlink component."""
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


def create_run_dir(staging_root_value: str, run_id: str) -> Path:
    configured_root = Path(staging_root_value).expanduser()
    if path_has_symlink_component(configured_root):
        raise ContextError("private staging is unavailable")
    root = configured_root.resolve(strict=False)
    if root.is_symlink():
        raise ContextError("private staging is unavailable")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    runs = root / "runs"
    if runs.is_symlink():
        raise ContextError("private staging is unavailable")
    runs.mkdir(exist_ok=True, mode=0o700)
    os.chmod(runs, 0o700)
    run_dir = runs / run_id
    try:
        run_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ContextError("private shadow run already exists") from exc
    return run_dir.resolve(strict=True)


def atomic_write_json(destination: Path, value: object) -> None:
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


def collector_document(collector_path: Path) -> dict[str, Any]:
    if (
        path_has_symlink_component(collector_path)
        or collector_path.is_symlink()
        or not collector_path.is_file()
    ):
        raise ContextError("collector is unavailable")
    result = subprocess.run(
        [sys.executable, str(collector_path)],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode != 0:
        raise ContextError("collector failed")
    try:
        payload = json.loads(result.stdout, object_pairs_hook=duplicate_free_object)
    except ContextError:
        raise
    except json.JSONDecodeError as exc:
        raise ContextError("collector output is invalid") from exc
    if not isinstance(payload, dict):
        raise ContextError("collector output is invalid")
    if "shadow_run" in payload:
        raise ContextError("collector output is invalid")
    return payload


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        run_id = safe_run_id(args.run_id or default_run_id())
        run_dir = create_run_dir(args.staging_root, run_id)
        collector = collector_document(Path(args.collector).expanduser())
        atomic_write_json(run_dir / "collector.json", collector)
        context = dict(collector)
        context["shadow_run"] = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "collector_path": str(run_dir / "collector.json"),
            "draft_path": str(run_dir / "draft.json"),
            "candidate_path": str(run_dir / "candidate.json"),
            "validation_path": str(run_dir / "validation.json"),
            "email_preview_path": str(run_dir / "email.json"),
            "shadow_report_path": str(run_dir / "shadow-report.json"),
            "publisher_dry_run_path": str(run_dir / "publisher-dry-run.json"),
        }
        emit(context)
        return 0
    except ContextError as exc:
        emit({"status": "error", "message": str(exc)})
        return 1
    except (OSError, UnicodeError, ValueError):
        emit({"status": "error", "message": "private staging failed safely"})
        return 1
    except Exception:
        emit({"status": "error", "message": "private staging failed safely"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
