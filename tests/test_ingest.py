# tests/test_ingest.py
import importlib
import json
import os

from server import ingest


def base_payload():
    return {"schema": 1, "version": "galaxywave_delta", "game": "gitadora", "gtype": "dm",
            "gsvPlayerId": 1, "scrapedAt": "2026-06-26T00:00:00Z", "mode": "standard",
            "profile": {"playerName": "LASK", "drumSkillPoint": 7530.40},
            "charts": [{"sid": "2", "name": "X", "diff": "MAS", "rank": "B", "achievement": 0.6515,
                        "exact": True, "level": 8.45, "playCount": 1, "clearCount": 1}]}


# --- validate ---------------------------------------------------------------

def test_validate_ok():
    assert ingest.validate(base_payload()) == []


def test_validate_rejects_bad_rank_and_range():
    p = base_payload()
    p["charts"][0]["rank"] = "Z"
    p["charts"][0]["achievement"] = 2
    errs = ingest.validate(p)
    assert errs


def test_validate_rejects_sensitive_keys():
    p = base_payload()
    p["cardNumber"] = "HX..."
    assert any("card" in e.lower() for e in ingest.validate(p))


def test_validate_rejects_nested_sensitive_keys():
    p = base_payload()
    p["charts"][0]["cookie"] = "session=abc"
    assert any("cookie" in e.lower() for e in ingest.validate(p))


def test_validate_rejects_cardlike_value_under_benign_key():
    # a card number hidden under an innocuous key must still be rejected
    p = base_payload()
    p["profile"]["note"] = "HX2AKJCDDZA6YZ1M"   # 16-char uppercase alnum = card-shaped
    assert any("card" in e.lower() for e in ingest.validate(p))


def test_validate_allows_gitadora_id_and_normal_values():
    # the 10-char ギタドラ ID and ordinary fields must NOT be flagged as card-like
    p = base_payload()
    p["profile"]["gitadoraId"] = "HG12B7108F"
    p["profile"]["allSongSkill"] = 24020.46
    assert ingest.validate(p) == []


def test_validate_size_and_count_limits():
    p = base_payload()
    p["charts"] = p["charts"] * 6000
    assert any("5000" in e or "rows" in e.lower() for e in ingest.validate(p))


def test_validate_rejects_bad_schema():
    p = base_payload()
    p["schema"] = 2
    assert ingest.validate(p)


def test_validate_rank_only_null_achievement_ok():
    p = base_payload()
    p["charts"][0]["exact"] = False
    p["charts"][0]["achievement"] = None
    assert ingest.validate(p) == []


def test_validate_rejects_bad_level():
    p = base_payload()
    p["charts"][0]["level"] = 99
    assert ingest.validate(p)


def test_validate_rejects_bad_diff():
    p = base_payload()
    p["charts"][0]["diff"] = "ULTRA"
    assert ingest.validate(p)


# --- identity-link + store --------------------------------------------------

def _reload(tmp_path, monkeypatch, players=None):
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    if players is None:
        players = [
            {"id": 0, "playerId": 1, "name": "LASK", "sp": 7530.40},
            {"id": 1, "playerId": 2, "name": "Other", "sp": 1000.0},
        ]
    proc = os.path.join(str(tmp_path), "processed", "galaxywave_delta", "drum")
    os.makedirs(proc, exist_ok=True)
    with open(os.path.join(proc, "players.json"), "w", encoding="utf-8") as fh:
        json.dump(players, fh)
    import server.userstore as us
    importlib.reload(us)
    import server.ingest as ing
    importlib.reload(ing)
    return ing, us


def test_link_identity_matches_name_and_skill(tmp_path, monkeypatch):
    ing, _ = _reload(tmp_path, monkeypatch)
    idx, conf = ing.link_identity(base_payload(), "galaxywave_delta")
    assert idx == 0
    assert 0.0 < conf <= 1.0


def test_link_identity_no_name_match_returns_none(tmp_path, monkeypatch):
    ing, _ = _reload(tmp_path, monkeypatch)
    p = base_payload()
    p["profile"]["playerName"] = "NoSuchPlayer"
    idx, conf = ing.link_identity(p, "galaxywave_delta")
    assert idx is None


def test_link_identity_name_match_but_skill_far_off_fails(tmp_path, monkeypatch):
    ing, _ = _reload(tmp_path, monkeypatch)
    p = base_payload()
    p["profile"]["drumSkillPoint"] = 100.0  # far from 7530.40
    idx, _conf = ing.link_identity(p, "galaxywave_delta")
    assert idx is None


def test_ingest_match_writes_latest(tmp_path, monkeypatch):
    ing, us = _reload(tmp_path, monkeypatch)
    status, body = ing.ingest(base_payload(), "galaxywave_delta")
    assert status == 200
    assert body["status"] == "linked"
    assert body["linkedDbId"] == 0
    assert body.get("token")
    latest = us.get_latest("galaxywave_delta", 1)
    assert latest is not None
    assert latest["visibility"] == "private"
    assert latest["gsvPlayerId"] == 1
    assert latest["linkedDbId"] == 0
    assert len(latest["charts"]) == 1


def test_ingest_quarantines_on_no_match(tmp_path, monkeypatch):
    ing, us = _reload(tmp_path, monkeypatch)
    p = base_payload()
    p["profile"]["playerName"] = "Ghost"
    status, body = ing.ingest(p, "galaxywave_delta")
    assert status == 202
    assert body["status"] == "quarantined"
    # never overwrites / creates a latest overlay on a failed link
    assert us.get_latest("galaxywave_delta", 1) is None


def test_ingest_validation_error_returns_400(tmp_path, monkeypatch):
    ing, _ = _reload(tmp_path, monkeypatch)
    p = base_payload()
    p["charts"][0]["rank"] = "Z"
    status, body = ing.ingest(p, "galaxywave_delta")
    assert status == 400
    assert body["errors"]


def test_ingest_always_writes_immutable_raw(tmp_path, monkeypatch):
    ing, _ = _reload(tmp_path, monkeypatch)
    ing.ingest(base_payload(), "galaxywave_delta")
    raw_dir = os.path.join(str(tmp_path), "userdata", "raw", "v1", "galaxywave_delta", "1")
    assert os.path.isdir(raw_dir)
    assert len(os.listdir(raw_dir)) == 1


def test_upload_id_is_deterministic(tmp_path, monkeypatch):
    ing, _ = _reload(tmp_path, monkeypatch)
    a = ing._upload_id(1, "2026-06-26T00:00:00Z")
    b = ing._upload_id(1, "2026-06-26T00:00:00Z")
    c = ing._upload_id(1, "2026-06-26T00:00:01Z")
    assert a == b
    assert a != c
