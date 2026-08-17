#!/usr/bin/env python3
"""Publish validated Bay Area Offbeat event data through an isolated worktree.

The long-lived checkout is deliberately never used as the publication worktree.
A failed gate therefore leaves both the checked-out source tree and the existing
GitHub Pages deployment unchanged.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, NoReturn


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "https://github.com/SomethingSillyStupid/bay-area-offbeat.git"
REQUIRED_REPO_PATHS = (
    "scripts/validate_events.py",
    "scripts/build_site.py",
    "site/index.html",
    "site/styles.css",
    "site/app.js",
    "data/current.json",
    "tests",
)
SNAPSHOT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PublishError(Exception):
    """A deliberately non-sensitive failure safe to expose in a JSON report."""


def emit(report: dict[str, object]) -> None:
    """Write exactly one compact machine-readable report to standard output."""
    print(json.dumps(report, separators=(",", ":"), ensure_ascii=False))


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep command-line failures machine-readable and traceback-free."""

    def error(self, message: str) -> NoReturn:
        del message
        emit({"published": False, "message": "invalid arguments"})
        raise SystemExit(2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="validated canonical JSON file")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="long-lived repository checkout")
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        help="expected origin URL; must match the checked-out repository",
    )
    parser.add_argument("--branch", default="main", help="publication branch")
    parser.add_argument(
        "--snapshot-date",
        help="optional YYYY-MM-DD public payload snapshot date",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run all validation/build gates without staging, committing, or pushing",
    )
    parser.add_argument(
        "--now",
        help="optional ISO validation time forwarded to validate_events.py",
    )
    parser.add_argument(
        "--lock-path",
        default="/tmp/bay-area-offbeat-publish.lock",
        help="process lock path; it must be outside the repository",
    )
    return parser.parse_args(argv)


