#!/usr/bin/env python3
"""
Merge partial manifest JSON files into a single manifest.json.

Usage:
    python scripts/merge_manifests.py parts/*.json --output manifest.json
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Merge partial FrankenPeanuts manifests")
    parser.add_argument("files", nargs="+", help="Partial manifest JSON files")
    parser.add_argument("--output", default="manifest.json", help="Output file path")
    args = parser.parse_args()

    merged: dict[str, list[dict]] = {}
    for f in args.files:
        path = Path(f)
        if not path.exists():
            continue
        with open(path) as fh:
            data = json.load(fh)
        merged.update(data)

    # Sort by date key for deterministic output
    sorted_manifest = dict(sorted(merged.items()))

    with open(args.output, "w") as fh:
        json.dump(sorted_manifest, fh, separators=(",", ":"))

    dates_with_panels = sum(1 for v in sorted_manifest.values() if v)
    total_panels = sum(len(v) for v in sorted_manifest.values())
    print(f"Merged {len(sorted_manifest)} dates from {len(args.files)} files.")
    print(f"{dates_with_panels} dates with usable panels, {total_panels} total panels.")


if __name__ == "__main__":
    main()
