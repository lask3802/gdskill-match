"""
updater/main.py — scheduled Cloud Function (2nd gen, Python).

Runs daily (via Cloud Scheduler): scrape gsv.fun, rebuild the analysis artifacts,
and upload them to the GCS bucket the Cloud Run service reads from — once per
configured instrument (DrumMania, optionally GuitarFreaks).

Self-contained (own copies of fetch_data.py / build_dataset.py) so it deploys via
buildpacks with no Dockerfile. Writes to /tmp (the only writable path in the
function runtime) via GD_DATA_DIR.

Env:
  GCS_BUCKET      target bucket (required)
  GD_VERSION      game version (default galaxywave_delta)
  GD_INSTRUMENTS  comma-separated instruments to rebuild (default "drum")
"""

import os
import subprocess
import sys

import functions_framework

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = os.environ.get("GD_VERSION", "galaxywave_delta")
DATA_DIR = os.environ.get("GD_DATA_DIR", "/tmp/gddata")
ARTIFACTS = ["charts.json", "players.json", "kasegi.json", "meta.json", "matrix.npz"]


def _instruments():
    raw = os.environ.get("GD_INSTRUMENTS", "drum")
    insts = [s.strip() for s in raw.split(",") if s.strip()]
    return insts or ["drum"]


def _run():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", GD_DATA_DIR=DATA_DIR)
    bucket_name = os.environ["GCS_BUCKET"]
    from google.cloud import storage
    bucket = storage.Client().bucket(bucket_name)

    done = []
    for instrument in _instruments():
        subprocess.run([sys.executable, os.path.join(HERE, "fetch_data.py"),
                        "--version", VERSION, "--instrument", instrument],
                       check=True, cwd=HERE, env=env)
        subprocess.run([sys.executable, os.path.join(HERE, "build_dataset.py"),
                        "--version", VERSION, "--instrument", instrument],
                       check=True, cwd=HERE, env=env)
        proc = os.path.join(DATA_DIR, "processed", VERSION, instrument)
        for f in ARTIFACTS:
            bucket.blob(f"processed/{VERSION}/{instrument}/{f}").upload_from_filename(
                os.path.join(proc, f))
        done.append(instrument)
    return bucket_name, done


@functions_framework.http
def update_data(request):
    """HTTP entry point — invoked by Cloud Scheduler (OIDC)."""
    bucket, done = _run()
    msg = (f"rebuilt + uploaded {VERSION} [{', '.join(done)}] "
           f"-> gs://{bucket}/processed/{VERSION}/<instrument>/\n")
    print(msg, flush=True)
    return (msg, 200)
