#!/usr/bin/env bash
# GD Skill Match — one-command launcher (macOS / Linux / Git Bash)
#   ./run.sh                          run on the default version + instrument (drum)
#   FETCH=1 ./run.sh                  re-download fresh data from gsv.fun first
#   INSTRUMENT=guitar ./run.sh        build/serve GuitarFreaks (gsv type:g)
#   PORT=9000 VERSION=galaxywave_delta ./run.sh
# The server serves every instrument it finds built under data/processed/<ver>/;
# build both (run once per INSTRUMENT) to get the in-page DM<->GF switcher.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

PORT="${PORT:-8770}"
VERSION="${VERSION:-galaxywave_delta}"
INSTRUMENT="${INSTRUMENT:-drum}"
FETCH="${FETCH:-0}"

PROC="data/processed/${VERSION}/${INSTRUMENT}/matrix.npz"
RAW="data/raw/players_${INSTRUMENT}_${VERSION}.jsonl"

if [ "$FETCH" = "1" ] || [ ! -f "$RAW" ]; then
  echo "[run] fetching $INSTRUMENT data from gsv.fun (version=$VERSION) ..."
  python pipeline/fetch_data.py --version "$VERSION" --instrument "$INSTRUMENT"
fi
if [ "$FETCH" = "1" ] || [ ! -f "$PROC" ]; then
  echo "[run] building $INSTRUMENT dataset ..."
  python pipeline/build_dataset.py --version "$VERSION" --instrument "$INSTRUMENT"
fi

echo "[run] starting server on http://127.0.0.1:${PORT}"
python server/app.py --port "$PORT" --version "$VERSION"
