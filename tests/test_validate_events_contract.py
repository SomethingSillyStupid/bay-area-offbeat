from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_events.py"
FIXTURE = ROOT / "tests" / "fixtures" / "valid_document.json"
NOW = "2026-08-13T23:00:00+00:00"


def valid_document() -> dict[str, object]:
    return deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))


def deterministic_event_id(title: str, starts_at: str, official_url: str) -> str:
    """Mirror the documented public deterministic-ID formula."""
    normalized_title = " ".join(
        unicodedata.normalize("NFKC", title).casefold().split()
    )
    material = "\n".join((normalized_title, starts_at, official_url))
    return "evt_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def run_document(
    document: object, *extra_args: str, now: str = NOW
) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
        json.dump(document, handle)
        handle.flush()
        return subprocess.run(
            [sys.executable, str(SCRIPT), handle.name, "--now", now, *extra_args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


def run_raw_document(
    payload: str, *extra_args: str, now: str = NOW
) -> subprocess.CompletedProcess[str]:
    """Exercise JSON-parser behavior that json.dump cannot represent."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        return subprocess.run(
            [sys.executable, str(SCRIPT), handle.name, "--now", now, *extra_args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


class StrictEventSchemaContractTests(unittest.TestCase):
    def test_cli_parse_failures_emit_only_compact_json_reports(self) -> None:
        cases = {
            "missing input": [],
            "unknown option": [str(FIXTURE), "--not-a-real-option"],
            "missing option value": [str(FIXTURE), "--now"],
        }
        for label, arguments in cases.items():
            with self.subTest(label=label):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *arguments],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertEqual(
                    report["errors"], ["invalid command line arguments"]
                )
                self.assertFalse(report["valid"])
                self.assertEqual(report["event_count"], 0)
                self.assertTrue(report["validated_at"].endswith("+00:00"))

    def test_duplicate_json_members_are_rejected_at_document_and_event_levels(self) -> None:
        fixture_text = FIXTURE.read_text(encoding="utf-8")
        cases = {
            "document": fixture_text.replace(
                '"schema_version": 1,',
                '"schema_version": 9,\n  "schema_version": 1,',
                1,
            ),
            "event": fixture_text.replace(
                '"title": "Midnight Typewriter Picnic",',
                '"title": "Untrusted earlier value",\n    '
                '"title": "Midnight Typewriter Picnic",',
                1,
            ),
        }
        for level, payload in cases.items():
            with self.subTest(level=level):
                result = run_raw_document(payload)

                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
                report = json.loads(result.stdout)
                self.assertFalse(report["valid"])
                self.assertTrue(
                    any("duplicate JSON member" in error for error in report["errors"])
                )

    def test_excessively_nested_json_fails_closed_without_traceback(self) -> None:
        payload = "[" * 10_000 + "0" + "]" * 10_000

        result = run_raw_document(payload)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertEqual(
            report["errors"], ["input JSON nesting exceeds safe parser limits"]
        )

    def test_timestamp_with_offset_seconds_is_rejected_before_frontend_diverges(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["starts_at"] = "2026-08-15T02:00:00+00:00:30"  # type: ignore[index]
        event["id"] = deterministic_event_id(  # type: ignore[index]
            event["title"], event["starts_at"], event["official_url"]
        )

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("starts_at" in error for error in report["errors"]))

    def test_missing_event_field_fails_closed_with_machine_readable_report(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        del event["title"]  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("missing fields" in error for error in report["errors"]))

    def test_unhashable_id_fails_closed_instead_of_crashing(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["id"] = ["not", "an", "id"]  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("events[0].id" in error for error in report["errors"]))

    def test_event_field_type_errors_fail_closed_without_traceback(self) -> None:
        cases = {
            "all_day": "false",
            "radar": "false",
            "tags": "film",
            "ends_at": "2026-08-15T02:30:00",
            "last_verified_at": "2026-08-13T22:00:00",
        }
        for field, bad_value in cases.items():
            with self.subTest(field=field):
                document = valid_document()
                event = document["events"][0]  # type: ignore[index]
                event[field] = bad_value  # type: ignore[index]

                result = run_document(document)

                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
                report = json.loads(result.stdout)
                self.assertFalse(report["valid"])
                self.assertTrue(any(field in error for error in report["errors"]))

    def test_ends_at_must_be_later_than_starts_at(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["ends_at"] = "2026-08-15T01:59:00+00:00"  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("ends_at" in error and "starts_at" in error for error in report["errors"]))

    def test_explicitly_allowlisted_legacy_http_host_is_accepted(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        legacy_url = "http://legacy.example/event"
        event["official_url"] = legacy_url  # type: ignore[index]
        event["id"] = deterministic_event_id(
            event["title"], event["starts_at"], legacy_url  # type: ignore[index]
        )

        result = run_document(document, "--allow-http-host", "legacy.example")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["valid"])

    def test_future_verification_timestamp_is_rejected(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["last_verified_at"] = "2026-08-13T23:01:00+00:00"  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "last_verified_at" in error and "validation time" in error
                for error in report["errors"]
            )
        )

    def test_pacific_radar_allows_last_sunday_across_fall_dst_change(self) -> None:
        document = valid_document()
        document["generated_at"] = "2026-10-31T16:00:00-07:00"
        event = document["events"][0]  # type: ignore[index]
        event["starts_at"] = "2026-11-08T23:59:00-08:00"  # type: ignore[index]
        event["last_verified_at"] = "2026-10-31T22:00:00+00:00"  # type: ignore[index]
        event["id"] = deterministic_event_id(
            event["title"], event["starts_at"], event["official_url"]  # type: ignore[index]
        )

        result = run_document(document, now="2026-10-31T23:00:00+00:00")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["valid"])

    def test_pacific_radar_requires_true_on_monday_after_fall_dst_change(self) -> None:
        document = valid_document()
        document["generated_at"] = "2026-10-31T16:00:00-07:00"
        event = document["events"][0]  # type: ignore[index]
        event["starts_at"] = "2026-11-09T00:00:00-08:00"  # type: ignore[index]
        event["last_verified_at"] = "2026-10-31T22:00:00+00:00"  # type: ignore[index]
        event["id"] = deterministic_event_id(
            event["title"], event["starts_at"], event["official_url"]  # type: ignore[index]
        )

        result = run_document(document, now="2026-10-31T23:00:00+00:00")

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("after next Sunday" in error for error in report["errors"]))

    def test_cli_does_not_mutate_its_input_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate.json"
            candidate.write_text(json.dumps(valid_document(), indent=2), encoding="utf-8")
            before = candidate.read_bytes()

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(candidate), "--now", NOW],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(candidate.read_bytes(), before)

    def test_url_with_invalid_port_is_rejected_as_not_a_valid_organizer_url(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["official_url"] = "https://example.org:70000/event"  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("official_url" in error for error in report["errors"]))

    def test_url_with_embedded_whitespace_is_rejected(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["official_url"] = "https://example.org /event"  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("official_url" in error for error in report["errors"]))

    def test_empty_event_array_is_rejected(self) -> None:
        document = valid_document()
        document["events"] = []

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("at least one" in error for error in report["errors"]))

    def test_public_why_is_limited_to_280_characters(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["why"] = "x" * 281  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any("events[0].why" in error and "280" in error for error in report["errors"])
        )

    def test_tag_count_is_limited_to_six(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["tags"] = [f"tag-{index}" for index in range(7)]  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any("events[0].tags" in error and "6" in error for error in report["errors"])
        )

    def test_verification_cannot_postdate_payload_generation(self) -> None:
        document = valid_document()
        document["generated_at"] = "2026-08-13T22:00:00+00:00"
        event = document["events"][0]  # type: ignore[index]
        event["last_verified_at"] = "2026-08-13T22:30:00+00:00"  # type: ignore[index]

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "last_verified_at" in error and "generated_at" in error
                for error in report["errors"]
            )
        )

    def test_extreme_now_fails_closed_with_a_utc_json_report(self) -> None:
        result = run_document(valid_document(), now="9999-12-31T23:59:59-14:00")

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("--now" in error for error in report["errors"]))
        self.assertTrue(report["validated_at"].endswith("+00:00"))

    def test_extreme_generated_at_fails_closed_without_traceback(self) -> None:
        document = valid_document()
        document["generated_at"] = "9999-12-31T23:59:59+00:00"

        result = run_document(document, now="9999-12-31T23:59:59+00:00")

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("Pacific week window" in error for error in report["errors"]))

    def test_minimum_event_timestamp_fails_closed_without_traceback(self) -> None:
        document = valid_document()
        event = document["events"][0]  # type: ignore[index]
        event["starts_at"] = "0001-01-01T00:00:00Z"  # type: ignore[index]
        event["id"] = deterministic_event_id(  # type: ignore[index]
            event["title"], event["starts_at"], event["official_url"]
        )

        result = run_document(document)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("starts_at" in error for error in report["errors"]))

    def test_stale_payload_or_event_verification_is_rejected(self) -> None:
        stale_payload = valid_document()
        stale_payload["generated_at"] = "2026-08-12T10:00:00+00:00"

        stale_payload_result = run_document(stale_payload)

        self.assertEqual(stale_payload_result.returncode, 1)
        stale_payload_report = json.loads(stale_payload_result.stdout)
        self.assertFalse(stale_payload_report["valid"])
        self.assertTrue(any("generated_at" in error and "fresh" in error for error in stale_payload_report["errors"]))

        stale_verification = valid_document()
        event = stale_verification["events"][0]  # type: ignore[index]
        event["last_verified_at"] = "2026-08-12T10:00:00+00:00"  # type: ignore[index]

        stale_verification_result = run_document(stale_verification)

        self.assertEqual(stale_verification_result.returncode, 1)
        stale_verification_report = json.loads(stale_verification_result.stdout)
        self.assertFalse(stale_verification_report["valid"])
        self.assertTrue(
            any("last_verified_at" in error and "fresh" in error for error in stale_verification_report["errors"])
        )


if __name__ == "__main__":
    unittest.main()
