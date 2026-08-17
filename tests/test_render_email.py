from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RENDERER = REPO / "scripts" / "render_email.py"


class RenderEmailCliTests(unittest.TestCase):
    def test_renderer_groups_current_next_and_radar_events_chronologically(self) -> None:
        document = {
            "schema_version": 1,
            "generated_at": "2026-08-13T23:00:00+00:00",
            "timezone": "America/Los_Angeles",
            "events": [
                {
                    "id": "evt_radar00000001",
                    "title": "Farther-out experiment",
                    "starts_at": "2026-09-03T19:00:00-07:00",
                    "ends_at": None,
                    "all_day": False,
                    "city": "San Francisco",
                    "neighborhood": "SoMa",
                    "price_note": "$12",
                    "official_url": "https://example.test/radar",
                    "source_name": "Official organizer",
                    "why": "A small future-facing reason.",
                    "tags": ["media art"],
                    "radar": True,
                    "last_verified_at": "2026-08-13T22:00:00+00:00",
                },
                {
                    "id": "evt_next0000000001",
                    "title": "Next-week listening room",
                    "starts_at": "2026-08-17T20:00:00-07:00",
                    "ends_at": None,
                    "all_day": False,
                    "city": "Oakland",
                    "neighborhood": "Temescal",
                    "price_note": "Free",
                    "official_url": "https://example.test/next",
                    "source_name": "Official organizer",
                    "why": "A very good reason to leave the house.",
                    "tags": ["experimental music"],
                    "radar": False,
                    "last_verified_at": "2026-08-13T22:00:00+00:00",
                },
                {
                    "id": "evt_currentlate0001",
                    "title": "Later current-week event",
                    "starts_at": "2026-08-15T20:00:00-07:00",
                    "ends_at": None,
                    "all_day": False,
                    "city": "Berkeley",
                    "neighborhood": None,
                    "price_note": None,
                    "official_url": "https://example.test/later",
                    "source_name": "Official organizer",
                    "why": "A later reason.",
                    "tags": [],
                    "radar": False,
                    "last_verified_at": "2026-08-13T22:00:00+00:00",
                },
                {
                    "id": "evt_currentfirst001",
                    "title": "First current-week event",
                    "starts_at": "2026-08-14T18:30:00-07:00",
                    "ends_at": None,
                    "all_day": False,
                    "city": "Oakland",
                    "neighborhood": "Downtown",
                    "price_note": "$8–15",
                    "official_url": "https://example.test/first",
                    "source_name": "Official organizer",
                    "why": "The first reason.",
                    "tags": ["film"],
                    "radar": False,
                    "last_verified_at": "2026-08-13T22:00:00+00:00",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "events.json"
            source.write_text(json.dumps(document), encoding="utf-8")
            result = subprocess.run(
                ["python3", str(RENDERER), "--input", str(source), "--json"],
                cwd=REPO,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["subject"], "Bay Area offbeat best-of — Thu Aug 13")
        body = rendered["body"]
        self.assertIn("THIS WEEK", body)
        self.assertIn("NEXT WEEK", body)
        self.assertIn("ON THE RADAR", body)
        self.assertLess(body.index("First current-week event"), body.index("Later current-week event"))
        self.assertLess(body.index("Later current-week event"), body.index("Next-week listening room"))
        self.assertLess(body.index("Next-week listening room"), body.index("Farther-out experiment"))
        self.assertIn("https://example.test/first", body)
        self.assertEqual(rendered["counts"], {"this_week": 2, "next_week": 1, "radar": 1})
    def test_renderer_breaks_same_time_ties_by_ascii_event_id(self) -> None:
        document = {
            "generated_at": "2026-08-13T23:00:00+00:00",
            "events": [
                {
                    "id": "evt_aa7acb7d74e33fc3",
                    "title": "Zulu tie event",
                    "starts_at": "2026-08-15T19:00:00-07:00",
                    "all_day": False,
                    "city": "Oakland",
                    "neighborhood": None,
                    "price_note": None,
                    "why": "A deliberately middle ASCII event-ID tie case.",
                    "official_url": "https://example.test/tie/zulu-1",
                    "radar": False,
                },
                {
                    "id": "evt_e659d3148a2544f2",
                    "title": "Álbum tie event",
                    "starts_at": "2026-08-15T19:00:00-07:00",
                    "all_day": False,
                    "city": "Oakland",
                    "neighborhood": None,
                    "price_note": None,
                    "why": "A deliberately later ASCII event-ID tie case.",
                    "official_url": "https://example.test/tie/album-5",
                    "radar": False,
                },
                {
                    "id": "evt_34bfd3a408186804",
                    "title": "alpha tie event",
                    "starts_at": "2026-08-15T19:00:00-07:00",
                    "all_day": False,
                    "city": "Oakland",
                    "neighborhood": None,
                    "price_note": None,
                    "why": "A deliberately earlier ASCII event-ID tie case.",
                    "official_url": "https://example.test/tie/alpha-4",
                    "radar": False,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "events.json"
            source.write_text(json.dumps(document), encoding="utf-8")
            result = subprocess.run(
                ["python3", str(RENDERER), "--input", str(source), "--json"],
                cwd=REPO,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)["body"]
        self.assertLess(body.index("alpha tie event"), body.index("Zulu tie event"))
        self.assertLess(body.index("Zulu tie event"), body.index("Álbum tie event"))


if __name__ == "__main__":
    unittest.main()
