from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_shadow_context.py"


class PrepareShadowContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="offbeat-shadow-context-test-"
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.staging_root = self.root / "offbeat-staging"
        self.collector = self.root / "collector.py"

    def write_collector(self, source: str) -> None:
        self.collector.write_text(source, encoding="utf-8")

    def prepare(self, *extra_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--collector",
                str(self.collector),
                "--staging-root",
                str(self.staging_root),
                "--run-id",
                "2026-08-18T140000Z-test",
                *extra_args,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_collector_output_is_preserved_privately_and_augmented_with_safe_run_paths(self) -> None:
        self.write_collector(
            "import json\n"
            "print(json.dumps({'generated_utc':'2026-08-18T14:00:00Z','items':[], 'diagnostics':[]}))\n"
        )

        result = self.prepare()

        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)
        run = context["shadow_run"]
        run_dir = self.staging_root / "runs" / "2026-08-18T140000Z-test"
        self.assertEqual(run["run_dir"], str(run_dir))
        self.assertEqual(run["draft_path"], str(run_dir / "draft.json"))
        self.assertEqual(run["candidate_path"], str(run_dir / "candidate.json"))
        self.assertEqual(run["publisher_dry_run_path"], str(run_dir / "publisher-dry-run.json"))
        collector_copy = run_dir / "collector.json"
        self.assertEqual(
            json.loads(collector_copy.read_text(encoding="utf-8")),
            {"generated_utc": "2026-08-18T14:00:00Z", "items": [], "diagnostics": []},
        )
        self.assertEqual(stat.S_IMODE(collector_copy.stat().st_mode), 0o600)
        self.assertFalse((run_dir / "draft.json").exists())

    def test_invalid_collector_json_is_rejected_without_a_private_collector_copy(self) -> None:
        self.write_collector("print('not-json')\n")

        result = self.prepare()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "error", "message": "collector output is invalid"},
        )
        run_dir = self.staging_root / "runs" / "2026-08-18T140000Z-test"
        self.assertFalse((run_dir / "collector.json").exists())

    def test_collector_failure_is_rejected_without_exposing_its_output(self) -> None:
        self.write_collector("import sys\nprint('private collector failure')\nraise SystemExit(7)\n")

        result = self.prepare()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("private collector failure", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "error", "message": "collector failed"},
        )

    def test_symlinked_staging_root_is_rejected_before_creating_a_run(self) -> None:
        self.write_collector("import json\nprint(json.dumps({'items': [], 'diagnostics': []}))\n")
        real_root = self.root / "real-staging"
        real_root.mkdir()
        linked_root = self.root / "linked-staging"
        linked_root.symlink_to(real_root, target_is_directory=True)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--collector",
                str(self.collector),
                "--staging-root",
                str(linked_root),
                "--run-id",
                "symlink-root-test",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "error", "message": "private staging is unavailable"},
        )
        self.assertFalse((real_root / "runs" / "symlink-root-test").exists())

    def test_symlinked_collector_is_rejected_without_executing_its_target(self) -> None:
        self.write_collector("import json\nprint(json.dumps({'items': [], 'diagnostics': []}))\n")
        linked_collector = self.root / "linked-collector.py"
        linked_collector.symlink_to(self.collector)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--collector",
                str(linked_collector),
                "--staging-root",
                str(self.staging_root),
                "--run-id",
                "symlink-collector-test",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "error", "message": "collector is unavailable"},
        )
        self.assertFalse(
            (self.staging_root / "runs" / "symlink-collector-test" / "collector.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
