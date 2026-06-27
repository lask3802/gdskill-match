# tests/test_userstore.py
import json, os, importlib

def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    import server.userstore as us
    return importlib.reload(us)

def test_latest_roundtrip(tmp_path, monkeypatch):
    us = _store(tmp_path, monkeypatch)
    assert us.get_latest("galaxywave_delta", 42) is None
    us.set_latest("galaxywave_delta", 42, {"gsvPlayerId": 42, "charts": []})
    got = us.get_latest("galaxywave_delta", 42)
    assert got["gsvPlayerId"] == 42
    assert 42 in us.list_latest("galaxywave_delta")

def test_raw_is_immutable_separate_file(tmp_path, monkeypatch):
    us = _store(tmp_path, monkeypatch)
    us.save_upload("galaxywave_delta", 42, "u1", {"a": 1})
    us.save_upload("galaxywave_delta", 42, "u2", {"a": 2})
    base = os.path.join(str(tmp_path), "userdata", "raw", "v1", "galaxywave_delta", "42")
    assert set(os.listdir(base)) == {"u1.json", "u2.json"}
