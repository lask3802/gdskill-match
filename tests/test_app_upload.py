# tests/test_app_upload.py
"""App wiring — guarded upload route + overlay injection + publish toggle
(spec §5.3, §6, §9; plan Task 6).

Spins up the real `server.app` stdlib HTTP server on an ephemeral port against a
tiny synthetic processed dir + a tmp `GD_DATA_DIR`, then asserts:

  * `--enable-upload` / GD_ENABLE_UPLOAD gates the write routes — when OFF,
    POST/GET `/api/upload` is 404 (the cloud deploy stays read-only);
  * OPTIONS `/api/upload` returns the eagate CORS preflight header;
  * a valid same-origin upload links + stores + returns a share token (200);
  * an oversize body is rejected with 413 before ingest runs;
  * `/api/publish` flips visibility only after a matching share-token check;
  * player endpoints inject the overlay: the owner (correct `?token=`) sees the
    private overlay (`enhanced`), a non-owner does not; a public overlay is seen
    by everyone. `visibility` is surfaced in the response.
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

# name, diff, level, pool  (mirrors tests/test_engine_overlay.py)
CHARTS = [
    ("A", "MAS", 8.0, "hot"),
    ("B", "MAS", 8.5, "hot"),
    ("C", "EXT", 7.0, "hot"),
    ("D", "MAS", 9.0, "hot"),
    ("E", "EXT", 6.5, "other"),
    ("F", "ADV", 5.0, "other"),
    ("G", "MAS", 8.2, "other"),
    ("H", "EXT", 7.5, "other"),
]

BASE = {
    0: {0: 0.95, 1: 0.92},
    1: {0: 0.88, 2: 0.80},
    2: {1: 0.90, 3: 0.70, 4: 0.85},
    3: {2: 0.75, 4: 0.82, 6: 0.78},
    4: {5: 0.90, 7: 0.72},
}
SP = [2000.0, 1900.0, 2100.0, 1800.0, 1950.0]


def _build_processed(root):
    proc = os.path.join(root, "processed", VERSION, "drum")
    os.makedirs(proc, exist_ok=True)
    C = len(CHARTS)
    P = len(BASE)
    levels = np.array([c[2] for c in CHARTS], dtype=np.float32)
    pool_is_hot = np.array([1 if c[3] == "hot" else 0 for c in CHARTS], dtype=np.uint8)
    skill = np.zeros((P, C), dtype=np.float32)
    ach = np.zeros((P, C), dtype=np.float32)
    presence = np.zeros((P, C), dtype=np.uint8)
    for pi, sheet in BASE.items():
        for ci, a in sheet.items():
            ach[pi, ci] = a
            skill[pi, ci] = levels[ci] * 20.0 * a
            presence[pi, ci] = 1

    counts = presence.sum(axis=0).astype(np.float32)
    chart_mean = np.zeros(C, dtype=np.float32)
    chart_std = np.zeros(C, dtype=np.float32)
    ach_mean = np.zeros(C, dtype=np.float32)
    for ci in range(C):
        m = presence[:, ci] > 0
        if m.any():
            chart_mean[ci] = float(skill[m, ci].mean())
            chart_std[ci] = max(float(skill[m, ci].std()), 5.0)
            ach_mean[ci] = float(ach[m, ci].mean())
        else:
            chart_std[ci] = 5.0
    support_mask = np.ones(C, dtype=bool)
    rng = np.random.default_rng(0)
    emb = rng.standard_normal((P, 4)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)

    charts = []
    for ci, (name, diff, level, pool) in enumerate(CHARTS):
        charts.append({
            "id": ci, "name": name, "diff": diff, "level": round(float(level), 2),
            "pool": pool, "count": int(counts[ci]),
            "skill_mean": round(float(chart_mean[ci]), 2),
            "skill_std": round(float(chart_std[ci]), 2),
            "skill_max": round(float(skill[:, ci].max()), 2),
            "ach_mean": round(float(ach_mean[ci]), 4),
            "ach_std": 0.05,
        })

    players = []
    for pi in range(P):
        hot_cut, other_cut = (140.0, 120.0) if pi == 0 else (50.0, 50.0)
        players.append({
            "id": pi, "playerId": 100 + pi, "name": f"P{pi}", "sp": SP[pi],
            "hotPoint": 0.0, "otherPoint": 0.0,
            "hotCount": int(((presence[pi] > 0) & (pool_is_hot > 0)).sum()),
            "otherCount": int(((presence[pi] > 0) & (pool_is_hot == 0)).sum()),
            "hotCutoff": hot_cut, "otherCutoff": other_cut,
            "updateDate": "2026-06-26",
        })

    with open(os.path.join(proc, "charts.json"), "w", encoding="utf-8") as fh:
        _json.dump(charts, fh)
    with open(os.path.join(proc, "players.json"), "w", encoding="utf-8") as fh:
        _json.dump(players, fh)
    with open(os.path.join(proc, "kasegi.json"), "w", encoding="utf-8") as fh:
        _json.dump([{"scope": 0, "hot": [], "other": []}], fh)
    with open(os.path.join(proc, "meta.json"), "w", encoding="utf-8") as fh:
        _json.dump({"version": VERSION, "players": P, "charts": C,
                    "supportCharts": C, "minSupport": 1, "svdDim": 4,
                    "kasegiBrackets": [0], "builtAt": "2026-06-26T00:00:00+00:00"}, fh)
    np.savez_compressed(
        os.path.join(proc, "matrix.npz"),
        skill=skill, presence=presence, ach=ach, levels=levels,
        pool_is_hot=pool_is_hot, chart_count=counts, chart_mean=chart_mean,
        chart_std=chart_std, support_mask=support_mask, emb=emb,
    )


def _overlay_latest(visibility="private", token="tok"):
    """A dense overlay for db player 0 (gsvPlayerId 100): exact + rank-only,
    incl. charts below the player's cutoff (practice targets)."""
    return {
        "schema": 1, "version": VERSION, "gsvPlayerId": 100,
        "visibility": visibility, "linkedDbId": 0, "token": token,
        "profile": {"playerName": "P0", "drumSkillPoint": 2000.0, "allSongSkill": 1234.5},
        "charts": [
            {"sid": "1", "name": "A", "diff": "MAS", "rank": "S",
             "achievement": 0.93, "exact": True, "level": 8.0},
            {"sid": "2", "name": "B", "diff": "MAS", "rank": "S",
             "achievement": 0.92, "exact": True, "level": 8.5},
            {"sid": "3", "name": "C", "diff": "EXT", "rank": "A",
             "achievement": None, "exact": False, "level": 7.0},
            {"sid": "4", "name": "D", "diff": "MAS", "rank": "B",
             "achievement": None, "exact": False, "level": 9.0},
            {"sid": "5", "name": "E", "diff": "EXT", "rank": "S",
             "achievement": 0.91, "exact": True, "level": 6.5},
            {"sid": "7", "name": "G", "diff": "MAS", "rank": "C",
             "achievement": None, "exact": False, "level": 8.2},
            {"sid": "8", "name": "H", "diff": "EXT", "rank": "B",
             "achievement": 0.70, "exact": True, "level": 7.5},
        ],
    }


