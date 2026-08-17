from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_daily.py"
NOW = "2026-08-13T23:00:00+00:00"

# The integration fixture deliberately has tiny stand-in validation/build tools so
# these tests exercise publisher Git/worktree behavior, not the real project's
# evolving event rules. The publisher itself must still run them as subprocesses.
VALIDATOR_SOURCE = r'''#!/usr/bin/env python3
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("input")
parser.add_argument("--now")
args = parser.parse_args()
try:
    with open(args.input, encoding="utf-8") as handle:
        document = json.load(handle)
    valid = (
        document.get("schema_version") == 1
        and document.get("timezone") == "America/Los_Angeles"
        and isinstance(document.get("generated_at"), str)
        and isinstance(document.get("events"), list)
    )
except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
    valid = False
print(json.dumps({"valid": valid, "validated_at": args.now}, separators=(",", ":")))
sys.exit(0 if valid else 1)
'''

BUILD_SOURCE = r'''#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
with open(args.input, encoding="utf-8") as handle:
    document = json.load(handle)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "index.html").write_text("built\n", encoding="utf-8")
(out / "data.json").write_text(json.dumps(document), encoding="utf-8")
print(json.dumps({"built": True}, separators=(",", ":")))
'''

FAILING_BUILD_SOURCE = "#!/usr/bin/env python3\nraise SystemExit(9)\n"

SEED_TEST_SOURCE = r'''import json
import unittest
from pathlib import Path


class SeedRepositoryTests(unittest.TestCase):
    def test_site_and_canonical_data_exist(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "site" / "index.html").is_file())
        with (root / "data" / "current.json").open(encoding="utf-8") as handle:
            self.assertIsInstance(json.load(handle)["events"], list)


if __name__ == "__main__":
    unittest.main()
'''


