"""
app.py — zero-dependency HTTP server for the GITADORA DrumMania skill-match app.

Uses only the Python standard library (http.server) so it runs with no pip install
beyond the scientific stack the engine already needs (numpy / scikit-learn). Serves
the JSON API under /api/* and the static frontend from web/.

Run:  python server/app.py            (defaults to http://127.0.0.1:8770)
      python server/app.py --port 9000 --version galaxywave_delta
"""

import argparse
import json
import mimetypes
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB_DIR = os.path.join(ROOT, "web")

# Resolve imports whether app.py is run directly (server dir on sys.path ->
# `import engine`) or as part of the repo (`from server import ...`).
sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from engine import get_engine  # noqa: E402
from server import ingest, userstore  # noqa: E402

VERSION = os.environ.get("GD_VERSION", "galaxywave_delta")

# The upload route is OFF by default — the public cloud deployment stays a
# read-only GET service (spec §5.1). It is enabled only on a local/private
# instance via `--enable-upload` or GD_ENABLE_UPLOAD=1.
ENABLE_UPLOAD = os.environ.get("GD_ENABLE_UPLOAD", "").strip().lower() in (
    "1", "true", "yes", "on")
MAX_UPLOAD_BYTES = ingest.MAX_BYTES

# A browser may POST here cross-origin from the player's authenticated eagate
# tab (spec §5.3): allow only eagate + same-origin, with credentials omitted.
EAGATE_ORIGIN = "https://p.eagate.573.jp"
ALLOWED_ORIGINS = {EAGATE_ORIGIN}


