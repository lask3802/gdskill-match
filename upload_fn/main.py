"""
upload_fn/main.py — HTTP Cloud Function entrypoint for full-data uploads.

A thin transport adapter around `server/ingest.py`: it does CORS + method
routing only, then delegates validation / identity-linking / storage to
`ingest.ingest`, which writes solely under `userdata/*` (via `server.userstore`)
and never touches matrix.npz or the base per-chart stats.

Deployed as a standalone Functions-Framework HTTP function (entry-point
`upload`) with its own minimal service account — write-only to
`gs://$GCS_BUCKET/userdata/*`. This keeps the read-only Cloud Run service in
`server/app.py` untouched (spec §5.1). In the cloud, set `GCS_BUCKET` so
`userstore` targets GCS; `GD_VERSION` selects the engine version.

The function deliberately does NOT import `functions_framework` at module level
(the plain `def upload(request)` signature works with
`functions-framework --target upload`), so the logic stays importable/testable
without the framework or Flask installed.

Hard rules honoured (delegated to `server.ingest`): no card numbers / cookies /
raw HTML are ever stored; uploads are an overlay only; identity is self-attested
(ambiguous -> quarantine, never overwrite); private by default. CORS echoes only
eagate + the configured Cloud Run origin, with credentials omitted (spec §5.3).
"""

import json
import os
import sys

# Resolve `from server import ingest` whether the function is deployed with the
# repo root on the path or invoked from this directory (mirrors app.py's shim).
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import ingest  # noqa: E402

VERSION = os.environ.get("GD_VERSION", "galaxywave_delta")

# CORS: allow eagate + the Cloud Run origin only; credentials omitted (spec §5.3).
# Extra origins (e.g. the deployed Cloud Run URL) come from GD_ALLOWED_ORIGINS
# (comma-separated) so nothing is hardcoded.
EAGATE_ORIGIN = "https://p.eagate.573.jp"


def _allowed_origins():
    origins = {EAGATE_ORIGIN}
    for o in os.environ.get("GD_ALLOWED_ORIGINS", "").split(","):
        o = o.strip()
        if o:
            origins.add(o)
    return origins


def _cors_headers(origin):
    """Echo the request Origin only when allowed; always set Vary: Origin."""
    headers = {"Vary": "Origin"}
    if origin and origin in _allowed_origins():
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type"
    return headers


def _json_response(body, status, cors):
    headers = dict(cors)
    headers["Content-Type"] = "application/json; charset=utf-8"
    return (json.dumps(body, ensure_ascii=False), status, headers)


def upload(request):
    """Functions-Framework HTTP entrypoint.

    Returns a Flask-style `(body, status, headers)` tuple — Functions Framework
    turns that into the HTTP response. No HTTP framework objects are constructed
    here, so the function is unit-testable with a plain fake request.
    """
    origin = request.headers.get("Origin")
    cors = _cors_headers(origin)

    method = getattr(request, "method", "GET")
    if method == "OPTIONS":
        headers = dict(cors)
        headers["Content-Length"] = "0"
        return ("", 204, headers)
    if method != "POST":
        return _json_response({"error": "method not allowed"}, 405, cors)

    # Enforce the size cap before parsing, when the client declares it
    # (defence in depth; ingest.validate also bounds the serialised payload).
    try:
        length = int(request.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    if length > ingest.MAX_BYTES:
        return _json_response(
            {"errors": [f"payload too large: {length} bytes > {ingest.MAX_BYTES}"]},
            413, cors)

    try:
        payload = request.get_json(silent=True)
    except Exception:  # noqa: BLE001 — a malformed body must not 500
        payload = None
    if payload is None:
        return _json_response({"errors": ["body is not valid JSON"]}, 400, cors)

    status, body = ingest.ingest(payload, VERSION)
    return _json_response(body, status, cors)
