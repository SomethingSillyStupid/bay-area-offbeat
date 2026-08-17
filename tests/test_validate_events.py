import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_events.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = "2026-08-13T23:00:00+00:00"


def run_validator(fixture_name, *extra_args):
    """Run the validator against one checked-in fixture."""
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(FIXTURES / fixture_name),
            "--now",
            NOW,
            *extra_args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def load_valid_document():
    """Return an independent copy of the valid fixture."""
    with (FIXTURES / "valid_document.json").open(encoding="utf-8") as handle:
        return deepcopy(json.load(handle))


def run_document(document, *extra_args, now=NOW):
    """Run the validator against a temporary JSON document."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
        json.dump(document, handle)
        handle.flush()
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                handle.name,
                "--now",
                now,
                *extra_args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )


def error_report(result):
    """Parse a validator report after checking that it is invalid."""
    report = json.loads(result.stdout)
    if result.returncode == 0 or report["valid"]:
        raise AssertionError(f"expected invalid report, got {report!r}")
    return report


def event_id(title, starts_at, official_url):
    """Mirror the documented deterministic-ID formula for test data."""
    normalized = " ".join(unicodedata.normalize("NFKC", title).casefold().split())
    material = "\n".join((normalized, starts_at, official_url))
    return "evt_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def load_validator_module():
    """Load the standalone validator script for direct API tests."""
    spec = importlib.util.spec_from_file_location("validate_events", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load validator module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ValidatorCliTests(unittest.TestCase):
    def test_valid_document_passes(self):
        result = run_validator("valid_document.json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "valid": True,
                "errors": [],
                "event_count": 1,
                "validated_at": NOW,
            },
        )

    def test_malformed_json_fails_closed_with_json_report(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            handle.write("{")
            handle.flush()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), handle.name, "--now", NOW],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertEqual(report["event_count"], 0)
        self.assertEqual(report["validated_at"], NOW)
        self.assertTrue(any("JSON" in error for error in report["errors"]))

    def test_missing_file_fails_closed_with_json_report(self):
        missing = FIXTURES / "does-not-exist.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(missing), "--now", NOW],
            capture_output=True,
            text=True,
            check=False,
        )

        report = error_report(result)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(report["event_count"], 0)
        self.assertTrue(any("read input" in error for error in report["errors"]))

    def test_invalid_now_fails_closed_with_utc_json_report(self):
        result = run_document(load_valid_document(), now="not-a-timestamp")

        report = error_report(result)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(report["event_count"], 1)
        validated_at = datetime.fromisoformat(report["validated_at"])
        self.assertIsNotNone(validated_at.tzinfo)
        self.assertTrue(report["validated_at"].endswith("+00:00"))
        self.assertTrue(any("--now" in error for error in report["errors"]))

    def test_now_is_optional_and_report_uses_current_utc(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            handle.write("{")
            handle.flush()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), handle.name],
                capture_output=True,
                text=True,
                check=False,
            )

        report = error_report(result)
        self.assertNotIn("Traceback", result.stderr)
        validated_at = datetime.fromisoformat(report["validated_at"])
        self.assertIsNotNone(validated_at.tzinfo)
        self.assertTrue(report["validated_at"].endswith("+00:00"))

    def test_non_object_document_fails_closed(self):
        result = run_document([])

        report = error_report(result)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(report["event_count"], 0)
        self.assertTrue(any("document must be an object" in error for error in report["errors"]))

    def test_missing_events_key_fails_closed(self):
        document = load_valid_document()
        del document["events"]

        report = error_report(run_document(document))
        self.assertEqual(report["event_count"], 0)
        self.assertTrue(any("missing top-level keys: events" in error for error in report["errors"]))

    def test_unexpected_top_level_key_is_rejected(self):
        document = load_valid_document()
        document["private_notes"] = "do not publish"

        report = error_report(run_document(document))
        self.assertTrue(any("unexpected top-level keys: private_notes" in error for error in report["errors"]))

    def test_top_level_metadata_values_are_strict(self):
        cases = {
            "schema_version": 2,
            "schema_version_bool": True,
            "timezone": "UTC",
            "timezone_type": 7,
        }
        for case, bad_value in cases.items():
            with self.subTest(case=case):
                document = load_valid_document()
                field = case.removesuffix("_bool").removesuffix("_type")
                document[field] = bad_value
                result = run_document(document)
                report = error_report(result)
                self.assertNotIn("Traceback", result.stderr)
                self.assertTrue(any(field in error for error in report["errors"]), report)

    def test_generated_at_must_be_aware_and_not_future(self):
        for value in (
            "not-a-timestamp",
            "2026-08-13T16:00:00",
            "2026-08-14T00:00:00+00:00",
        ):
            with self.subTest(value=value):
                document = load_valid_document()
                document["generated_at"] = value
                result = run_document(document)
                report = error_report(result)
                self.assertNotIn("Traceback", result.stderr)
                self.assertTrue(any("generated_at" in error for error in report["errors"]), report)

    def test_event_that_has_started_is_rejected(self):
        result = run_document(
            load_valid_document(), now="2026-08-15T02:00:00+00:00"
        )

        report = error_report(result)
        self.assertTrue(any("starts_at" in error for error in report["errors"]))

    def test_duplicate_event_id_is_rejected(self):
        document = load_valid_document()
        document["events"].append(deepcopy(document["events"][0]))

        report = error_report(run_document(document))
        self.assertTrue(any("duplicate id" in error for error in report["errors"]))

    def test_wrong_deterministic_id_is_rejected(self):
        document = load_valid_document()
        document["events"][0]["id"] = "evt_0000000000000000"

        report = error_report(run_document(document))
        self.assertTrue(any("deterministic id" in error for error in report["errors"]))

    def test_material_duplicate_is_rejected_despite_different_id(self):
        document = load_valid_document()
        duplicate = deepcopy(document["events"][0])
        duplicate["title"] = "  MIDNIGHT   TYPEWRITER PICNIC "
        duplicate["official_url"] = "https://example.org/events/alternate-listing"
        duplicate["id"] = event_id(
            duplicate["title"], duplicate["starts_at"], duplicate["official_url"]
        )
        document["events"].append(duplicate)

        report = error_report(run_document(document))
        self.assertTrue(any("material duplicate" in error for error in report["errors"]))

    def test_html_and_control_characters_are_rejected(self):
        validator = load_validator_module()
        cases = {
            "title": "<b>Midnight Typewriter Picnic</b>",
            "city": "Oakland\u0007",
            "neighborhood": "<em>Temescal</em>",
            "price_note": "Free\u0000",
            "source_name": "<script>source</script>",
            "why": "Line one\nLine two",
            "tags": ["<i>writing</i>"],
        }
        for field, bad_value in cases.items():
            with self.subTest(field=field):
                document = load_valid_document()
                document["events"][0][field] = bad_value
                if field == "title":
                    event = document["events"][0]
                    event["id"] = event_id(
                        event["title"], event["starts_at"], event["official_url"]
                    )
                errors = validator.validate_document(
                    document, datetime.fromisoformat(NOW), set()
                )
                self.assertTrue(any(field in error for error in errors), errors)

    def test_invalid_or_non_https_url_is_rejected(self):
        validator = load_validator_module()
        for url in (
            "not-an-absolute-url",
            "ftp://example.org/event",
            "http://legacy.example/event",
        ):
            with self.subTest(url=url):
                document = load_valid_document()
                event = document["events"][0]
                event["official_url"] = url
                event["id"] = event_id(event["title"], event["starts_at"], url)
                errors = validator.validate_document(
                    document, datetime.fromisoformat(NOW), set()
                )
                self.assertTrue(
                    any("official_url" in error for error in errors), errors
                )


if __name__ == "__main__":
    unittest.main()
