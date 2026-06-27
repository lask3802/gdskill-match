"""
cloudstore.py — keep the analysis artifacts in sync with a GCS bucket.

Used by both the Cloud Run service (download artifacts at startup) and the scheduled
updater Cloud Function (rebuild + upload). No-ops gracefully when run locally with no
GCS_BUCKET set, so local development never depends on the cloud.

Env:
  GCS_BUCKET    bucket name holding artifacts under  processed/<version>/<instrument>/<file>
  GD_INSTRUMENTS  comma-separated instruments to serve/build (default "drum")

Layout mirrors data/processed/<version>/<instrument>/ : charts.json players.json
kasegi.json meta.json matrix.npz
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_BASE = os.environ.get("GD_DATA_DIR") or os.path.join(ROOT, "data")
PROC_DIR = os.path.join(DATA_BASE, "processed")
PIPELINE = os.path.join(ROOT, "pipeline")

ARTIFACTS = ["charts.json", "players.json", "kasegi.json", "meta.json", "matrix.npz"]


def configured_instruments():
    """Instruments to build/serve, from GD_INSTRUMENTS (default just drum)."""
    raw = os.environ.get("GD_INSTRUMENTS", "drum")
    insts = [s.strip() for s in raw.split(",") if s.strip()]
    return insts or ["drum"]


def _bucket():
    name = os.environ.get("GCS_BUCKET")
    if not name:
        return None
    try:
        from google.cloud import storage  # lazy: not needed locally
    except Exception:  # noqa: BLE001
        return None
    return storage.Client().bucket(name)


def _local_dir(version, instrument="drum"):
    return os.path.join(PROC_DIR, version, instrument)


def _blob_name(version, instrument, fname):
    return f"processed/{version}/{instrument}/{fname}"


def has_local(version, instrument="drum"):
    d = _local_dir(version, instrument)
    return all(os.path.isfile(os.path.join(d, f)) for f in ARTIFACTS)


def download_artifacts(version, instrument="drum"):
    """Download one instrument's artifacts from GCS. True if all present."""
    b = _bucket()
    if b is None:
        return False
    d = _local_dir(version, instrument)
    os.makedirs(d, exist_ok=True)
    ok = True
    for f in ARTIFACTS:
        blob = b.blob(_blob_name(version, instrument, f))
        if not blob.exists():
            ok = False
            continue
        blob.download_to_filename(os.path.join(d, f))
    if ok:
        print(f"[cloudstore] downloaded {instrument}/{version} from gs://{b.name}", flush=True)
    return ok


def upload_artifacts(version, instrument="drum"):
    """Upload one instrument's local artifacts to GCS."""
    b = _bucket()
    if b is None:
        print("[cloudstore] no GCS_BUCKET; skip upload", flush=True)
        return False
    d = _local_dir(version, instrument)
    for f in ARTIFACTS:
        path = os.path.join(d, f)
        if os.path.isfile(path):
            b.blob(_blob_name(version, instrument, f)).upload_from_filename(path)
    print(f"[cloudstore] uploaded {instrument}/{version} to gs://{b.name}", flush=True)
    return True


def build_local(version, instrument="drum"):
    """Scrape gsv.fun + build artifacts by running the pipeline scripts."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, os.path.join(PIPELINE, "fetch_data.py"),
                    "--version", version, "--instrument", instrument],
                   check=True, cwd=ROOT, env=env)
    subprocess.run([sys.executable, os.path.join(PIPELINE, "build_dataset.py"),
                    "--version", version, "--instrument", instrument],
                   check=True, cwd=ROOT, env=env)


def ensure_artifacts(version, instruments=None):
    """Guarantee local artifacts exist before the engine loads, for every
    configured instrument. Order per instrument: already-local → download from
    GCS → scrape+build (and upload). A failure on one instrument never blocks the
    others (so an unavailable guitar source can't take down drum)."""
    for instrument in (instruments or configured_instruments()):
        try:
            if has_local(version, instrument):
                continue
            if download_artifacts(version, instrument) and has_local(version, instrument):
                continue
            print(f"[cloudstore] bootstrapping {instrument}/{version} (scrape + build) ...",
                  flush=True)
            build_local(version, instrument)
            upload_artifacts(version, instrument)
        except Exception as ex:  # noqa: BLE001 — one instrument must not block others
            print(f"[cloudstore] {instrument}/{version} unavailable: {ex}", flush=True)


def rebuild_and_upload(version, instruments=None):
    """Daily refresh: scrape + build + upload every configured instrument."""
    for instrument in (instruments or configured_instruments()):
        build_local(version, instrument)
        upload_artifacts(version, instrument)
    return True
