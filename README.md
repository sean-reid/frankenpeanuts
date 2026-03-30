# FrankenPeanuts

It's alive! A comic strip stitched together from the dismembered panels of random *Peanuts* strips (1950–2000).

Every reload is a new abomination. Charles Schulz is rolling in his grave and you're welcome.

## How it works

1. A GitHub Actions workflow fetches every daily strip from [peanuts-search.com](https://peanuts-search.com)
2. Panel gutters are detected by scanning columns for dark pixels
3. Near-square panels are cropped and saved as static JPEGs
4. A manifest (`manifest.json`) maps each date to its usable panels
5. The client picks 4 random panels, loads them directly — no proxies, no processing

## Building the manifest

The panel extraction pipeline runs as a manual GitHub Actions workflow:

```
gh workflow run "Build Panel Manifest"
```

This spins up 10 parallel runners, each handling a ~5-year chunk of the archive with 16 concurrent threads. The full 50-year archive processes in about 3–5 minutes.

To run locally:

```bash
pip install requests Pillow numpy
python scripts/build_manifest.py --workers 16 --delay 0.1
```

Options:
- `--start-date` / `--end-date` — process a specific date range
- `--workers` — number of concurrent fetch threads (default 16)
- `--delay` — seconds between requests per worker (default 0.1)
- `--output` — manifest output path (default `manifest.json`)
- `--panels-dir` — directory for cropped images (default `panels/`)

The script is resumable — re-run it and it skips dates already in the manifest.

## Sharing

The entire comic is encoded in the URL hash. Copy the link, send it to a friend, ruin their day.

## Project structure

```
index.html              — the single-page app
manifest.json           — pre-computed panel index (generated)
panels/                 — cropped panel JPEGs (generated)
scripts/
  build_manifest.py     — fetch strips, detect panels, extract images
  merge_manifests.py    — combine partial manifests from parallel runners
.github/workflows/
  build-manifest.yml    — parallel CI pipeline
```

## Disclaimer

*Peanuts* © Peanuts Worldwide LLC. This is a fan project. Please don't sue me, Snoopy.
