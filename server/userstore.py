"""
userstore.py — read/write the per-player upload overlay store (`userdata/*`).

Source of truth for uploaded full-data overlays. Mirrors `cloudstore.py`'s
local-FS / GCS duality and env handling so local development never depends on
the cloud:

  - Local FS root: ${GD_DATA_DIR or repo}/data/userdata
  - GCS:           gs://$GCS_BUCKET/userdata/...   (lazy import, only in cloud)

Layout (spec §5.4) — daily base rebuild MUST NOT touch any of these:
  userdata/raw/v1/<version>/<gsvPlayerId>/<uploadId>.json   # immutable raw upload
  userdata/latest/v1/<version>/<gsvPlayerId>.json           # latest overlay metrics
  userdata/unlinked/v1/<uploadId>.json                      # quarantined / unlinked

This module only ever writes under `userdata/`; it never touches matrix.npz or
the base per-chart stats.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_BASE = os.environ.get("GD_DATA_DIR") or os.path.join(ROOT, "data")
USERDATA_DIR = os.path.join(DATA_BASE, "userdata")

# Schema generation for the on-disk layout (separate from payload `schema`).
LAYOUT = "v1"


def _bucket():
    name = os.environ.get("GCS_BUCKET")
    if not name:
        return None
    try:
        from google.cloud import storage  # lazy: not needed locally
    except Exception:  # noqa: BLE001
        return None
    return storage.Client().bucket(name)


# --- GCS object names (also used as POSIX-style relative paths locally) -------

def _raw_name(version, gsv_player_id, upload_id):
    return f"userdata/raw/{LAYOUT}/{version}/{gsv_player_id}/{upload_id}.json"


def _latest_name(version, gsv_player_id):
    return f"userdata/latest/{LAYOUT}/{version}/{gsv_player_id}.json"


def _latest_prefix(version):
    return f"userdata/latest/{LAYOUT}/{version}/"


def _unlinked_name(upload_id):
    return f"userdata/unlinked/{LAYOUT}/{upload_id}.json"


def _local_path(name):
    """Map a GCS object name to a local filesystem path under DATA_BASE."""
    return os.path.join(DATA_BASE, *name.split("/"))


# --- low-level read / write ---------------------------------------------------

def _write_json(name, obj):
    text = json.dumps(obj, ensure_ascii=False)
    b = _bucket()
    if b is not None:
        b.blob(name).upload_from_string(text, content_type="application/json")
        return
    path = _local_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_json(name):
    b = _bucket()
    if b is not None:
        blob = b.blob(name)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    path = _local_path(name)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --- public API ---------------------------------------------------------------

def save_upload(version, gsv_player_id, upload_id, payload):
    """Write an immutable raw upload to userdata/raw/v1/<version>/<id>/<uploadId>.json."""
    _write_json(_raw_name(version, gsv_player_id, upload_id), payload)


def set_latest(version, gsv_player_id, latest):
    """Write the latest overlay metrics to userdata/latest/v1/<version>/<id>.json."""
    _write_json(_latest_name(version, gsv_player_id), latest)


def get_latest(version, gsv_player_id):
    """Return the latest overlay dict for a player, or None if absent."""
    return _read_json(_latest_name(version, gsv_player_id))


def save_unlinked(upload_id, payload):
    """Quarantine an upload whose identity could not be linked."""
    _write_json(_unlinked_name(upload_id), payload)


def list_latest(version):
    """gsvPlayerIds (ints) that have a latest overlay for this version."""
    out = []
    b = _bucket()
    if b is not None:
        prefix = _latest_prefix(version)
        for blob in b.list_blobs(prefix=prefix):
            stem = blob.name[len(prefix):]
            if stem.endswith(".json"):
                stem = stem[:-len(".json")]
            try:
                out.append(int(stem))
            except (TypeError, ValueError):
                continue
        return out
    d = _local_path(_latest_prefix(version).rstrip("/"))
    if not os.path.isdir(d):
        return out
    for fname in os.listdir(d):
        if not fname.endswith(".json"):
            continue
        try:
            out.append(int(fname[:-len(".json")]))
        except (TypeError, ValueError):
            continue
    return out
