# tests/test_app_instrument.py
"""App-level instrument routing (?inst=) + /api/meta instrument discovery.

Spins up the real stdlib server against a tmp GD_DATA_DIR holding BOTH a drum and
a guitar processed dir, then asserts the server routes by ?inst=, advertises the
available instruments, and rejects an unknown instrument.
"""
import http.client
import importlib
import json as _json
import os
import sys
import threading

import numpy as np
import pytest
from http.server import ThreadingHTTPServer

VERSION = "galaxywave_delta"


def _write_processed(root, instrument, names):
    """Minimal but engine-loadable processed dir. `names` -> player name prefix so
    we can tell which instrument answered."""
    proc = os.path.join(root, "processed", VERSION, instrument)
    os.makedirs(proc, exist_ok=True)
    C, P = 4, 3
    levels = np.array([7.0, 7.5, 8.0, 8.5], dtype=np.float32)
    pool_is_hot = np.array([1, 1, 0, 0], dtype=np.uint8)
    presence = np.array([[1, 1, 0, 0], [1, 0, 1, 0], [0, 1, 1, 1]], dtype=np.uint8)
    ach = presence * 0.9
    skill = (ach * levels * 20.0).astype(np.float32)
    counts = presence.sum(axis=0).astype(np.float32)
    chart_mean = np.where(counts > 0, skill.sum(axis=0) / np.maximum(counts, 1), 0).astype(np.float32)
    chart_std = np.full(C, 5.0, dtype=np.float32)
    support_mask = np.ones(C, dtype=bool)
    emb = np.eye(P, 2, dtype=np.float32)
    part = "D" if instrument == "drum" else "G"
    charts = [{"id": ci, "name": f"{names}{ci}", "diff": "MAS", "part": part,
               "level": float(levels[ci]), "pool": "hot" if pool_is_hot[ci] else "other",
               "count": int(counts[ci]), "skill_mean": float(chart_mean[ci]),
               "skill_std": 5.0, "skill_max": float(skill[:, ci].max()),
               "ach_mean": 0.9, "ach_std": 0.05} for ci in range(C)]
    players = [{"id": pi, "playerId": 100 + pi, "name": f"{names}player{pi}",
                "sp": 3000.0 - pi * 50, "hotPoint": 0.0, "otherPoint": 0.0,
                "hotCount": 2, "otherCount": 2, "hotCutoff": 40.0, "otherCutoff": 40.0,
                "updateDate": "2026-06-27"} for pi in range(P)]
    with open(os.path.join(proc, "charts.json"), "w", encoding="utf-8") as fh:
        _json.dump(charts, fh)
    with open(os.path.join(proc, "players.json"), "w", encoding="utf-8") as fh:
        _json.dump(players, fh)
    with open(os.path.join(proc, "kasegi.json"), "w", encoding="utf-8") as fh:
        _json.dump([{"scope": 0, "hot": [], "other": []}], fh)
    with open(os.path.join(proc, "meta.json"), "w", encoding="utf-8") as fh:
        _json.dump({"version": VERSION, "instrument": instrument, "players": P,
                    "charts": C, "supportCharts": C, "minSupport": 1, "svdDim": 2,
                    "kasegiBrackets": [0], "builtAt": "2026-06-27T00:00:00+00:00"}, fh)
    np.savez_compressed(os.path.join(proc, "matrix.npz"),
                        skill=skill, presence=presence, ach=ach, levels=levels,
                        pool_is_hot=pool_is_hot, chart_count=counts, chart_mean=chart_mean,
                        chart_std=chart_std, support_mask=support_mask, emb=emb)


def _reload_app():
    for name in ("server.ranks", "server.userstore", "server.overlay", "server.ingest"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    if "engine" in sys.modules:
        importlib.reload(sys.modules["engine"])
    if "server.app" in sys.modules:
        return importlib.reload(sys.modules["server.app"])
    return importlib.import_module("server.app")


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read()
    out = (resp.status, _json.loads(raw) if raw else None)
    conn.close()
    return out


@pytest.fixture
def server(tmp_path, monkeypatch):
    started = []
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    _write_processed(str(tmp_path), "drum", "DRUM")
    _write_processed(str(tmp_path), "guitar", "GTR")
    app = _reload_app()
    app.VERSION = VERSION
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    started.append(httpd)
    yield httpd.server_address[1]
    for h in started:
        h.shutdown()
        h.server_close()


def test_meta_defaults_to_drum_and_lists_instruments(server):
    st, body = _get(server, "/api/meta")
    assert st == 200
    assert body["instrument"] == "drum"
    assert set(body["instruments"]) == {"drum", "guitar"}


def test_meta_reports_requested_instrument(server):
    st, body = _get(server, "/api/meta?inst=guitar")
    assert st == 200
    assert body["instrument"] == "guitar"


def test_player_routes_to_requested_instrument(server):
    st_d, prof_d = _get(server, "/api/player/0/profile")
    st_g, prof_g = _get(server, "/api/player/0/profile?inst=guitar")
    assert st_d == 200 and st_g == 200
    assert prof_d["player"]["name"].startswith("DRUM")
    assert prof_g["player"]["name"].startswith("GTR")
    # guitar gsv link points to the guitar profile
    assert prof_g["player"]["gsvUrl"].endswith("/g")


def test_unknown_instrument_is_400(server):
    st, body = _get(server, "/api/player/0/profile?inst=bogus")
    assert st == 400


def test_search_respects_instrument(server):
    st, body = _get(server, "/api/search?q=player&inst=guitar")
    assert st == 200
    assert body["results"]
    assert all(r["name"].startswith("GTR") for r in body["results"])
