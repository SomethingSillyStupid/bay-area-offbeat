#!/usr/bin/env python3
"""Build the public Bay Area Offbeat static site."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_FILES = ("index.html", "styles.css", "app.js", "robots.txt", "sitemap.xml")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/current.json")
    parser.add_argument("--out", default="dist")
    return parser.parse_args(argv)


def repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def safe_output_path(value: str) -> Path | None:
    """Allow replacement only of the dedicated repository-root build directory."""
    expected = (REPO_ROOT / "dist").absolute()
    candidate = repo_path(value).absolute()
    if candidate != expected:
        return None
    try:
        output_stat = os.lstat(expected)
    except FileNotFoundError:
        return expected
    if os.path.islink(expected) or not os.path.isdir(expected):
        return None
    if output_stat.st_dev != os.stat(REPO_ROOT).st_dev:
        return None
    return expected


def path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = repo_path(args.input)

    if not input_path.is_file():
        print(f"build_site: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(f"build_site: invalid JSON input: {input_path}", file=sys.stderr)
        return 1

    for filename in SITE_FILES:
        source = REPO_ROOT / "site" / filename
        if not source.is_file():
            print(f"build_site: source site file missing: {source}", file=sys.stderr)
            return 1

    try:
        index_source = (REPO_ROOT / "site" / "index.html").read_text(encoding="utf-8")
        app_source = (REPO_ROOT / "site" / "app.js").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("build_site: source site files must be readable UTF-8", file=sys.stderr)
        return 1
    expected_references = {
        "Bay Area Offbeat": index_source,
        "styles.css": index_source,
        "app.js": index_source,
        "data/current.json": app_source,
    }
    for reference, source_text in expected_references.items():
        if reference not in source_text:
            print(
                f"build_site: source site reference missing: {reference}",
                file=sys.stderr,
            )
            return 1

    output_path = safe_output_path(args.out)
    if output_path is None:
        print(f"build_site: unsafe output path: {args.out}", file=sys.stderr)
        return 1
    if path_is_within(input_path, output_path):
        print("build_site: input file must be outside output directory", file=sys.stderr)
        return 1

    if output_path.exists():
        if output_path.is_dir() and not output_path.is_symlink():
            shutil.rmtree(output_path)
        else:
            output_path.unlink()
    output_path.mkdir(parents=True)
    for filename in SITE_FILES:
        shutil.copyfile(REPO_ROOT / "site" / filename, output_path / filename)
    data_dir = output_path / "data"
    data_dir.mkdir()
    shutil.copyfile(input_path, data_dir / "current.json")

    print(
        json.dumps(
            {"output": str(output_path), "input": input_path.name},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