def command(
    args: list[str],
    *,
    cwd: Path,
    check_message: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run one command without leaking its output into public JSON reports."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except OSError as exc:
        raise PublishError(check_message) from exc
    if result.returncode != 0:
        raise PublishError(check_message)
    return result


def git_output(repo: Path, args: list[str], *, message: str, env: dict[str, str]) -> str:
    result = command(["git", *args], cwd=repo, check_message=message, env=env)
    return result.stdout.strip()


def normalize_remote(value: str) -> str:
    """Compare URLs conservatively while tolerating an accidental trailing slash."""
    return value.rstrip("/")


def path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def canonical_worktree_root(repo: Path, env: dict[str, str]) -> Path:
    """Require the caller to name the actual Git checkout root, not a subdirectory."""
    if not repo.is_dir():
        raise PublishError("repository directory is missing")
    top_level = git_output(
        repo,
        ["rev-parse", "--show-toplevel"],
        message="not a git repository",
        env=env,
    )
    try:
        canonical_root = Path(top_level).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PublishError("not a git repository") from exc
    if repo.resolve(strict=False) != canonical_root:
        raise PublishError("repository path must be the checkout root")
    return canonical_root


def safe_snapshot_path(snapshot_date: str | None) -> str | None:
    if snapshot_date is None:
        return None
    if not SNAPSHOT_DATE_RE.fullmatch(snapshot_date):
        raise PublishError("invalid snapshot date")
    try:
        datetime.strptime(snapshot_date, "%Y-%m-%d")
    except ValueError as exc:
        raise PublishError("invalid snapshot date") from exc
    return f"data/snapshots/{snapshot_date}.json"


def atomic_write(destination: Path, payload: bytes) -> None:
    """Write a file through a same-directory temp file and atomic replacement."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def publication_lock(lock_path: Path, repo: Path) -> Iterator[None]:
    """Serialize publisher processes through a regular, non-symlink lock file.

    A closed ``flock`` releases automatically, so the empty lock file is retained
    rather than deleting a caller-controlled pathname during cleanup.
    """
    expanded_lock = lock_path.expanduser()
    if not expanded_lock.is_absolute():
        expanded_lock = Path.cwd() / expanded_lock
    if not expanded_lock.name:
        raise PublishError("invalid lock path")
    lock_file = expanded_lock.parent.resolve(strict=False) / expanded_lock.name
    if lock_file.is_symlink():
        raise PublishError("lock path must not be a symlink")
    if path_is_within(lock_file.resolve(strict=False), repo):
        raise PublishError("lock path must be outside the repository")

    descriptor: int | None = None
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_status = lock_file.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(lock_status.st_mode):
                raise PublishError("lock path must be a regular file")

        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise PublishError("safe lock handling is unavailable")
        descriptor = os.open(lock_file, os.O_CREAT | os.O_RDWR | no_follow, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PublishError("lock path must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PublishError("another publication is already running") from exc
        except OSError as exc:
            raise PublishError("unable to acquire publication lock") from exc
        yield
    except PublishError:
        raise
    except OSError as exc:
        raise PublishError("unable to create publication lock") from exc
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                os.close(descriptor)


def stable_candidate_copy(input_path: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Read the requested candidate once, so validation and publication use identical bytes."""
    if not input_path.is_file():
        raise PublishError("candidate input file is missing")
    try:
        payload = input_path.read_bytes()
    except OSError as exc:
        raise PublishError("candidate input file cannot be read") from exc
    temporary_directory = tempfile.TemporaryDirectory(prefix="bay-area-offbeat-candidate-")
    candidate = Path(temporary_directory.name) / "candidate.json"
    atomic_write(candidate, payload)
    return temporary_directory, candidate


def verify_repo_preflight(
    repo: Path, expected_remote: str, branch: str, env: dict[str, str]
) -> None:
    """Reject unsafe publication prerequisites before creating a worktree."""
    if not repo.is_dir():
        raise PublishError("repository directory is missing")
    git_output(repo, ["rev-parse", "--is-inside-work-tree"], message="not a git repository", env=env)
    status = git_output(repo, ["status", "--porcelain"], message="unable to inspect repository", env=env)
    if status:
        raise PublishError("target repository is dirty")
    current_branch = git_output(
        repo,
        ["branch", "--show-current"],
        message="unable to determine repository branch",
        env=env,
    )
    if current_branch != branch:
        raise PublishError("target repository is not on the expected branch")
    expected_normalized_remote = normalize_remote(expected_remote)
    for remote_arguments in (
        ["remote", "get-url", "--all", "origin"],
        ["remote", "get-url", "--push", "--all", "origin"],
    ):
        configured_urls = git_output(
            repo,
            remote_arguments,
            message="target repository has no origin remote",
            env=env,
        ).splitlines()
        if not configured_urls or any(
            normalize_remote(url) != expected_normalized_remote for url in configured_urls
        ):
            raise PublishError("target repository remote does not match expected remote")
    for relative_path in REQUIRED_REPO_PATHS:
        if not (repo / relative_path).exists():
            raise PublishError("target repository is missing required publication files")

    command(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            f"refs/heads/{branch}:refs/remotes/origin/{branch}",
        ],
        cwd=repo,
        check_message="unable to fetch expected remote branch",
        env=env,
    )
    local_head = git_output(repo, ["rev-parse", "HEAD"], message="repository has no committed HEAD", env=env)
    remote_head = git_output(
        repo,
        ["rev-parse", f"origin/{branch}"],
        message="expected remote branch is unavailable",
        env=env,
    )
    if local_head == remote_head:
        return

    # A clean local checkout that is merely behind may safely seed an isolated
    # worktree from origin. Local-ahead or truly divergent histories are blocked.
    is_behind = subprocess.run(
        ["git", "merge-base", "--is-ancestor", local_head, remote_head],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    ).returncode == 0
    if not is_behind:
        raise PublishError("local and remote publication histories diverge")


def run_validator(repo: Path, candidate: Path, now: str | None, env: dict[str, str]) -> None:
    arguments = [sys.executable, "scripts/validate_events.py", str(candidate)]
    if now is not None:
        arguments.extend(["--now", now])
    command(arguments, cwd=repo, check_message="candidate validation failed", env=env)


def run_worktree_gates(worktree: Path, now: str | None, env: dict[str, str]) -> None:
    run_validator(worktree, worktree / "data" / "current.json", now, env)
    command(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=worktree,
        check_message="test gate failed",
        env=env,
    )
    command(
        [
            sys.executable,
            "scripts/build_site.py",
            "--input",
            "data/current.json",
            "--out",
            "dist",
        ],
        cwd=worktree,
        check_message="static build gate failed",
        env=env,
    )


