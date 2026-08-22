from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_site.py"


class BuildSiteTests(unittest.TestCase):
    def write_minimal_site(self, fixture_root: Path) -> None:
        site = fixture_root / "site"
        site.mkdir()
        contents = {
            "index.html": (
                '<!doctype html><title>Bay Area Offbeat</title>'
                '<link rel="stylesheet" href="styles.css">'
                '<script src="app.js"></script>'
            ),
            "styles.css": "body {}\n",
            "app.js": 'fetch("data/current.json");\n',
            "robots.txt": "User-agent: *\nAllow: /\n",
            "sitemap.xml": "<?xml version=\"1.0\"?><urlset></urlset>\n",
        }
        for filename, content in contents.items():
            (site / filename).write_text(content, encoding="utf-8")

    def test_missing_input_fails_cleanly_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-build-", dir=ROOT) as tmp:
            work = Path(tmp)
            missing = work / "missing.json"
            output = work / "dist"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(missing),
                    "--out",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("input file not found", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output.exists())

    def test_invalid_json_fails_cleanly_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-build-", dir=ROOT) as tmp:
            work = Path(tmp)
            invalid = work / "invalid.json"
            invalid.write_text("{not json}\n", encoding="utf-8")
            output = work / "dist"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(invalid),
                    "--out",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid JSON input", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output.exists())

    def test_missing_site_source_fails_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-script-fixture-") as tmp:
            fixture_root = Path(tmp)
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir()
            fixture_script.write_bytes(SCRIPT.read_bytes())
            input_path = fixture_root / "events.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            output = fixture_root / "dist"

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    str(output),
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source site file missing", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output.exists())

    def test_unsafe_output_paths_are_rejected_without_deleting_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-safety-") as tmp:
            work = Path(tmp)
            fixture_root = work / "repo"
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir(parents=True)
            fixture_script.write_bytes(SCRIPT.read_bytes())
            self.write_minimal_site(fixture_root)
            input_path = fixture_root / "events.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            outside = work / "outside"
            outside.mkdir()
            outside_marker = outside / "keep.txt"
            outside_marker.write_text("keep", encoding="utf-8")

            for output in (fixture_root, outside):
                with self.subTest(output=output):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(fixture_script),
                            "--input",
                            str(input_path),
                            "--out",
                            str(output),
                        ],
                        cwd=fixture_root,
                        text=True,
                        capture_output=True,
                        check=False,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe output path", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

            self.assertEqual(outside_marker.read_text(encoding="utf-8"), "keep")
            self.assertTrue(fixture_script.is_file())

    def test_build_rejects_data_directory_without_replacing_canonical_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-output-safety-") as tmp:
            fixture_root = Path(tmp)
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir()
            fixture_script.write_bytes(SCRIPT.read_bytes())
            self.write_minimal_site(fixture_root)
            input_path = fixture_root / "candidate.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            canonical = fixture_root / "data" / "current.json"
            canonical.parent.mkdir()
            canonical.write_text("must-not-be-replaced\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    str(canonical.parent),
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe output path", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(canonical.read_text(encoding="utf-8"), "must-not-be-replaced\n")

    def test_build_rejects_nested_data_output_without_creating_public_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-output-safety-") as tmp:
            fixture_root = Path(tmp)
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir()
            fixture_script.write_bytes(SCRIPT.read_bytes())
            self.write_minimal_site(fixture_root)
            input_path = fixture_root / "candidate.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            output = fixture_root / "data" / "preview"

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    str(output),
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe output path", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output.exists())

    def test_build_rejects_symlinked_dist_without_deleting_its_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-output-safety-") as tmp:
            work = Path(tmp)
            fixture_root = work / "repo"
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir(parents=True)
            fixture_script.write_bytes(SCRIPT.read_bytes())
            self.write_minimal_site(fixture_root)
            input_path = fixture_root / "candidate.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            target = work / "outside-target"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("do-not-delete\n", encoding="utf-8")
            (fixture_root / "dist").symlink_to(target, target_is_directory=True)

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    "dist",
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe output path", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "do-not-delete\n")

    def test_build_rejects_arbitrary_repository_file_output_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-output-safety-") as tmp:
            fixture_root = Path(tmp)
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir()
            fixture_script.write_bytes(SCRIPT.read_bytes())
            self.write_minimal_site(fixture_root)
            input_path = fixture_root / "candidate.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            readme = fixture_root / "README.md"
            readme.write_text("do-not-delete\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    str(readme),
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe output path", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(readme.read_text(encoding="utf-8"), "do-not-delete\n")

    def test_build_rejects_git_metadata_output_without_creating_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-output-safety-") as tmp:
            fixture_root = Path(tmp)
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir()
            fixture_script.write_bytes(SCRIPT.read_bytes())
            self.write_minimal_site(fixture_root)
            input_path = fixture_root / "candidate.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            output = fixture_root / ".git" / "preview"

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    str(output),
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe output path", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output.exists())

    def test_build_rejects_input_inside_dist_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-output-safety-") as tmp:
            fixture_root = Path(tmp)
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir()
            fixture_script.write_bytes(SCRIPT.read_bytes())
            self.write_minimal_site(fixture_root)
            output = fixture_root / "dist"
            candidate = output / "data" / "current.json"
            candidate.parent.mkdir(parents=True)
            candidate.write_text('{"events": []}\n', encoding="utf-8")
            marker = output / "keep.txt"
            marker.write_text("do-not-delete\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(candidate),
                    "--out",
                    "dist",
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside output", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(candidate.read_text(encoding="utf-8"), '{"events": []}\n')
            self.assertEqual(marker.read_text(encoding="utf-8"), "do-not-delete\n")

    def test_build_copies_only_public_assets_and_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-build-") as tmp:
            work = Path(tmp)
            fixture_root = work / "repo"
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir(parents=True)
            fixture_script.write_bytes(SCRIPT.read_bytes())
            site = fixture_root / "site"
            site.mkdir()
            site_contents = {
                "index.html": (
                    '<!doctype html><title>Bay Area Offbeat</title>'
                    '<link rel="stylesheet" href="styles.css">'
                    '<script src="app.js"></script>'
                ),
                "styles.css": "body { color: white; }\n",
                "app.js": 'fetch("data/current.json");\n',
                "robots.txt": "User-agent: *\nAllow: /\n",
                "sitemap.xml": "<?xml version=\"1.0\"?><urlset></urlset>\n",
            }
            for filename, content in site_contents.items():
                (site / filename).write_text(content, encoding="utf-8")
            input_path = fixture_root / "custom-events.json"
            payload = {
                "schema_version": 1,
                "generated_at": "2026-08-13T19:00:00Z",
                "timezone": "America/Los_Angeles",
                "events": [],
            }
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            output = fixture_root / "dist"
            stale = output / "stale" / "private.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text("remove me", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    str(output),
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            files = sorted(
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file()
            )
            self.assertEqual(
                files,
                [
                    "app.js",
                    "data/current.json",
                    "index.html",
                    "robots.txt",
                    "sitemap.xml",
                    "styles.css",
                ],
            )
            for filename, content in site_contents.items():
                self.assertEqual(
                    (output / filename).read_text(encoding="utf-8"),
                    content,
                )
            self.assertEqual(
                json.loads((output / "data" / "current.json").read_text(encoding="utf-8")),
                payload,
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["output"], str(output.resolve()))
            self.assertEqual(report["input"], input_path.name)
            self.assertEqual(result.stderr, "")

    def test_build_rejects_site_without_expected_public_references(self) -> None:
        with tempfile.TemporaryDirectory(prefix="offbeat-source-") as tmp:
            fixture_root = Path(tmp)
            fixture_script = fixture_root / "scripts" / "build_site.py"
            fixture_script.parent.mkdir(parents=True)
            fixture_script.write_bytes(SCRIPT.read_bytes())
            site = fixture_root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                '<!doctype html><title>Bay Area Offbeat</title>'
                '<link rel="stylesheet" href="styles.css">',
                encoding="utf-8",
            )
            (site / "styles.css").write_text("body {}\n", encoding="utf-8")
            (site / "app.js").write_text(
                'fetch("data/current.json");\n', encoding="utf-8"
            )
            (site / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
            (site / "sitemap.xml").write_text(
                "<?xml version=\"1.0\"?><urlset></urlset>\n", encoding="utf-8"
            )
            input_path = fixture_root / "events.json"
            input_path.write_text('{"events": []}\n', encoding="utf-8")
            output = fixture_root / "dist"

            result = subprocess.run(
                [
                    sys.executable,
                    str(fixture_script),
                    "--input",
                    str(input_path),
                    "--out",
                    str(output),
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source site reference missing", result.stderr)
            self.assertIn("app.js", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
