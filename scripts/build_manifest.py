#!/usr/bin/env python3
"""
Build a panel manifest for FrankenPeanuts.

Iterates weekday dates from 1950-10-02 to 2000-02-13, fetches each Peanuts
strip image, detects panel boundaries via column-darkness scanning, filters
for near-square panels, and writes manifest.json.

Resumable: if manifest.json already exists, dates present in it are skipped.

Requirements:
    pip install requests Pillow

Usage:
    python scripts/build_manifest.py [--output manifest.json] [--delay 0.5]
    python scripts/build_manifest.py --start-date 1950-10-02 --end-date 1960-01-01
"""

import argparse
import json
import sys
import time
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image
import numpy as np

# ── Config ──────────────────────────────────────────────────────────────────

STRIP_URL = "https://peanuts-search.com/I/"
START_DATE = date(1950, 10, 2)
END_DATE = date(2000, 2, 13)

MIN_RATIO = 0.55
MAX_RATIO = 1.45

DARK_PX_THRESH = 60        # brightness 0-255
GUTTER_THRESH = 0.70       # fraction of dark pixels to count as gutter
EDGE_MARGIN_FRAC = 0.03    # ignore gutters within this fraction of edges
MIN_PANEL_FRAC = 0.05      # discard panels narrower than this fraction of width

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "FrankenPeanuts-ManifestBuilder/1.0"
})


# ── Date helpers ────────────────────────────────────────────────────────────

def date_to_idx(d: date) -> str:
    return f"{d.year}{d.month:02d}{d.day:02d}"


def weekday_dates(start: date = START_DATE, end: date = END_DATE):
    """Yield all non-Sunday dates in the given range."""
    d = start
    while d <= end:
        if d.weekday() != 6:  # 6 = Sunday
            yield d
        d += timedelta(days=1)


# ── Image fetching ──────────────────────────────────────────────────────────

def fetch_strip(date_idx: str) -> Image.Image | None:
    url = STRIP_URL + date_idx
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        print(f"  SKIP {date_idx}: {e}", file=sys.stderr)
        return None


# ── Panel detection (port of the JS algorithm) ─────────────────────────────

def detect_panels(img: Image.Image) -> list[dict]:
    arr = np.array(img)
    h, w, _ = arr.shape

    # Sample middle 80% of height
    y_start = int(h * 0.10)
    y_end = int(h * 0.90)
    sample = arr[y_start:y_end:2, :, :]  # every 2nd row

    # Per-column darkness: fraction of sampled pixels with avg brightness < threshold
    brightness = sample.mean(axis=2)  # (sample_rows, w)
    dark_counts = (brightness < DARK_PX_THRESH).sum(axis=0)  # (w,)
    darkness = dark_counts / brightness.shape[0]

    # Identify gutter regions (runs of columns darker than threshold)
    is_gutter = darkness > GUTTER_THRESH
    gutters = []
    in_g = False
    g_start = 0
    for x in range(w):
        if is_gutter[x]:
            if not in_g:
                g_start = x
                in_g = True
        elif in_g:
            gutters.append((g_start, x - 1))
            in_g = False
    if in_g:
        gutters.append((g_start, w - 1))

    # Discard gutters near edges
    margin = w * EDGE_MARGIN_FRAC
    interior = [(s, e) for s, e in gutters if margin < (s + e) / 2 < w - margin]

    # Split points
    splits = [0] + [round((s + e) / 2) for s, e in interior] + [w]

    # Build panel list
    panels = []
    for i in range(len(splits) - 1):
        px = splits[i]
        pw = splits[i + 1] - px
        if pw < w * MIN_PANEL_FRAC:
            continue
        ratio = pw / h
        panels.append({
            "index": i,
            "x": px,
            "w": pw,
            "h": h,
            "ratio": round(ratio, 4),
        })
    return panels


def is_near_square(panel: dict) -> bool:
    return MIN_RATIO <= panel["ratio"] <= MAX_RATIO


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build FrankenPeanuts panel manifest")
    parser.add_argument("--output", default="manifest.json", help="Output file path")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between requests")
    parser.add_argument("--start-date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date) if args.start_date else START_DATE
    end = date.fromisoformat(args.end_date) if args.end_date else END_DATE

    out_path = Path(args.output)

    # Load existing manifest for resumability
    manifest: dict[str, list[dict]] = {}
    if out_path.exists():
        with open(out_path) as f:
            manifest = json.load(f)
        print(f"Resuming: {len(manifest)} dates already processed")

    all_dates = list(weekday_dates(start, end))
    total = len(all_dates)
    processed = 0
    new_count = 0

    for d in all_dates:
        idx = date_to_idx(d)
        processed += 1

        if idx in manifest:
            continue

        img = fetch_strip(idx)
        if img is None:
            manifest[idx] = []  # mark as attempted
        else:
            panels = detect_panels(img)
            good = [p for p in panels if is_near_square(p)]
            # Store only the fields the client needs (no pixel data)
            manifest[idx] = [
                {"index": p["index"], "x": p["x"], "w": p["w"], "h": p["h"]}
                for p in good
            ]

        new_count += 1

        # Progress + periodic save every 100 new strips
        if new_count % 100 == 0:
            print(f"  [{processed}/{total}] {idx} — {new_count} new, "
                  f"{sum(1 for v in manifest.values() if v)} dates with panels")
            with open(out_path, "w") as f:
                json.dump(manifest, f, separators=(",", ":"))

        time.sleep(args.delay)

    # Final save
    with open(out_path, "w") as f:
        json.dump(manifest, f, separators=(",", ":"))

    dates_with_panels = sum(1 for v in manifest.values() if v)
    total_panels = sum(len(v) for v in manifest.values())
    print(f"\nDone! {len(manifest)} dates processed, "
          f"{dates_with_panels} have usable panels, {total_panels} total panels.")


if __name__ == "__main__":
    main()