def cleanup_worktree(repo: Path, worktree: Path, env: dict[str, str]) -> None:
    """Remove the temporary worktree regardless of a preceding gate result."""
    if worktree.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def staged_paths(worktree: Path, env: dict[str, str]) -> list[str]:
    output = git_output(
        worktree,
        ["diff", "--cached", "--name-only"],
        message="unable to inspect staged publication paths",
        env=env,
    )
    return [line for line in output.splitlines() if line]


def publication_date(snapshot_date: str | None) -> str:
    return snapshot_date or datetime.now(timezone.utc).date().isoformat()


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        repo = Path(args.repo).expanduser().resolve(strict=False)
        input_path = Path(args.input).expanduser().resolve(strict=False)
        lock_path = Path(args.lock_path)
        snapshot_relative = safe_snapshot_path(args.snapshot_date)
        if not args.branch or args.branch != "main":
            raise PublishError("publication branch must be main")
        if not args.remote:
            raise PublishError("expected remote is required")
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        repo = canonical_worktree_root(repo, env)

        candidate_directory, candidate = stable_candidate_copy(input_path)
        try:
            with publication_lock(lock_path, repo):
                verify_repo_preflight(repo, args.remote, args.branch, env)
                run_validator(repo, candidate, args.now, env)

                worktree = Path(tempfile.mkdtemp(prefix="bay-area-offbeat-publish-"))
                worktree_created = False
                try:
                    command(
                        ["git", "worktree", "add", "--detach", str(worktree), f"origin/{args.branch}"],
                        cwd=repo,
                        check_message="unable to create isolated publication worktree",
                        env=env,
                    )
                    worktree_created = True
                    atomic_write(worktree / "data" / "current.json", candidate.read_bytes())
                    if snapshot_relative is not None:
                        destination = (worktree / snapshot_relative).resolve(strict=False)
                        if not path_is_within(destination, worktree / "data" / "snapshots"):
                            raise PublishError("unsafe snapshot destination")
                        atomic_write(destination, candidate.read_bytes())

                    run_worktree_gates(worktree, args.now, env)
                    if args.dry_run:
                        emit(
                            {
                                "published": False,
                                "dry_run": True,
                                "message": "dry run passed",
                            }
                        )
                        return 0

                    allowed_paths = ["data/current.json"]
                    if snapshot_relative is not None:
                        allowed_paths.append(snapshot_relative)
                    command(
                        ["git", "add", "--", *allowed_paths],
                        cwd=worktree,
                        check_message="unable to stage generated event data",
                        env=env,
                    )
                    actual_paths = staged_paths(worktree, env)
                    if not actual_paths:
                        emit({"published": False, "message": "no changes to publish"})
                        return 0
                    if actual_paths != sorted(allowed_paths):
                        raise PublishError("publication staged unexpected paths")
                    command(
                        [
                            "git",
                            "commit",
                            "-m",
                            f"data: update Bay Area Offbeat events for {publication_date(args.snapshot_date)}",
                        ],
                        cwd=worktree,
                        check_message="unable to commit generated event data",
                        env=env,
                    )
                    commit = git_output(
                        worktree,
                        ["rev-parse", "HEAD"],
                        message="unable to determine publication commit",
                        env=env,
                    )
                    command(
                        ["git", "push", "origin", f"HEAD:refs/heads/{args.branch}"],
                        cwd=worktree,
                        check_message="unable to push publication commit",
                        env=env,
                    )
                    report: dict[str, object] = {
                        "published": True,
                        "commit": commit,
                        "message": "published",
                    }
                    if snapshot_relative is not None:
                        report["snapshot"] = snapshot_relative
                    emit(report)
                    return 0
                finally:
                    if worktree_created:
                        cleanup_worktree(repo, worktree, env)
                    else:
                        shutil.rmtree(worktree, ignore_errors=True)
        finally:
            candidate_directory.cleanup()
    except PublishError as exc:
        emit({"published": False, "message": str(exc)})
        return 1
    except (OSError, UnicodeError, ValueError):
        emit({"published": False, "message": "publisher failed safely"})
        return 1
    except Exception:
        # Do not serialize implementation details, paths, or subprocess output.
        emit({"published": False, "message": "publisher failed safely"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
