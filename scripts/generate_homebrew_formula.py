#!/usr/bin/env python3
"""Generate a Homebrew formula for a tagged RazeCLI release asset."""

from __future__ import annotations

import argparse
from pathlib import Path


def _build_formula(
    *,
    repository: str,
    tag: str,
    version: str,
    asset_name: str,
    asset_sha256: str,
) -> str:
    url = f"https://github.com/{repository}/releases/download/{tag}/{asset_name}"
    homepage = f"https://github.com/{repository}"
    return f'''class Razecli < Formula
  desc "CLI/TUI for practical Razer mouse settings on macOS"
  homepage "{homepage}"
  version "{version}"

  on_macos do
    if Hardware::CPU.arm?
      url "{url}"
      sha256 "{asset_sha256}"
    else
      odie "No prebuilt x86_64 release yet. Use source install or arm64 build."
    end
  end

  def install
    bin.install "razecli-onedir/razecli-onedir" => "razecli"
  end

  test do
    output = shell_output("#{{bin}}/razecli --help")
    assert_match "CLI for identifying and configuring Razer mice", output
  end
end
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Homebrew formula for release")
    parser.add_argument("--repository", required=True, help="owner/repo (for example compilererrors/RazeCLI)")
    parser.add_argument("--tag", required=True, help="Release tag, for example v0.1.1")
    parser.add_argument("--asset-name", required=True, help="Release asset filename (tar.gz)")
    parser.add_argument("--asset-sha256", required=True, help="SHA256 for the tar.gz asset")
    parser.add_argument("--out", required=True, help="Output path for formula file")
    args = parser.parse_args()

    tag = args.tag.strip()
    version = tag[1:] if tag.startswith("v") else tag
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    content = _build_formula(
        repository=args.repository.strip(),
        tag=tag,
        version=version,
        asset_name=args.asset_name.strip(),
        asset_sha256=args.asset_sha256.strip().lower(),
    )
    out_path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
