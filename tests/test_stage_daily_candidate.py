from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stage_daily_candidate.py"
FIXTURE = ROOT / "tests" / "fixtures" / "valid_document.json"
GENERATED_AT = "2026-08-13T23:00:00Z"
NOW = "2026-08-13T23:00:01Z"

sys.path.insert(0, str(ROOT / "scripts"))
stage = importlib.import_module("stage_daily_candidate")


def valid_draft() -> dict[str, list[dict[str, object]]]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    event = dict(document["events"][0])
    event.pop("id")
    return {"events": [event]}


class StageDailyCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="offbeat-stage-candidate-test-"
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.staging_root = self.root / "offbeat-staging"
        self.run_dir = self.staging_root / "runs" / "2026-08-13T230000Z-test"
        self.run_dir.mkdir(parents=True)
        self.draft_path = self.run_dir / "draft.json"

    def write_draft(self, value: object) -> None:
        self.draft_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def stage(self, *extra_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--draft",
                str(self.draft_path),
                "--run-dir",
                str(self.run_dir),
                "--staging-root",
                str(self.staging_root),
                "--generated-at",
                GENERATED_AT,
                "--now",
                NOW,
                *extra_args,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_valid_draft_creates_canonical_candidate_and_deterministic_email_preview(self) -> None:
        self.write_draft(valid_draft())

        result = self.stage()

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ready_for_shadow_publish_dry_run")
        self.assertFalse(report["public_publish"])
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["event_ids"], ["evt_b73715bd49caa477"])

        candidate_path = self.run_dir / "candidate.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        self.assertEqual(candidate["schema_version"], 1)
        self.assertEqual(candidate["generated_at"], GENERATED_AT)
        self.assertEqual(candidate["timezone"], "America/Los_Angeles")
        self.assertEqual(candidate["events"][0]["id"], "evt_b73715bd49caa477")
        self.assertNotIn("private_notes", candidate["events"][0])

        validation = json.loads((self.run_dir / "validation.json").read_text(encoding="utf-8"))
        self.assertTrue(validation["valid"], validation)
        preview = json.loads((self.run_dir / "email.json").read_text(encoding="utf-8"))
        self.assertIn("Midnight Typewriter Picnic", preview["body"])
        self.assertEqual(preview["counts"], {"this_week": 1, "next_week": 0, "radar": 0})

    def test_publisher_dry_run_uses_the_exact_private_candidate_and_expected_repository(self) -> None:
        candidate_path = self.run_dir / "candidate.json"
        candidate_path.write_text(json.dumps(valid_draft()), encoding="utf-8")
        expected_report = {
            "published": False,
            "dry_run": True,
            "message": "dry run passed",
        }
        captured: list[list[str]] = []

        def fake_run(arguments: list[str]) -> tuple[int, dict[str, object]]:
            captured.append(arguments)
            return 0, expected_report

        with patch.object(stage, "run_json_command", side_effect=fake_run):
            report = stage.run_publisher_dry_run(candidate_path, NOW)

        self.assertEqual(report, expected_report)
        self.assertEqual(
            captured,
            [
                [
                    sys.executable,
                    str(ROOT / "scripts" / "publish_daily.py"),
                    "--input",
                    str(candidate_path),
                    "--repo",
                    str(ROOT),
                    "--dry-run",
                    "--now",
                    NOW,
                ]
            ],
        )

    def test_publisher_dry_run_rejects_a_nonpassing_or_malformed_report(self) -> None:
        candidate_path = self.run_dir / "candidate.json"
        candidate_path.write_text(json.dumps(valid_draft()), encoding="utf-8")

        with patch.object(
            stage,
            "run_json_command",
            return_value=(1, {"published": False, "message": "publisher failed"}),
        ):
            with self.assertRaisesRegex(stage.StageError, "publisher dry run failed"):
                stage.run_publisher_dry_run(candidate_path, NOW)

    def test_explicit_publisher_dry_run_writes_a_private_receipt_only_after_success(self) -> None:
        self.write_draft(valid_draft())
        expected_report = {
            "published": False,
            "dry_run": True,
            "message": "dry run passed",
        }
        arguments = [
            "--draft",
            str(self.draft_path),
            "--run-dir",
            str(self.run_dir),
            "--staging-root",
            str(self.staging_root),
            "--generated-at",
            GENERATED_AT,
            "--now",
            NOW,
            "--publisher-dry-run",
        ]

        with patch.object(stage, "run_publisher_dry_run", return_value=expected_report):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = stage.main(arguments)

        self.assertEqual(exit_code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "shadow_publish_dry_run_passed")
        self.assertTrue(report["publisher_dry_run"])
        self.assertEqual(
            json.loads((self.run_dir / "publisher-dry-run.json").read_text(encoding="utf-8")),
            expected_report,
        )

    def test_unexpected_private_draft_field_is_rejected_without_creating_candidate(self) -> None:
        draft = valid_draft()
        draft["events"][0]["private_notes"] = "raw newsletter body must never enter canonical data"
        self.write_draft(draft)

        result = self.stage()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "rejected")
        self.assertIn("unexpected", report["message"])
        self.assertFalse((self.run_dir / "candidate.json").exists())
        self.assertFalse((self.run_dir / "email.json").exists())
        self.assertTrue(self.draft_path.is_file())

    def test_invalid_canonical_event_writes_validation_report_but_not_email_preview(self) -> None:
        draft = valid_draft()
        draft["events"][0]["official_url"] = "http://insecure.example.test/event"
        self.write_draft(draft)

        result = self.stage()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["message"], "candidate validation failed")
        validation = json.loads((self.run_dir / "validation.json").read_text(encoding="utf-8"))
        self.assertFalse(validation["valid"])
        self.assertFalse((self.run_dir / "email.json").exists())
        self.assertTrue((self.run_dir / "candidate.json").is_file())

    def test_run_directory_outside_private_staging_root_is_rejected_before_reading_draft(self) -> None:
        outside_run = self.root / "outside"
        outside_run.mkdir()
        outside_draft = outside_run / "draft.json"
        outside_draft.write_text(json.dumps(valid_draft()), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--draft",
                str(outside_draft),
                "--run-dir",
                str(outside_run),
                "--staging-root",
                str(self.staging_root),
                "--generated-at",
                GENERATED_AT,
                "--now",
                NOW,
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
            {"status": "rejected", "message": "run directory must be inside private staging"},
        )
        self.assertFalse((outside_run / "candidate.json").exists())

    def test_symlinked_staging_root_is_rejected_before_reading_a_private_draft(self) -> None:
        real_root = self.root / "real-staging"
        real_run = real_root / "runs" / "symlink-root-test"
        real_run.mkdir(parents=True)
        linked_root = self.root / "linked-staging"
        linked_root.symlink_to(real_root, target_is_directory=True)
        linked_run = linked_root / "runs" / "symlink-root-test"
        linked_draft = linked_run / "draft.json"
        linked_draft.write_text(json.dumps(valid_draft()), encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--draft",
                str(linked_draft),
                "--run-dir",
                str(linked_run),
                "--staging-root",
                str(linked_root),
                "--generated-at",
                GENERATED_AT,
                "--now",
                NOW,
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
            {"status": "rejected", "message": "private staging root must not be a symlink"},
        )
        self.assertFalse((real_run / "candidate.json").exists())


if __name__ == "__main__":
    unittest.main()
