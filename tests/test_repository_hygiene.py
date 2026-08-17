from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneTests(unittest.TestCase):
    def test_ephemeral_front_end_previews_are_ignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".front-end-preview-example/index.html"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            "ephemeral front-end preview artifacts must not be eligible for publication",
        )


if __name__ == "__main__":
    unittest.main()
