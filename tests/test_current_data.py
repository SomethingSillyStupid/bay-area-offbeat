from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "data" / "current.json"
VALIDATOR = ROOT / "scripts" / "validate_events.py"


def parse_timestamp(value: str):
    return __import__("datetime").datetime.fromisoformat(value.replace("Z", "+00:00"))


class CurrentDataContractTests(unittest.TestCase):
    def test_checked_in_public_payload_validates_at_its_generation_boundary(self) -> None:
        """The checked-in public sample must pass the same hard gate used for publication."""
        self.assertTrue(CURRENT.is_file(), f"missing canonical public payload: {CURRENT}")
        document = json.loads(CURRENT.read_text(encoding="utf-8"))
        generated_at = parse_timestamp(document["generated_at"])
        validation_time = (generated_at + timedelta(seconds=1)).isoformat()

        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                str(CURRENT),
                "--now",
                validation_time,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report["valid"], report)
        self.assertGreaterEqual(report["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
