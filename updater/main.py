"""
updater/main.py — scheduled Cloud Function (2nd gen, Python).

Runs daily (via Cloud Scheduler): scrape gsv.fun, rebuild the analysis artifacts,
and upload them to the GCS bucket the Cloud Run service reads from.

Self-contained (own copies of fetch_data.py / build_dataset.py) so it deploys via
buildpacks with no Dockerfile. Writes to /tmp (the only writable path in the
function runtime) via GD_DATA_DIR.

Env:
  GCS_BUCKET   target bucket (required)
  GD_VERSION   game version (default galaxywave_delta)
"""

import os
import subprocess
import sys

import functions_framework

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = os.environ.get("GD_VERSION", "galaxywave_delta")
DATA_DIR = os.environ.get("GD_DATA_DIR", "/tmp/gddata")
ARTIFACTS = ["charts.json", "players.json", "kasegi.json", "meta.json", "matrix.npz"]


def _run():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", GD_DATA_DIR=DATA_DIR)
    subprocess.run([sys.executable, os.path.join(HERE, "fetch_data.py"),
                    "--version", VERSION], check=True, cwd=HERE, env=env)
    subprocess.run([sys.executable, os.path.join(HERE, "build_dataset.py"),
                    "--version", VERSION], check=True, cwd=HERE, env=env)

    bucket_name = os.environ["GCS_BUCKET"]
    from google.cloud import storage
    bucket = storage.Client().bucket(bucket_name)
    proc = os.path.join(DATA_DIR, "processed", VERSION)
    for f in ARTIFACTS:
        bucket.blob(f"processed/{VERSION}/{f}").upload_from_filename(os.path.join(proc, f))
    return bucket_name


@functions_framework.http
def update_data(request):
    """HTTP entry point — invoked by Cloud Scheduler (OIDC)."""
    bucket = _run()
    msg = f"rebuilt + uploaded {VERSION} -> gs://{bucket}/processed/{VERSION}/\n"
    print(msg, flush=True)
    return (msg, 200)