def git(
    *args: str, cwd: Path | None = None, git_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if git_dir is not None:
        command.extend(["--git-dir", str(git_dir)])
    command.extend(args)
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def canonical_document(revision: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": "2026-08-13T16:00:00-07:00",
        "timezone": "America/Los_Angeles",
        "events": [],
        "revision": revision,
    }


class PublisherRepositoryFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "remote.git"
        self.repo = root / "target"
        self.input = root / "candidate.json"
        self.lock = root / "publisher.lock"
        self.remote_url = self.remote.as_uri()
        self._create()

    def _run_git(self, *args: str, cwd: Path | None = None) -> str:
        result = git(*args, cwd=cwd or self.repo)
        if result.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed: {result.stdout!r} {result.stderr!r}"
            )
        return result.stdout.strip()

    def _create(self) -> None:
        result = git("init", "--bare", "--initial-branch=main", str(self.remote))
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        result = git("init", "--initial-branch=main", str(self.repo))
        if result.returncode != 0:
            raise AssertionError(result.stderr)

        (self.repo / "scripts").mkdir()
        (self.repo / "scripts" / "validate_events.py").write_text(
            VALIDATOR_SOURCE, encoding="utf-8"
        )
        (self.repo / "scripts" / "build_site.py").write_text(
            BUILD_SOURCE, encoding="utf-8"
        )
        shutil.copyfile(SCRIPT, self.repo / "scripts" / "publish_daily.py")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_seed.py").write_text(
            SEED_TEST_SOURCE, encoding="utf-8"
        )
        (self.repo / "site").mkdir()
        (self.repo / "site" / "index.html").write_text(
            "<!doctype html><title>Bay Area Offbeat</title>\n", encoding="utf-8"
        )
        (self.repo / "site" / "styles.css").write_text("body {}\n", encoding="utf-8")
        (self.repo / "site" / "app.js").write_text(
            'fetch("data/current.json");\n', encoding="utf-8"
        )
        (self.repo / "data" / "snapshots").mkdir(parents=True)
        (self.repo / "data" / "current.json").write_text(
            json.dumps(canonical_document("old"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (self.repo / ".gitignore").write_text(
            "dist/\n__pycache__/\n*.py[cod]\n", encoding="utf-8"
        )

        self._run_git("config", "user.name", "Publisher Test")
        self._run_git("config", "user.email", "publisher@example.invalid")
        self._run_git("add", "--all")
        self._run_git("commit", "-m", "seed")
        self._run_git("remote", "add", "origin", self.remote_url)
        self._run_git("push", "-u", "origin", "main")

    def write_input(self, document: dict[str, object] | str) -> None:
        if isinstance(document, str):
            self.input.write_text(document, encoding="utf-8")
            return
        self.input.write_text(
            json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
        )

    def run(
        self,
        *extra_args: str,
        remote: str | None = None,
        branch: str = "main",
        repo: Path | None = None,
        lock: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--input",
                str(self.input),
                "--repo",
                str(repo or self.repo),
                "--remote",
                remote or self.remote_url,
                "--branch",
                branch,
                "--lock-path",
                str(lock or self.lock),
                "--now",
                NOW,
                *extra_args,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "GIT_TERMINAL_PROMPT": "0"},
        )

    def remote_head(self) -> str:
        result = git("rev-parse", "refs/heads/main", git_dir=self.remote)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def status(self) -> str:
        return self._run_git("status", "--short")

    def working_tree_snapshot(self) -> dict[str, bytes]:
        snapshot: dict[str, bytes] = {}
        for path in sorted(self.repo.rglob("*")):
            if ".git" in path.relative_to(self.repo).parts or not path.is_file():
                continue
            snapshot[path.relative_to(self.repo).as_posix()] = path.read_bytes()
        return snapshot

    def remote_changed_paths(self, older: str, newer: str) -> list[str]:
        result = git("diff", "--name-only", older, newer, git_dir=self.remote)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return [line for line in result.stdout.splitlines() if line]

    def make_dirty(self) -> Path:
        marker = self.repo / "unrelated-local-note.txt"
        marker.write_text("do not publish this\n", encoding="utf-8")
        return marker

    def create_local_unpushed_commit(self) -> None:
        (self.repo / "local-only.txt").write_text("local only\n", encoding="utf-8")
        self._run_git("add", "local-only.txt")
        self._run_git("commit", "-m", "local-only")

    def replace_builder_and_push(self, source: str) -> None:
        (self.repo / "scripts" / "build_site.py").write_text(source, encoding="utf-8")
        self._run_git("add", "scripts/build_site.py")
        self._run_git("commit", "-m", "break builder")
        self._run_git("push", "origin", "main")

    def worktree_paths(self) -> list[Path]:
        result = git("worktree", "list", "--porcelain", cwd=self.repo)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return [
            Path(line.removeprefix("worktree "))
            for line in result.stdout.splitlines()
            if line.startswith("worktree ")
        ]


class PublishDailyCliTests(unittest.TestCase):
    def test_requires_explicit_input(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"published": False, "message": "invalid arguments"},
        )


class PublishDailyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="offbeat-publisher-test-"
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.fixture = PublisherRepositoryFixture(Path(self.temporary_directory.name))

    def assert_lock_is_safe_or_absent(self) -> None:
        if os.path.lexists(self.fixture.lock):
            self.assertFalse(self.fixture.lock.is_symlink())
            self.assertTrue(self.fixture.lock.is_file())

    def assert_failed_without_remote_change(
        self, result: subprocess.CompletedProcess[str], remote_before: str
    ) -> dict[str, object]:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["published"])
        self.assertEqual(self.fixture.remote_head(), remote_before)
        self.assert_lock_is_safe_or_absent()
        return report

    def test_dry_run_checks_candidate_without_changing_repo_or_remote(self) -> None:
        self.fixture.write_input(canonical_document("dry-run"))
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()

        result = self.fixture.run("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "published": False,
                "dry_run": True,
                "message": "dry run passed",
            },
        )
        self.assertEqual(self.fixture.remote_head(), remote_before)
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)
        self.assert_lock_is_safe_or_absent()
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])

    def test_invalid_input_leaves_remote_and_worktree_unchanged(self) -> None:
        self.fixture.write_input("{not valid json\n")
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()

        self.assert_failed_without_remote_change(self.fixture.run(), remote_before)

        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])

    def test_symlink_lock_path_is_rejected_without_deleting_its_target(self) -> None:
        self.fixture.write_input(canonical_document("symlink-lock"))
        remote_before = self.fixture.remote_head()
        victim = self.fixture.root / "must-survive.txt"
        victim.write_text("do not delete\n", encoding="utf-8")
        self.fixture.lock.symlink_to(victim)

        result = self.fixture.run("--dry-run")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["published"])
        self.assertIn("lock", str(report["message"]).casefold())
        self.assertEqual(self.fixture.remote_head(), remote_before)
        self.assertTrue(victim.is_file())
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not delete\n")
        self.assertTrue(self.fixture.lock.is_symlink())
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])

    def test_nested_repo_path_is_rejected_before_in_checkout_lock_creation(self) -> None:
        self.fixture.write_input(canonical_document("nested-repo"))
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()
        wrapper = self.fixture.repo / "ignored-wrapper"
        wrapper.mkdir()
        for name in ("scripts", "site", "data", "tests"):
            (wrapper / name).symlink_to(
                self.fixture.repo / name, target_is_directory=True
            )
        info_exclude = self.fixture.repo / ".git" / "info" / "exclude"
        with info_exclude.open("a", encoding="utf-8") as handle:
            handle.write("\nignored-wrapper/\nroot.lock\n")
        in_checkout_lock = self.fixture.repo / "root.lock"
        self.assertEqual(self.fixture.status(), "")

        report = self.assert_failed_without_remote_change(
            self.fixture.run(
                "--dry-run", repo=wrapper, lock=in_checkout_lock
            ),
            remote_before,
        )

        self.assertIn("repository", str(report["message"]).casefold())
        self.assertFalse(os.path.lexists(in_checkout_lock))
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])

    def test_unexpected_remote_is_rejected_before_mutation(self) -> None:
        self.fixture.write_input(canonical_document("wrong-remote"))
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()

        report = self.assert_failed_without_remote_change(
            self.fixture.run(remote="file:///not-the-configured-remote"), remote_before
        )

        self.assertIn("remote", str(report["message"]).casefold())
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)

    def test_unexpected_origin_pushurl_is_rejected_before_mutation(self) -> None:
        self.fixture.write_input(canonical_document("wrong-pushurl"))
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()
        unapproved_remote = self.fixture.root / "unapproved-push.git"
        result = git("init", "--bare", "--initial-branch=main", str(unapproved_remote))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.fixture._run_git(
            "remote", "set-url", "--push", "origin", unapproved_remote.as_uri()
        )

        report = self.assert_failed_without_remote_change(
            self.fixture.run("--dry-run"), remote_before
        )

        self.assertIn("remote", str(report["message"]).casefold())
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])

    def test_dirty_target_repository_is_rejected_before_mutation(self) -> None:
        self.fixture.write_input(canonical_document("dirty"))
        remote_before = self.fixture.remote_head()
        marker = self.fixture.make_dirty()
        tree_before = self.fixture.working_tree_snapshot()

        report = self.assert_failed_without_remote_change(self.fixture.run(), remote_before)

        self.assertIn("dirty", str(report["message"]).casefold())
        self.assertTrue(marker.is_file())
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)

    def test_local_remote_divergence_is_rejected_before_mutation(self) -> None:
        self.fixture.write_input(canonical_document("diverged"))
        remote_before = self.fixture.remote_head()
        self.fixture.create_local_unpushed_commit()
        tree_before = self.fixture.working_tree_snapshot()

        report = self.assert_failed_without_remote_change(self.fixture.run(), remote_before)

        self.assertIn("diverg", str(report["message"]).casefold())
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)

    def test_snapshot_date_traversal_is_rejected_without_creating_files(self) -> None:
        self.fixture.write_input(canonical_document("bad-snapshot"))
        remote_before = self.fixture.remote_head()

        report = self.assert_failed_without_remote_change(
            self.fixture.run("--snapshot-date", "../escape"), remote_before
        )

        self.assertIn("snapshot", str(report["message"]).casefold())
        self.assertFalse((self.fixture.root / "escape.json").exists())
        self.assertEqual(self.fixture.status(), "")

    def test_changed_payload_commits_only_allowlisted_data_paths(self) -> None:
        self.fixture.write_input(canonical_document("new"))
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()

        result = self.fixture.run("--snapshot-date", "2026-08-13")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["published"])
        self.assertIsInstance(report["commit"], str)
        self.assertEqual(len(report["commit"]), 40)
        self.assertEqual(report["snapshot"], "data/snapshots/2026-08-13.json")
        remote_after = self.fixture.remote_head()
        self.assertNotEqual(remote_after, remote_before)
        self.assertEqual(
            self.fixture.remote_changed_paths(remote_before, remote_after),
            ["data/current.json", "data/snapshots/2026-08-13.json"],
        )
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])

    def test_identical_canonical_payload_is_a_safe_no_op(self) -> None:
        self.fixture.write_input(canonical_document("old"))
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()

        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"published": False, "message": "no changes to publish"},
        )
        self.assertEqual(self.fixture.remote_head(), remote_before)
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])

    def test_isolated_worktree_is_removed_after_build_failure(self) -> None:
        self.fixture.replace_builder_and_push(FAILING_BUILD_SOURCE)
        self.fixture.write_input(canonical_document("will-not-publish"))
        remote_before = self.fixture.remote_head()
        tree_before = self.fixture.working_tree_snapshot()

        report = self.assert_failed_without_remote_change(self.fixture.run(), remote_before)

        self.assertIn("build", str(report["message"]).casefold())
        self.assertEqual(self.fixture.status(), "")
        self.assertEqual(self.fixture.working_tree_snapshot(), tree_before)
        self.assertEqual(self.fixture.worktree_paths(), [self.fixture.repo])


if __name__ == "__main__":
    unittest.main()