def _valid_payload():
    """A minimal upload that links to db player 0 (name + skillpoint match)."""
    return {
        "schema": 1, "version": VERSION, "game": "gitadora", "gtype": "dm",
        "gsvPlayerId": 100, "scrapedAt": "2026-06-26T00:00:00Z", "mode": "standard",
        "profile": {"playerName": "P0", "drumSkillPoint": 2000.0, "allSongSkill": 1234.5},
        "charts": [{"sid": "1", "name": "A", "diff": "MAS", "rank": "S",
                    "achievement": 0.93, "exact": True, "level": 8.0,
                    "playCount": 3, "clearCount": 1}],
    }


def _reload_app():
    """Reload the module chain so DATA_BASE / ENABLE_UPLOAD pick up the env that
    the calling fixture has just set (mirrors test_engine_overlay's reload)."""
    for name in ("server.ranks", "server.userstore", "server.overlay", "server.ingest"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    if "engine" in sys.modules:        # top-level `engine` is what app.py imports
        importlib.reload(sys.modules["engine"])
    if "server.app" in sys.modules:
        return importlib.reload(sys.modules["server.app"])
    return importlib.import_module("server.app")


def _request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = dict(headers or {})
    data = None
    if body is not None:
        data = _json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    out = (resp.status, dict(resp.getheaders()), raw)
    conn.close()
    return out


@pytest.fixture
def make_server(tmp_path, monkeypatch):
    started = []

    def _make(enable=True):
        monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        if enable:
            monkeypatch.setenv("GD_ENABLE_UPLOAD", "1")
        else:
            monkeypatch.delenv("GD_ENABLE_UPLOAD", raising=False)
        _build_processed(str(tmp_path))
        app = _reload_app()
        app.VERSION = VERSION
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        started.append(httpd)
        return app, httpd.server_address[1]

    yield _make
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------- #

def test_upload_disabled_is_404(make_server):
    _app, port = make_server(enable=False)
    st_post, _, _ = _request(port, "POST", "/api/upload", body=_valid_payload())
    assert st_post == 404
    st_get, _, _ = _request(port, "GET", "/api/upload")
    assert st_get == 404


def test_options_cors_preflight(make_server):
    _app, port = make_server(enable=True)
    st, hdrs, _ = _request(port, "OPTIONS", "/api/upload",
                           headers={"Origin": "https://p.eagate.573.jp"})
    assert st in (200, 204)
    assert hdrs.get("Access-Control-Allow-Origin") == "https://p.eagate.573.jp"


def test_upload_valid_links_and_returns_token(make_server):
    _app, port = make_server(enable=True)
    st, hdrs, raw = _request(port, "POST", "/api/upload", body=_valid_payload(),
                             headers={"Origin": "https://p.eagate.573.jp"})
    assert st == 200
    body = _json.loads(raw)
    assert body["status"] == "linked"
    assert body["token"]
    assert body["visibility"] == "private"
    # cross-origin caller can read the response
    assert hdrs.get("Access-Control-Allow-Origin") == "https://p.eagate.573.jp"


def test_upload_oversize_is_413(make_server, monkeypatch):
    app, port = make_server(enable=True)
    monkeypatch.setattr(app, "MAX_UPLOAD_BYTES", 50)
    st, _, _ = _request(port, "POST", "/api/upload", body=_valid_payload())
    assert st == 413


def test_publish_requires_token_and_toggles_visibility(make_server):
    app, port = make_server(enable=True)
    st, _, raw = _request(port, "POST", "/api/upload", body=_valid_payload())
    assert st == 200
    token = _json.loads(raw)["token"]

    st_bad, _, _ = _request(port, "POST", "/api/publish",
                            body={"gsvPlayerId": 100, "token": "nope", "visibility": "public"})
    assert st_bad == 403

    st_ok, _, raw_ok = _request(port, "POST", "/api/publish",
                                body={"gsvPlayerId": 100, "token": token, "visibility": "public"})
    assert st_ok == 200
    assert _json.loads(raw_ok)["visibility"] == "public"

    import server.userstore as us
    assert us.get_latest(VERSION, 100)["visibility"] == "public"


def test_owner_sees_private_overlay_others_do_not(make_server):
    _app, port = make_server(enable=True)
    import server.userstore as us
    us.set_latest(VERSION, 100, _overlay_latest(visibility="private", token="tok"))

    st, _, raw = _request(port, "GET", "/api/player/0/profile?token=tok")
    assert st == 200
    prof = _json.loads(raw)
    assert prof.get("enhanced") is True
    assert prof.get("visibility") == "private"

    st2, _, raw2 = _request(port, "GET", "/api/player/0/profile")
    prof2 = _json.loads(raw2)
    assert prof2.get("enhanced") is not True
    assert prof2.get("visibility") == "private"


def test_public_overlay_visible_to_non_owner(make_server):
    _app, port = make_server(enable=True)
    import server.userstore as us
    us.set_latest(VERSION, 100, _overlay_latest(visibility="public", token="tok"))

    st, _, raw = _request(port, "GET", "/api/player/0/songs")
    assert st == 200
    recs = _json.loads(raw)
    assert recs.get("enhanced") is True
    assert recs.get("visibility") == "public"
    assert "practiceTargets" in recs


def test_player_without_overlay_is_unchanged(make_server):
    _app, port = make_server(enable=True)
    st, _, raw = _request(port, "GET", "/api/player/1/profile")
    assert st == 200
    prof = _json.loads(raw)
    assert "visibility" not in prof
    assert prof.get("enhanced") is not True
