#!/usr/bin/env python3
"""
Build a panel manifest for FrankenPeanuts.

Iterates weekday dates, fetches each Peanuts strip image, detects panel
boundaries via column-darkness scanning, filters for near-square panels,
saves cropped panel images, and writes a manifest JSON file.

Uses concurrent fetching and processing for maximum throughput.
Resumable: if the output manifest already exists, dates present in it are skipped.

Requirements:
    pip install requests Pillow numpy

Usage:
    python scripts/build_manifest.py [--output manifest.json] [--panels-dir panels]
    python scripts/build_manifest.py --start-date 1950-10-02 --end-date 1960-01-01
    python scripts/build_manifest.py --workers 20 --delay 0.1
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from threading import Lock

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

JPEG_QUALITY = 85

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "FrankenPeanuts-ManifestBuilder/1.0"
})
# Keep connections alive and allow pooling for concurrent requests
adapter = requests.adapters.HTTPAdapter(
    pool_connections=30, pool_maxsize=30, max_retries=2
)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)


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

def fetch_strip_bytes(date_idx: str) -> bytes | None:
    url = STRIP_URL + date_idx
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"  SKIP {date_idx}: {e}", file=sys.stderr)
        return None


# ── Panel detection (vectorized with numpy) ────────────────────────────────

def detect_panels(arr: np.ndarray) -> list[dict]:
    h, w, _ = arr.shape

    # Sample middle 80% of height, every 2nd row — use uint16 to avoid float alloc
    y_start = int(h * 0.10)
    y_end = int(h * 0.90)
    sample = arr[y_start:y_end:2, :, :]

    # Per-column darkness: sum channels, threshold, count
    col_brightness = sample.sum(axis=2)  # uint16, avoids float mean
    dark_counts = (col_brightness < DARK_PX_THRESH * 3).sum(axis=0)
    n_rows = sample.shape[0]

    # Find gutter columns using vectorized threshold
    is_gutter = dark_counts > (GUTTER_THRESH * n_rows)

    # Find gutter runs using diff on boolean array
    padded = np.concatenate([[False], is_gutter, [False]])
    diff = np.diff(padded.astype(np.int8))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0] - 1

    # Discard gutters near edges
    margin = w * EDGE_MARGIN_FRAC
    mids = (starts + ends) / 2.0
    mask = (mids > margin) & (mids < w - margin)
    interior_mids = np.round(mids[mask]).astype(int)

    # Split points
    splits = np.concatenate([[0], interior_mids, [w]])

    # Build panel list, skip tiny slivers
    min_w = w * MIN_PANEL_FRAC
    panels = []
    for i in range(len(splits) - 1):
        px = int(splits[i])
        pw = int(splits[i + 1]) - px
        if pw < min_w:
            continue
        ratio = pw / h
        if MIN_RATIO <= ratio <= MAX_RATIO:
            panels.append({"index": i, "x": px, "w": pw, "h": h})
    return panels


# ── Panel extraction ───────────────────────────────────────────────────────

def save_panel(img: Image.Image, panel: dict, panels_dir: Path, date_idx: str) -> str:
    """Crop a panel from the strip and save as JPEG. Returns the filename."""
    cropped = img.crop((panel["x"], 0, panel["x"] + panel["w"], panel["h"]))
    filename = f"{date_idx}-{panel['index']}.jpg"
    cropped.save(panels_dir / filename, "JPEG", quality=JPEG_QUALITY)
    return filename


# ── Process a single date (runs in thread pool) ───────────────────────────

def process_date(date_idx: str, panels_dir: Path, delay: float) -> tuple[str, list[dict]]:
    """Fetch, detect, crop, return (date_idx, entries). Thread-safe."""
    if delay > 0:
        time.sleep(delay)

    raw = fetch_strip_bytes(date_idx)
    if raw is None:
        return (date_idx, [])

    img = Image.open(BytesIO(raw)).convert("RGB")
    arr = np.array(img)
    panels = detect_panels(arr)

    entries = []
    for p in panels:
        filename = save_panel(img, p, panels_dir, date_idx)
        entries.append({"index": p["index"], "file": filename})
    return (date_idx, entries)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build FrankenPeanuts panel manifest")
    parser.add_argument("--output", default="manifest.json", help="Output manifest path")
    parser.add_argument("--panels-dir", default="panels", help="Directory for cropped panel images")
    parser.add_argument("--delay", type=float, default=0.1, help="Seconds between requests per worker")
    parser.add_argument("--workers", type=int, default=16, help="Number of concurrent fetch/process threads")
    parser.add_argument("--start-date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date) if args.start_date else START_DATE
    end = date.fromisoformat(args.end_date) if args.end_date else END_DATE

    out_path = Path(args.output)
    panels_dir = Path(args.panels_dir)
    panels_dir.mkdir(parents=True, exist_ok=True)

    # Load existing manifest for resumability
    manifest: dict[str, list[dict]] = {}
    if out_path.exists():
        with open(out_path) as f:
            manifest = json.load(f)
        print(f"Resuming: {len(manifest)} dates already processed")

    all_dates = list(weekday_dates(start, end))
    to_process = [date_to_idx(d) for d in all_dates if date_to_idx(d) not in manifest]
    total = len(to_process)

    if total == 0:
        print("Nothing to process.")
        return

    print(f"Processing {total} dates with {args.workers} workers...")

    completed = 0
    manifest_lock = Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_date, idx, panels_dir, args.delay): idx
            for idx in to_process
        }

        for future in as_completed(futures):
            date_idx, entries = future.result()
            with manifest_lock:
                manifest[date_idx] = entries
                completed += 1

                if completed % 200 == 0:
                    dates_with = sum(1 for v in manifest.values() if v)
                    print(f"  [{completed}/{total}] {dates_with} dates with panels")
                    with open(out_path, "w") as f:
                        json.dump(manifest, f, separators=(",", ":"))

    # Final save
    with open(out_path, "w") as f:
        json.dump(manifest, f, separators=(",", ":"))

    dates_with_panels = sum(1 for v in manifest.values() if v)
    total_panels = sum(len(v) for v in manifest.values())
    print(f"\nDone! {len(manifest)} dates processed, "
          f"{dates_with_panels} have usable panels, {total_panels} total panels.")


if __name__ == "__main__":
    main()