def _json_default(o):
    """Make numpy scalars/arrays JSON-serializable (defensive: numpy 2.x keeps
    np.float32 through round(), unlike 1.26)."""
    import numpy as np
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "gdskill-match/1.0"

    def log_message(self, fmt, *args):  # quieter logging
        sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    # -------- helpers --------
    def _send_json(self, obj, status=200, cors_origin=None):
        body = _json_bytes(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cors_origin is not None:
            self._send_cors_headers(cors_origin)
        self.end_headers()
        self.wfile.write(body)

    # -------- CORS (upload routes only) --------
    def _allowed_origin(self, origin):
        """Echo the request Origin only if it is eagate or this same origin
        (spec §5.3). Returns the allowed origin string, or None."""
        if not origin:
            return None
        if origin in ALLOWED_ORIGINS:
            return origin
        host = self.headers.get("Host")
        if host and (origin == "https://" + host or origin == "http://" + host):
            return origin
        return None

    def _send_cors_headers(self, origin):
        """Emit CORS headers for an allowed origin; credentials are omitted
        (no Access-Control-Allow-Credentials)."""
        allowed = self._allowed_origin(origin)
        self.send_header("Vary", "Origin")
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_file(self, path):
        if not os.path.isfile(path):
            self._send_json({"error": "not found"}, 404)
            return
        ctype, _ = mimetypes.guess_type(path)
        ctype = ctype or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -------- routing --------
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            if path.startswith("/api/"):
                self._api(path, qs)
            else:
                self._static(path)
        except BrokenPipeError:
            pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()  # logged server-side only; not exposed to clients
            try:
                self._send_json({"error": "internal"}, 500)
            except Exception:  # noqa: BLE001
                pass

    def _static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        # prevent path traversal
        rel = os.path.normpath(path).lstrip("\\/")
        full = os.path.join(WEB_DIR, rel)
        if not os.path.abspath(full).startswith(os.path.abspath(WEB_DIR)):
            self._send_json({"error": "forbidden"}, 403)
            return
        self._send_file(full)

    def _api(self, path, qs):
        # Write routes are POST-only; a GET on them is never the engine's job.
        if path in ("/api/upload", "/api/publish"):
            self._send_json({"error": "not found"}, 404)
            return
        e = get_engine(VERSION)
        parts = [p for p in path.split("/") if p]   # e.g. ['api','player','12','profile']

        if path == "/api/meta":
            self._send_json({
                "version": VERSION, "versionName": _version_name(VERSION),
                "players": e.P, "charts": e.C, "supportCharts": e.meta["supportCharts"],
                "svdDim": e.meta["svdDim"],
                "kasegiBrackets": e.meta.get("kasegiBrackets", []),
                "builtAt": e.meta.get("builtAt"),
            })
            return

        if path == "/api/search":
            q = (qs.get("q", [""])[0])
            self._send_json({"results": e.find_players(q, limit=25)})
            return

        if path == "/api/top":
            self._send_json({"results": e.top_players(limit=60)})
            return

        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "player":
            try:
                pid = int(parts[2])
            except ValueError:
                self._send_json({"error": "bad id"}, 400)
                return
            if pid < 0 or pid >= e.P:
                self._send_json({"error": "id out of range"}, 404)
                return
            # Inject the player's uploaded overlay (spec §6): the owner (matching
            # `?token=`) sees their private overlay, everyone sees public ones.
            # No upload -> (None, None) -> exact existing base behaviour.
            token = qs.get("token", [None])[0]
            ov, visibility = self._player_overlay(e, pid, token)
            sub = parts[3] if len(parts) >= 4 else "all"
            if sub == "profile":
                prof = e.profile(pid, overlay=ov)
                if visibility is not None:
                    prof["visibility"] = visibility
                self._send_json(prof)
            elif sub == "similar":
                resp = {"results": e.similar_players(pid, overlay=ov)}
                if visibility is not None:
                    resp["enhanced"] = ov is not None
                    resp["visibility"] = visibility
                self._send_json(resp)
            elif sub == "rivals":
                self._send_json({"results": e.rivals(pid)})
            elif sub == "songs":
                recs = e.song_recs(pid, overlay=ov)
                if visibility is not None:
                    recs["visibility"] = visibility
                self._send_json(recs)
            elif sub == "chart":
                if len(parts) < 5:
                    self._send_json({"error": "missing chart id"}, 400)
                    return
                try:
                    cid = int(parts[4])
                except ValueError:
                    self._send_json({"error": "bad chart id"}, 400)
                    return
                if cid < 0 or cid >= e.C:
                    self._send_json({"error": "chart id out of range"}, 404)
                    return
                self._send_json(e.chart_detail(pid, cid))
            elif sub == "all":
                prof = e.profile(pid, overlay=ov)
                songs = e.song_recs(pid, overlay=ov)
                if visibility is not None:
                    prof["visibility"] = visibility
                    songs["visibility"] = visibility
                payload = {
                    "profile": prof,
                    "similar": e.similar_players(pid, overlay=ov),
                    "rivals": e.rivals(pid),
                    "songs": songs,
                }
                if visibility is not None:
                    payload["enhanced"] = ov is not None
                    payload["visibility"] = visibility
                self._send_json(payload)
            else:
                self._send_json({"error": "unknown endpoint"}, 404)
            return

        self._send_json({"error": "unknown endpoint"}, 404)

    def _player_overlay(self, e, pid, token):
        """Resolve `(Overlay | None, visibility | None)` for a player request.

        Reads the stored latest overlay to learn its visibility + share token,
        then asks the engine for the overlay with owner gating: the owner (token
        match) always sees it, everyone else only when it is `public`. Any
        failure falls back silently to base behaviour (spec §10)."""
        try:
            latest = userstore.get_latest(VERSION, e.players[pid]["playerId"])
        except Exception:  # noqa: BLE001 — never break the read path
            latest = None
        if not isinstance(latest, dict):
            return None, None
        visibility = latest.get("visibility", "private")
        as_owner = bool(token) and token == latest.get("token")
        try:
            ov = e.get_overlay(pid, as_owner=as_owner)
        except Exception:  # noqa: BLE001
            ov = None
        return ov, visibility

    # -------- writes (guarded by --enable-upload) --------
    def do_OPTIONS(self):
        parsed = urlparse(self.path)
        origin = self.headers.get("Origin")
        if ENABLE_UPLOAD and parsed.path in ("/api/upload", "/api/publish"):
            self.send_response(204)
            self._send_cors_headers(origin)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            # Off by default: the cloud GET service exposes no write surface.
            if not ENABLE_UPLOAD or path not in ("/api/upload", "/api/publish"):
                self._send_json({"error": "not found"}, 404)
                return
            if path == "/api/upload":
                self._handle_upload()
            else:
                self._handle_publish()
        except BrokenPipeError:
            pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()  # logged server-side only; not exposed to clients
            try:
                self._send_json({"error": "internal"}, 500)
            except Exception:  # noqa: BLE001
                pass

    def _read_body(self):
        """Read the request body, enforcing the upload size cap before parsing.

        Returns `(payload_dict, None)` on success or `(None, (status, body))`
        with an error response to send."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            return None, (413, {"errors": [
                f"payload too large: {length} bytes > {MAX_UPLOAD_BYTES}"]})
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            return json.loads(raw.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError):
            return None, (400, {"errors": ["body is not valid JSON"]})

    def _handle_upload(self):
        origin = self.headers.get("Origin")
        payload, err = self._read_body()
        if err is not None:
            self._send_json(err[1], err[0], cors_origin=origin)
            return
        status, body = ingest.ingest(payload, VERSION)
        self._send_json(body, status, cors_origin=origin)

    def _handle_publish(self):
        """Flip a stored overlay's visibility (spec §9) after a share-token check."""
        origin = self.headers.get("Origin")
        payload, err = self._read_body()
        if err is not None:
            self._send_json(err[1], err[0], cors_origin=origin)
            return
        gsv_id = payload.get("gsvPlayerId")
        token = payload.get("token")
        visibility = payload.get("visibility")
        if visibility not in ("private", "public"):
            self._send_json({"error": "visibility must be 'private' or 'public'"},
                            400, cors_origin=origin)
            return
        if not (isinstance(gsv_id, int) and not isinstance(gsv_id, bool) and gsv_id > 0):
            self._send_json({"error": "gsvPlayerId must be a positive integer"},
                            400, cors_origin=origin)
            return
        try:
            latest = userstore.get_latest(VERSION, gsv_id)
        except Exception:  # noqa: BLE001
            latest = None
        if not isinstance(latest, dict):
            self._send_json({"error": "no overlay for that player"},
                            404, cors_origin=origin)
            return
        if not token or token != latest.get("token"):
            self._send_json({"error": "invalid token"}, 403, cors_origin=origin)
            return
        latest["visibility"] = visibility
        userstore.set_latest(VERSION, gsv_id, latest)
        self._send_json({"status": "ok", "gsvPlayerId": gsv_id, "visibility": visibility},
                        200, cors_origin=origin)


def _version_name(v):
    return {
        "galaxywave_delta": "GALAXY WAVE DELTA", "galaxywave": "GALAXY WAVE",
        "fuzzup": "FUZZ-UP", "highvoltage": "HIGH-VOLTAGE",
    }.get(v, v)


def main():
    global VERSION, ENABLE_UPLOAD
    # Cloud Run injects $PORT and expects 0.0.0.0; locally default to 127.0.0.1:8770.
    env_port = os.environ.get("PORT")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(env_port) if env_port else 8770)
    ap.add_argument("--host", default="0.0.0.0" if env_port else "127.0.0.1")
    ap.add_argument("--version", default=VERSION)
    ap.add_argument("--enable-upload", action="store_true", default=ENABLE_UPLOAD,
                    help="mount POST /api/upload + /api/publish (off by default; "
                         "the public cloud service stays read-only)")
    args = ap.parse_args()
    VERSION = args.version
    ENABLE_UPLOAD = args.enable_upload
    if ENABLE_UPLOAD:
        print("[app] upload route ENABLED (POST /api/upload, /api/publish)", flush=True)

    # In the cloud, pull the latest analysis artifacts from GCS (or bootstrap them
    # by scraping+building) before the engine loads. No-op locally if files exist.
    try:
        import cloudstore
        cloudstore.ensure_artifacts(VERSION)
    except Exception as ex:  # noqa: BLE001 - never block local dev on this
        print(f"[app] cloudstore skipped: {ex}", flush=True)

    print(f"[app] loading engine for version={VERSION} ...", flush=True)
    e = get_engine(VERSION)
    print(f"[app] engine ready: {e.P} players, {e.C} charts", flush=True)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"[app] serving on {url}  (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[app] shutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
