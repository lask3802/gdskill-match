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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import get_engine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB_DIR = os.path.join(ROOT, "web")

VERSION = os.environ.get("GD_VERSION", "galaxywave_delta")


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
    def _send_json(self, obj, status=200):
        body = _json_bytes(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
            sub = parts[3] if len(parts) >= 4 else "all"
            if sub == "profile":
                self._send_json(e.profile(pid))
            elif sub == "similar":
                self._send_json({"results": e.similar_players(pid)})
            elif sub == "rivals":
                self._send_json({"results": e.rivals(pid)})
            elif sub == "songs":
                self._send_json(e.song_recs(pid))
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
                self._send_json({
                    "profile": e.profile(pid),
                    "similar": e.similar_players(pid),
                    "rivals": e.rivals(pid),
                    "songs": e.song_recs(pid),
                })
            else:
                self._send_json({"error": "unknown endpoint"}, 404)
            return

        self._send_json({"error": "unknown endpoint"}, 404)


def _version_name(v):
    return {
        "galaxywave_delta": "GALAXY WAVE DELTA", "galaxywave": "GALAXY WAVE",
        "fuzzup": "FUZZ-UP", "highvoltage": "HIGH-VOLTAGE",
    }.get(v, v)


def main():
    global VERSION
    # Cloud Run injects $PORT and expects 0.0.0.0; locally default to 127.0.0.1:8770.
    env_port = os.environ.get("PORT")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(env_port) if env_port else 8770)
    ap.add_argument("--host", default="0.0.0.0" if env_port else "127.0.0.1")
    ap.add_argument("--version", default=VERSION)
    args = ap.parse_args()
    VERSION = args.version

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
