#!/usr/bin/env python3
"""Generate a Homebrew formula for tagged RazeCLI release assets."""

from __future__ import annotations

import argparse
from pathlib import Path


def _build_formula(
    *,
    repository: str,
    tag: str,
    version: str,
    asset_name_arm64: str,
    asset_sha256_arm64: str,
    asset_name_x86_64: str | None,
    asset_sha256_x86_64: str | None,
) -> str:
    homepage = f"https://github.com/{repository}"
    arm_url = f"https://github.com/{repository}/releases/download/{tag}/{asset_name_arm64}"

    if asset_name_x86_64 and asset_sha256_x86_64:
        x86_url = f"https://github.com/{repository}/releases/download/{tag}/{asset_name_x86_64}"
        intel_block = f'''    elsif Hardware::CPU.intel?
      url "{x86_url}"
      sha256 "{asset_sha256_x86_64}"
'''
    else:
        intel_block = '''    elsif Hardware::CPU.intel?
      odie "No prebuilt x86_64 release yet. Use source install or arm64 build."
'''

    return f'''class Razecli < Formula
  desc "CLI/TUI for practical Razer mouse settings on macOS"
  homepage "{homepage}"
  version "{version}"

  on_macos do
    if Hardware::CPU.arm?
      url "{arm_url}"
      sha256 "{asset_sha256_arm64}"
{intel_block}    else
      odie "Unsupported macOS CPU architecture."
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Homebrew formula for release")
    parser.add_argument("--repository", required=True, help="owner/repo (for example compilererrors/RazeCLI)")
    parser.add_argument("--tag", required=True, help="Release tag, for example v0.1.1")
    parser.add_argument("--asset-name-arm64", required=True, help="arm64 release asset filename (tar.gz)")
    parser.add_argument("--asset-sha256-arm64", required=True, help="SHA256 for arm64 tar.gz asset")
    parser.add_argument("--asset-name-x86-64", dest="asset_name_x86_64", help="x86_64 release asset filename")
    parser.add_argument("--asset-sha256-x86-64", dest="asset_sha256_x86_64", help="SHA256 for x86_64 tar.gz asset")
    parser.add_argument("--out", required=True, help="Output path for formula file")
    args = parser.parse_args()

    has_x86_name = bool(args.asset_name_x86_64)
    has_x86_sha = bool(args.asset_sha256_x86_64)
    if has_x86_name != has_x86_sha:
        parser.error("--asset-name-x86-64 and --asset-sha256-x86-64 must be provided together")
    return args


def main() -> int:
    args = _parse_args()
    tag = args.tag.strip()
    version = tag[1:] if tag.startswith("v") else tag
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    content = _build_formula(
        repository=args.repository.strip(),
        tag=tag,
        version=version,
        asset_name_arm64=args.asset_name_arm64.strip(),
        asset_sha256_arm64=args.asset_sha256_arm64.strip().lower(),
        asset_name_x86_64=(args.asset_name_x86_64.strip() if args.asset_name_x86_64 else None),
        asset_sha256_x86_64=(args.asset_sha256_x86_64.strip().lower() if args.asset_sha256_x86_64 else None),
    )
    out_path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
