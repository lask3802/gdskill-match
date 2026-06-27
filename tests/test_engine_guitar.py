# tests/test_engine_guitar.py
"""Engine instrument parameterization (GuitarFreaks read-side).

Builds a tiny synthetic processed dir under processed/<version>/guitar/ — where a
song's guitar (G) and bass (B) charts at the same diff are DISTINCT — and asserts
Engine(version, "guitar"):
  * loads from the instrument subdir;
  * links out to the gsv guitar profile (URL ends /g, not /d);
  * keeps G/B charts separate (chart_detail reports part);
  * returns the same response shapes as drum for profile / similar / songs;
  * exposes no overlay (upload stays DrumMania-only this round).
"""
import importlib
import json
import os

import numpy as np
import pytest

VERSION = "gfver"

# name, diff, part, level, pool  — note Overlap appears as both G and B at MAS
CHARTS = [
    ("Overlap", "MAS", "G", 8.0, "hot"),
    ("Overlap", "MAS", "B", 8.0, "hot"),
    ("Solo", "EXT", "G", 7.0, "hot"),
    ("Lead", "MAS", "G", 8.5, "hot"),
    ("Groove", "ADV", "B", 5.0, "other"),
    ("Riff", "MAS", "G", 8.2, "other"),
    ("Walk", "EXT", "B", 6.5, "other"),
    ("Chord", "EXT", "G", 7.5, "other"),
]

BASE = {
    0: {0: 0.95, 1: 0.90, 2: 0.93},
    1: {0: 0.88, 2: 0.80, 3: 0.85},
    2: {1: 0.90, 3: 0.70, 4: 0.85},
    3: {2: 0.75, 4: 0.82, 5: 0.78},
    4: {5: 0.90, 6: 0.72, 7: 0.80},
    5: {0: 0.91, 2: 0.86, 7: 0.74},
}
SP = [3100.0, 3050.0, 3000.0, 2950.0, 2900.0, 3020.0]


def _build_processed_guitar(root):
    proc = os.path.join(root, "processed", VERSION, "guitar")
    os.makedirs(proc, exist_ok=True)
    C = len(CHARTS)
    P = len(BASE)
    levels = np.array([c[3] for c in CHARTS], dtype=np.float32)
    pool_is_hot = np.array([1 if c[4] == "hot" else 0 for c in CHARTS], dtype=np.uint8)
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
    for ci, (name, diff, part, level, pool) in enumerate(CHARTS):
        charts.append({
            "id": ci, "name": name, "diff": diff, "part": part,
            "level": round(float(level), 2), "pool": pool, "count": int(counts[ci]),
            "skill_mean": round(float(chart_mean[ci]), 2),
            "skill_std": round(float(chart_std[ci]), 2),
            "skill_max": round(float(skill[:, ci].max()), 2),
            "ach_mean": round(float(ach_mean[ci]), 4), "ach_std": 0.05,
        })

    players = []
    for pi in range(P):
        players.append({
            "id": pi, "playerId": 200 + pi, "name": f"GP{pi}", "sp": SP[pi],
            "hotPoint": 0.0, "otherPoint": 0.0,
            "hotCount": int(((presence[pi] > 0) & (pool_is_hot > 0)).sum()),
            "otherCount": int(((presence[pi] > 0) & (pool_is_hot == 0)).sum()),
            "hotCutoff": 50.0, "otherCutoff": 50.0, "updateDate": "2026-06-27",
        })

    with open(os.path.join(proc, "charts.json"), "w", encoding="utf-8") as fh:
        json.dump(charts, fh)
    with open(os.path.join(proc, "players.json"), "w", encoding="utf-8") as fh:
        json.dump(players, fh)
    with open(os.path.join(proc, "kasegi.json"), "w", encoding="utf-8") as fh:
        json.dump([{"scope": 0, "hot": [], "other": []}], fh)
    with open(os.path.join(proc, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": VERSION, "instrument": "guitar", "players": P, "charts": C,
                   "supportCharts": C, "minSupport": 1, "svdDim": 4,
                   "kasegiBrackets": [0], "builtAt": "2026-06-27T00:00:00+00:00"}, fh)
    np.savez_compressed(
        os.path.join(proc, "matrix.npz"),
        skill=skill, presence=presence, ach=ach, levels=levels,
        pool_is_hot=pool_is_hot, chart_count=counts, chart_mean=chart_mean,
        chart_std=chart_std, support_mask=support_mask, emb=emb,
    )


@pytest.fixture
def guitar_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    _build_processed_guitar(str(tmp_path))
    import server.userstore as us
    importlib.reload(us)
    import server.overlay as ov_mod
    importlib.reload(ov_mod)
    import server.engine as eng
    importlib.reload(eng)
    return eng.Engine(VERSION, instrument="guitar")


def test_engine_loads_from_instrument_dir(guitar_engine):
    e = guitar_engine
    assert e.instrument == "guitar"
    assert e.P == len(BASE)
    assert e.C == len(CHARTS)


def test_gsv_url_points_to_guitar(guitar_engine):
    e = guitar_engine
    url = e._gsv_url(200)
    assert url.endswith("/g"), url
    assert "/d" not in url.rsplit("/", 1)[1]


def test_guitar_bass_charts_distinct_in_engine(guitar_engine):
    e = guitar_engine
    g = e.chart_detail(0, 0)["chart"]
    b = e.chart_detail(0, 1)["chart"]
    assert g["name"] == b["name"] == "Overlap"
    assert {g["part"], b["part"]} == {"G", "B"}


def test_read_api_shapes_match_drum(guitar_engine):
    e = guitar_engine
    prof = e.profile(0)
    for key in ("player", "rank", "signature", "sheetHot", "sheetOther", "levelHist"):
        assert key in prof
    assert isinstance(e.similar_players(0), list)
    recs = e.song_recs(0)
    for key in ("discovery", "skillUp", "combined"):
        assert key in recs


def test_no_overlay_for_guitar(guitar_engine):
    e = guitar_engine
    assert e.get_overlay(0, as_owner=True) is None


def test_default_instrument_is_drum():
    import server.engine as eng
    # the public factory default must remain drum (back-compat)
    import inspect
    sig = inspect.signature(eng.Engine.__init__)
    assert sig.parameters["instrument"].default == "drum"
