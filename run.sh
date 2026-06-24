#!/usr/bin/env bash
# GD Skill Match — one-command launcher (macOS / Linux / Git Bash)
#   ./run.sh                 run on the default version
#   FETCH=1 ./run.sh         re-download fresh data from gsv.fun first
#   PORT=9000 VERSION=galaxywave_delta ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

PORT="${PORT:-8770}"
VERSION="${VERSION:-galaxywave_delta}"
FETCH="${FETCH:-0}"

PROC="data/processed/${VERSION}/matrix.npz"
RAW="data/raw/players_drum_${VERSION}.jsonl"

if [ "$FETCH" = "1" ] || [ ! -f "$RAW" ]; then
  echo "[run] fetching data from gsv.fun (version=$VERSION) ..."
  python pipeline/fetch_data.py --version "$VERSION"
fi
if [ "$FETCH" = "1" ] || [ ! -f "$PROC" ]; then
  echo "[run] building dataset ..."
  python pipeline/build_dataset.py --version "$VERSION"
fi

echo "[run] starting server on http://127.0.0.1:${PORT}"
python server/app.py --port "$PORT" --version "$VERSION"
