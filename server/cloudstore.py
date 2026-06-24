"""
cloudstore.py — keep the analysis artifacts in sync with a GCS bucket.

Used by both the Cloud Run service (download artifacts at startup) and the scheduled
updater Cloud Function (rebuild + upload). No-ops gracefully when run locally with no
GCS_BUCKET set, so local development never depends on the cloud.

Env:
  GCS_BUCKET   bucket name holding artifacts under  processed/<version>/<file>

Layout mirrors data/processed/<version>/ : charts.json players.json kasegi.json
meta.json matrix.npz
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


def _bucket():
    name = os.environ.get("GCS_BUCKET")
    if not name:
        return None
    try:
        from google.cloud import storage  # lazy: not needed locally
    except Exception:  # noqa: BLE001
        return None
    return storage.Client().bucket(name)


def _local_dir(version):
    return os.path.join(PROC_DIR, version)


def has_local(version):
    d = _local_dir(version)
    return all(os.path.isfile(os.path.join(d, f)) for f in ARTIFACTS)


def download_artifacts(version):
    """Download artifacts from GCS to the local processed dir. True if all present."""
    b = _bucket()
    if b is None:
        return False
    d = _local_dir(version)
    os.makedirs(d, exist_ok=True)
    ok = True
    for f in ARTIFACTS:
        blob = b.blob(f"processed/{version}/{f}")
        if not blob.exists():
            ok = False
            continue
        blob.download_to_filename(os.path.join(d, f))
    if ok:
        print(f"[cloudstore] downloaded artifacts for {version} from gs://{b.name}", flush=True)
    return ok


def upload_artifacts(version):
    """Upload the local processed artifacts to GCS."""
    b = _bucket()
    if b is None:
        print("[cloudstore] no GCS_BUCKET; skip upload", flush=True)
        return False
    d = _local_dir(version)
    for f in ARTIFACTS:
        path = os.path.join(d, f)
        if os.path.isfile(path):
            b.blob(f"processed/{version}/{f}").upload_from_filename(path)
    print(f"[cloudstore] uploaded artifacts for {version} to gs://{b.name}", flush=True)
    return True


def build_local(version):
    """Scrape gsv.fun + build artifacts by running the pipeline scripts."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, os.path.join(PIPELINE, "fetch_data.py"),
                    "--version", version], check=True, cwd=ROOT, env=env)
    subprocess.run([sys.executable, os.path.join(PIPELINE, "build_dataset.py"),
                    "--version", version], check=True, cwd=ROOT, env=env)


def ensure_artifacts(version):
    """Guarantee local artifacts exist before the engine loads.
    Order: already-local → download from GCS → scrape+build (and upload)."""
    if has_local(version):
        return
    if download_artifacts(version) and has_local(version):
        return
    print(f"[cloudstore] bootstrapping artifacts for {version} (scrape + build) ...", flush=True)
    build_local(version)
    upload_artifacts(version)


def rebuild_and_upload(version):
    """Daily refresh: scrape + build + upload (used by the scheduled function)."""
    build_local(version)
    upload_artifacts(version)
    return True
