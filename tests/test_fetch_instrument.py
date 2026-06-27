# tests/test_fetch_instrument.py
"""Instrument parameterization of the fetch layer (pure helpers only — no network).

Locks the per-instrument GraphQL shape and raw-record fields so a future edit
can't silently point GuitarFreaks at the DrumMania type/field.
"""
import importlib

import pytest

fd = importlib.import_module("pipeline.fetch_data")


def test_user_skill_query_drum_uses_type_d_and_drumskill():
    q = fd.user_skill_query(42, "galaxywave_delta", "drum")
    assert "type:d" in q.replace(" ", "")
    assert "drumSkill" in q
    assert "drumSkillPoint" in q


def test_user_skill_query_guitar_uses_type_g_and_guitarskill():
    q = fd.user_skill_query(42, "galaxywave_delta", "guitar")
    assert "type:g" in q.replace(" ", "")
    assert "guitarSkill" in q
    assert "guitarSkillPoint" in q


def test_make_record_drum_carries_drum_skillpoint():
    full = {
        "playerName": "DPlayer", "drumSkillPoint": 5000.0, "updateDate": "2026-06-27",
        "drumSkill": {"hot": {"point": 1.0, "data": [{"name": "S"}]},
                      "other": {"point": 2.0, "data": []}},
    }
    u = {"playerId": 7, "playerName": "DPlayer", "updateDate": "2026-06-26"}
    rec = fd.make_record(full, u, "drum")
    assert rec["playerId"] == 7
    assert rec["drumSkillPoint"] == 5000.0
    assert "guitarSkillPoint" not in rec
    assert rec["hot"]["data"] == [{"name": "S"}]
    assert rec["other"]["point"] == 2.0


def test_make_record_guitar_carries_guitar_skillpoint():
    full = {
        "playerName": "GPlayer", "guitarSkillPoint": 6000.0, "updateDate": "2026-06-27",
        "guitarSkill": {"hot": {"point": 3.0, "data": [{"name": "S", "part": "G"}]},
                        "other": {"point": 0.0, "data": [{"name": "S", "part": "B"}]}},
    }
    u = {"playerId": 9, "playerName": "GPlayer", "updateDate": "2026-06-26"}
    rec = fd.make_record(full, u, "guitar")
    assert rec["playerId"] == 9
    assert rec["guitarSkillPoint"] == 6000.0
    assert "drumSkillPoint" not in rec
    assert rec["hot"]["data"][0]["part"] == "G"
    assert rec["other"]["data"][0]["part"] == "B"


def test_raw_filenames_are_instrument_scoped():
    assert fd.players_filename("v", "guitar").endswith("players_guitar_v.jsonl")
    assert fd.kasegi_filename("v", "drum").endswith("kasegi_drum_v.json")
