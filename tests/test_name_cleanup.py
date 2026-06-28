# tests/test_name_cleanup.py
"""Encoding-loss chart-name cleanup.

gsv.fun occasionally returns a song title where a non-ASCII glyph was lost to a
literal ASCII '?' upstream (e.g. 'チョンマゲ航空①便' also appears as 'チョンマゲ航空?便',
and '…キッス♡…' as '…キッス?…'). Because charts are keyed by exact name, the two
spellings split one chart into two. `build_name_canon` merges a '?'-name into a
sibling that is identical except real non-ASCII characters fill the '?' positions,
while leaving titles with a *legitimate* literal '?' (no such sibling) untouched.
"""
import importlib
import json
import os

import numpy as np
import pytest

VERSION = "testver"


# ---------------- unit: the canonicalization rule ----------------
def test_canon_merges_mojibake_and_preserves_literal_question():
    import pipeline.build_dataset as bd
    counts = {
        ("チョンマゲ航空①便", "MAS", "D"): 5,   # canonical (more common)
        ("チョンマゲ航空?便", "MAS", "D"): 2,   # encoding-loss variant
        ("…キッス♡…", "EXT", "D"): 3,
        ("…キッス?…", "EXT", "D"): 4,
        ("誰?", "EXT", "D"): 9,               # legitimate literal '?'
        ("What's up?", "MAS", "D"): 1,        # legitimate literal '?'
    }
    remap = bd.build_name_canon(counts)
    assert remap[("チョンマゲ航空?便", "MAS", "D")] == "チョンマゲ航空①便"
    assert remap[("…キッス?…", "EXT", "D")] == "…キッス♡…"
    # literal-'?' titles have no non-ASCII sibling → never remapped
    assert ("誰?", "EXT", "D") not in remap
    assert ("What's up?", "MAS", "D") not in remap
    # the canonical names themselves are not remapped
    assert ("チョンマゲ航空①便", "MAS", "D") not in remap


def test_canon_corrects_name_even_without_same_difficulty_sibling():
    """Title-level: the clean spelling learned from one difficulty is applied to a
    difficulty/part where only the '?'-spelling has holders (the 'straggler' case).
    The corrected name still keys per-(diff, part), so charts stay distinct."""
    import pipeline.build_dataset as bd
    counts = {
        ("A①B", "MAS", "D"): 5,   # clean spelling only at MAS
        ("A?B", "EXT", "D"): 2,   # mojibake spelling only at EXT — no same-diff sibling
    }
    assert bd.build_name_canon(counts) == {("A?B", "EXT", "D"): "A①B"}


def test_canon_ignores_ascii_fill_and_length_mismatch():
    import pipeline.build_dataset as bd
    counts = {
        ("ABC", "MAS", "D"): 5,    # ASCII char where '?' sits → not a mojibake sibling
        ("AB?", "MAS", "D"): 2,
        ("X①YZ", "MAS", "D"): 5,   # different length → not a sibling
        ("X?Y", "MAS", "D"): 2,
    }
    assert bd.build_name_canon(counts) == {}


# ---------------- integration: build() collapses the split chart ----------------
def _ach(p):
    return f"{p * 100:.2f}%"


def _chart(name, diff, level, ach):
    return {
        "name": name, "part": "D", "diff": diff,
        "skill_value": round(level * 20.0 * ach, 2),
        "achive_value": _ach(ach), "diff_value": level,
    }


def _write_raw_drum(data_base):
    """6 players. The MAS chart 'チョンマゲ航空①便' is split: 3 players hold the clean
    spelling, 3 hold the '?'-mojibake spelling — all at level 9.25. A separate
    legit '?' title ('誰?') must survive unmerged."""
    raw_dir = os.path.join(data_base, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    CLEAN, MOJI, LEGIT = "チョンマゲ航空①便", "チョンマゲ航空?便", "誰?"
    sheets = [
        (5000.0, [_chart(CLEAN, "MAS", 9.25, 0.98), _chart(LEGIT, "EXT", 7.0, 0.95)], []),
        (4800.0, [_chart(CLEAN, "MAS", 9.25, 0.95), _chart(LEGIT, "EXT", 7.0, 0.90)], []),
        (4600.0, [_chart(CLEAN, "MAS", 9.25, 0.92)], [_chart(LEGIT, "EXT", 7.0, 0.88)]),
        (4400.0, [_chart(MOJI, "MAS", 9.25, 0.90), _chart(LEGIT, "EXT", 7.0, 0.80)], []),
        (4200.0, [_chart(MOJI, "MAS", 9.25, 0.88)], [_chart(LEGIT, "EXT", 7.0, 0.70)]),
        (4000.0, [_chart(MOJI, "MAS", 9.25, 0.85), _chart(LEGIT, "EXT", 7.0, 0.60)], []),
    ]
    players = []
    for i, (sp, hot, other) in enumerate(sheets):
        players.append({
            "playerId": 200 + i, "playerName": f"DP{i}",
            "drumSkillPoint": sp, "updateDate": "2026-06-27",
            "hot": {"point": sum(c["skill_value"] for c in hot), "data": hot},
            "other": {"point": sum(c["skill_value"] for c in other), "data": other},
        })
    path = os.path.join(raw_dir, f"players_drum_{VERSION}.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for p in players:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(os.path.join(raw_dir, f"kasegi_drum_{VERSION}.json"), "w", encoding="utf-8") as fh:
        json.dump([], fh)


@pytest.fixture
def built_drum(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    _write_raw_drum(str(tmp_path))
    import pipeline.build_dataset as bd
    importlib.reload(bd)
    bd.build(version=VERSION, instrument="drum", min_support=1, svd_dim=2)
    proc = os.path.join(str(tmp_path), "processed", VERSION, "drum")
    with open(os.path.join(proc, "charts.json"), encoding="utf-8") as fh:
        charts = json.load(fh)
    return proc, charts


def test_mojibake_variant_is_merged_into_one_chart(built_drum):
    _, charts = built_drum
    chonmage = [c for c in charts if "チョンマゲ航空" in c["name"] and c["diff"] == "MAS"]
    assert len(chonmage) == 1, f"expected a single merged chart, got {[c['name'] for c in chonmage]}"
    c = chonmage[0]
    assert c["name"] == "チョンマゲ航空①便"          # canonical spelling wins
    assert "?" not in c["name"]
    assert c["count"] == 6                           # all 6 holders folded together


def test_no_question_mark_chart_remains(built_drum):
    _, charts = built_drum
    assert not any(c["name"] == "チョンマゲ航空?便" for c in charts)


def test_legit_question_mark_title_preserved(built_drum):
    _, charts = built_drum
    assert any(c["name"] == "誰?" for c in charts), "legitimate '?' title was wrongly altered"


def test_matrix_columns_match_merged_chart_count(built_drum):
    proc, charts = built_drum
    npz = np.load(os.path.join(proc, "matrix.npz"))
    assert npz["skill"].shape[1] == len(charts)
