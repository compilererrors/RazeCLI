#!/usr/bin/env python3
"""Update Homebrew release URL in README to a concrete tag."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _update_readme_content(content: str, repository: str, tag: str) -> tuple[str, int]:
    pattern = re.compile(
        rf"https://github\.com/{re.escape(repository)}/releases/download/(?:<tag>|v\d+\.\d+\.\d+)/razecli\.rb"
    )
    replacement = f"https://github.com/{repository}/releases/download/{tag}/razecli.rb"
    updated, count = pattern.subn(replacement, content)
    return updated, count


def main() -> int:
    parser = argparse.ArgumentParser(description="Update README release-tag URL")
    parser.add_argument("--readme", default="README.md", help="README file path")
    parser.add_argument("--repository", required=True, help="owner/repo")
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v0.1.3")
    args = parser.parse_args()

    readme_path = Path(args.readme)
    original = readme_path.read_text(encoding="utf-8")
    updated, count = _update_readme_content(original, args.repository.strip(), args.tag.strip())

    if count > 0 and updated != original:
        readme_path.write_text(updated, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
