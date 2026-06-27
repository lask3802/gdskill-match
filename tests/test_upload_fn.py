# tests/test_upload_fn.py
"""Smoke tests for the upload Cloud Function entrypoint (plan Task 7).

`upload_fn/main.py` is a thin CORS + method-routing adapter around
`server.ingest`; the real validation / identity-linking / storage logic is
covered by tests/test_ingest.py. Here we only exercise the Functions-Framework
HTTP surface — without a real Flask / functions-framework runtime — by calling
`main.upload(request)` with a fake Flask-style request object and asserting:

  * a POST with a valid payload that links returns 200 (linked);
  * a POST with a valid payload that cannot be linked returns 202 (quarantined);
  * an OPTIONS request returns the eagate CORS preflight header;
  * a non-POST/OPTIONS method is rejected (405);
  * an oversize Content-Length is rejected (413) before ingest runs.

The function delegates storage to `server.userstore`, so every test runs against
a tmp `GD_DATA_DIR` (local FS, no GCS) to avoid touching the repo's data dir.
"""
import importlib
import importlib.util
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "galaxywave_delta"


class FakeRequest:
    """Minimal Flask-request stand-in: `.method`, `.headers`, `.get_json()`."""

    def __init__(self, method="POST", json_body=None, headers=None):
        self.method = method
        self._json = json_body
        self.headers = headers or {}

    def get_json(self, *args, **kwargs):  # mirrors flask.Request.get_json(silent=...)
        return self._json


def _base_payload():
    return {"schema": 1, "version": VERSION, "game": "gitadora", "gtype": "dm",
            "gsvPlayerId": 1, "scrapedAt": "2026-06-26T00:00:00Z", "mode": "standard",
            "profile": {"playerName": "LASK", "drumSkillPoint": 7530.40},
            "charts": [{"sid": "2", "name": "X", "diff": "MAS", "rank": "B",
                        "achievement": 0.6515, "exact": True, "level": 8.45,
                        "playCount": 1, "clearCount": 1}]}


def _write_players(tmp_path, players):
    proc = os.path.join(str(tmp_path), "processed", VERSION, "drum")
    os.makedirs(proc, exist_ok=True)
    with open(os.path.join(proc, "players.json"), "w", encoding="utf-8") as fh:
        json.dump(players, fh)


def _load_main(tmp_path, monkeypatch):
    """Reload the store + ingest under a tmp GD_DATA_DIR, then load the function
    module fresh from its file path (it is not an importable package)."""
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    for name in ("server.userstore", "server.ingest"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    main_path = os.path.join(REPO_ROOT, "upload_fn", "main.py")
    spec = importlib.util.spec_from_file_location("upload_fn_main", main_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_post_valid_links_returns_200(tmp_path, monkeypatch):
    _write_players(tmp_path, [{"id": 0, "playerId": 1, "name": "LASK", "sp": 7530.40}])
    main = _load_main(tmp_path, monkeypatch)
    result = main.upload(FakeRequest(method="POST", json_body=_base_payload()))
    status = result[1]
    body = json.loads(result[0])
    assert status == 200
    assert body["status"] == "linked"
    assert body["token"]


def test_post_unlinkable_quarantines_202(tmp_path, monkeypatch):
    # no players.json -> identity cannot be linked -> quarantine
    main = _load_main(tmp_path, monkeypatch)
    result = main.upload(FakeRequest(method="POST", json_body=_base_payload()))
    status = result[1]
    body = json.loads(result[0])
    assert status == 202
    assert body["status"] == "quarantined"


def test_post_validation_error_returns_400(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    p = _base_payload()
    p["charts"][0]["rank"] = "Z"
    result = main.upload(FakeRequest(method="POST", json_body=p))
    assert result[1] == 400
    assert json.loads(result[0])["errors"]


def test_options_returns_cors_preflight(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    result = main.upload(FakeRequest(
        method="OPTIONS", headers={"Origin": "https://p.eagate.573.jp"}))
    status, headers = result[1], result[2]
    assert status in (200, 204)
    assert headers.get("Access-Control-Allow-Origin") == "https://p.eagate.573.jp"
    assert headers.get("Vary") == "Origin"


def test_non_post_method_is_405(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    result = main.upload(FakeRequest(method="GET"))
    assert result[1] == 405


def test_oversize_content_length_is_413(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    big = str(main.ingest.MAX_BYTES + 1)
    result = main.upload(FakeRequest(
        method="POST", json_body=_base_payload(),
        headers={"Content-Length": big}))
    assert result[1] == 413
