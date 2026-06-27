# tests/test_build_guitar.py
"""GuitarFreaks dataset build (instrument parameterization).

Drives `pipeline.build_dataset.build(version, instrument="guitar", ...)` from a
synthetic raw guitar JSONL and asserts the GuitarFreaks-specific invariant that
DrumMania never had: a single song's **guitar (G)** and **bass (B)** charts at
the same difficulty must stay DISTINCT entries — GF skill spans both parts, so
collapsing them on (name, diff) would corrupt the matrix. Also asserts the
artifacts land under processed/<version>/<instrument>/ and carry a `part` field.
"""
import importlib
import json
import os

import numpy as np
import pytest

VERSION = "testver"


def _ach(p):
    return f"{p * 100:.2f}%"


def _chart(name, part, diff, level, ach):
    return {
        "name": name, "part": part, "diff": diff,
        "skill_value": round(level * 20.0 * ach, 2),
        "achive_value": _ach(ach), "diff_value": level,
    }


def _write_raw_guitar(data_base):
    """A small guitar field: 5 players. 'Overlap' appears as both a G chart and a
    B chart at MAS — these must not be merged."""
    raw_dir = os.path.join(data_base, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    players = []
    # each player's hot/other sheets reference a mix of G and B charts
    sheets = [
        # (skillpoint, [hot charts], [other charts])
        (3200.0,
         [_chart("Overlap", "G", "MAS", 8.0, 0.97), _chart("Overlap", "B", "MAS", 8.0, 0.90),
          _chart("Solo", "G", "EXT", 7.0, 0.95)],
         [_chart("Groove", "B", "ADV", 5.0, 0.99)]),
        (3000.0,
         [_chart("Overlap", "G", "MAS", 8.0, 0.93), _chart("Solo", "G", "EXT", 7.0, 0.88)],
         [_chart("Groove", "B", "ADV", 5.0, 0.96)]),
        (2800.0,
         [_chart("Overlap", "B", "MAS", 8.0, 0.85), _chart("Solo", "G", "EXT", 7.0, 0.80)],
         [_chart("Riff", "G", "MAS", 8.5, 0.70)]),
        (2600.0,
         [_chart("Overlap", "G", "MAS", 8.0, 0.78), _chart("Riff", "G", "MAS", 8.5, 0.82)],
         [_chart("Groove", "B", "ADV", 5.0, 0.91)]),
        (2400.0,
         [_chart("Solo", "G", "EXT", 7.0, 0.72), _chart("Overlap", "B", "MAS", 8.0, 0.65)],
         [_chart("Riff", "G", "MAS", 8.5, 0.60)]),
    ]
    for i, (sp, hot, other) in enumerate(sheets):
        players.append({
            "playerId": 100 + i, "playerName": f"GP{i}",
            "guitarSkillPoint": sp, "updateDate": "2026-06-27",
            "hot": {"point": sum(c["skill_value"] for c in hot), "data": hot},
            "other": {"point": sum(c["skill_value"] for c in other), "data": other},
        })
    path = os.path.join(raw_dir, f"players_guitar_{VERSION}.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for p in players:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    # empty kasegi is acceptable
    with open(os.path.join(raw_dir, f"kasegi_guitar_{VERSION}.json"), "w", encoding="utf-8") as fh:
        json.dump([], fh)


@pytest.fixture
def built_guitar(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    _write_raw_guitar(str(tmp_path))
    import pipeline.build_dataset as bd
    importlib.reload(bd)
    bd.build(version=VERSION, instrument="guitar", min_support=1, svd_dim=2)
    proc = os.path.join(str(tmp_path), "processed", VERSION, "guitar")
    with open(os.path.join(proc, "charts.json"), encoding="utf-8") as fh:
        charts = json.load(fh)
    return str(tmp_path), proc, charts


def test_guitar_artifacts_written_under_instrument_dir(built_guitar):
    _, proc, _ = built_guitar
    for f in ("charts.json", "players.json", "kasegi.json", "meta.json", "matrix.npz"):
        assert os.path.isfile(os.path.join(proc, f)), f"missing {f}"


def test_guitar_and_bass_charts_are_distinct(built_guitar):
    _, _, charts = built_guitar
    overlap = [c for c in charts if c["name"] == "Overlap" and c["diff"] == "MAS"]
    parts = sorted(c["part"] for c in overlap)
    assert parts == ["B", "G"], f"expected distinct G and B Overlap/MAS charts, got {parts}"


def test_every_chart_carries_a_part(built_guitar):
    _, _, charts = built_guitar
    assert all("part" in c and c["part"] for c in charts)


def test_matrix_matches_chart_count(built_guitar):
    _, proc, charts = built_guitar
    npz = np.load(os.path.join(proc, "matrix.npz"))
    assert npz["skill"].shape[1] == len(charts)
