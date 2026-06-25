# tests/test_engine_overlay.py
"""Engine overlay integration (spec §6, plan Task 5).

Builds a tiny synthetic processed dir + a dense official upload overlay for
player 0, then asserts:
  * profile(i, overlay) is `enhanced` and carries `overlayStats`;
  * song_recs(i, overlay) exposes the new three-class shape with a populated
    `practiceTargets` list (the new value of dense data);
  * similar_players(i, overlay) does NOT rank-drop the dense player to zero
    similarity purely because of a huge overlay union (asymmetric taste);
  * the no-overlay path keeps the exact existing behaviour/shape;
  * a PRIVATE overlay is hidden from a non-owner caller.
"""
import importlib
import json
import os

import numpy as np
import pytest

VERSION = "galaxywave_delta"

# name, diff, level, pool
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

# player index -> {chart index: achievement}  (sparse gsv top-N base sheets)
BASE = {
    0: {0: 0.95, 1: 0.92},
    1: {0: 0.88, 2: 0.80},
    2: {1: 0.90, 3: 0.70, 4: 0.85},
    3: {2: 0.75, 4: 0.82, 6: 0.78},
    4: {5: 0.90, 7: 0.72},
}
SP = [2000.0, 1900.0, 2100.0, 1800.0, 1950.0]


def _build_processed(root):
    proc = os.path.join(root, "processed", VERSION)
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
        json.dump(charts, fh)
    with open(os.path.join(proc, "players.json"), "w", encoding="utf-8") as fh:
        json.dump(players, fh)
    with open(os.path.join(proc, "kasegi.json"), "w", encoding="utf-8") as fh:
        json.dump([{"scope": 0, "hot": [], "other": []}], fh)
    with open(os.path.join(proc, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": VERSION, "players": P, "charts": C,
                   "supportCharts": C, "minSupport": 1, "svdDim": 4,
                   "kasegiBrackets": [0], "builtAt": "2026-06-26T00:00:00+00:00"}, fh)
    np.savez_compressed(
        os.path.join(proc, "matrix.npz"),
        skill=skill, presence=presence, ach=ach, levels=levels,
        pool_is_hot=pool_is_hot, chart_count=counts, chart_mean=chart_mean,
        chart_std=chart_std, support_mask=support_mask, emb=emb,
    )


def _overlay_latest():
    """Dense official upload for player 0 (gsvPlayerId 100): exact + rank-only,
    including charts that sit BELOW the player's pool cutoff (practice targets)."""
    return {
        "schema": 1, "version": VERSION, "gsvPlayerId": 100,
        "visibility": "private", "linkedDbId": 0, "token": "tok",
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


@pytest.fixture
def tmp_engine_with_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    _build_processed(str(tmp_path))
    import server.userstore as us
    importlib.reload(us)
    import server.overlay as ov_mod
    importlib.reload(ov_mod)
    import server.engine as eng
    importlib.reload(eng)
    us.set_latest(VERSION, 100, _overlay_latest())
    e = eng.Engine(VERSION)
    ov = e.get_overlay(0, as_owner=True)
    assert ov is not None
    return e, 0, ov


def test_profile_enhanced_and_practice_targets(tmp_engine_with_overlay):
    e, i, ov = tmp_engine_with_overlay
    prof = e.profile(i, overlay=ov)
    assert prof["enhanced"] is True
    assert "overlayStats" in prof
    assert prof["overlayStats"]["played"] >= 5
    recs = e.song_recs(i, overlay=ov)
    assert "practiceTargets" in recs and isinstance(recs["practiceTargets"], list)
    assert len(recs["practiceTargets"]) >= 1
    # a practice target advertises the achievement needed to clear the cutoff
    pt = recs["practiceTargets"][0]
    assert 0.0 < pt["neededAch"] <= 1.0
    assert pt["neededAch"] > pt["currentAch"]


def test_base_profile_and_songs_unchanged_without_overlay(tmp_engine_with_overlay):
    e, i, _ = tmp_engine_with_overlay
    prof = e.profile(i)
    assert prof.get("enhanced") is not True
    assert "overlayStats" not in prof
    recs = e.song_recs(i)
    # existing (base) shape: discovery / skillUp / combined, no practiceTargets
    assert "combined" in recs
    assert "practiceTargets" not in recs


def test_similar_not_zeroed_by_dense_overlay(tmp_engine_with_overlay):
    e, i, ov = tmp_engine_with_overlay
    res = e.similar_players(i, overlay=ov)
    assert isinstance(res, list) and len(res) >= 1
    # asymmetric taste: a dense uploader still scores positive against a sparse
    # peer whose charts it has covered (not punished by the huge overlay union).
    assert any(r["composite"] > 0 for r in res)


def test_private_overlay_hidden_from_non_owner(tmp_engine_with_overlay):
    e, i, _ = tmp_engine_with_overlay
    assert e.get_overlay(i, as_owner=False) is None
