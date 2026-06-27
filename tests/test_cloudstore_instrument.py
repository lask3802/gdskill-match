# tests/test_cloudstore_instrument.py
"""Cloud artifact sync is instrument-scoped (pure helpers only — no GCS)."""
import importlib
import os

cs = importlib.import_module("server.cloudstore")


def test_configured_instruments_defaults_to_drum(monkeypatch):
    monkeypatch.delenv("GD_INSTRUMENTS", raising=False)
    assert cs.configured_instruments() == ["drum"]


def test_configured_instruments_parses_env(monkeypatch):
    monkeypatch.setenv("GD_INSTRUMENTS", " drum , guitar ")
    assert cs.configured_instruments() == ["drum", "guitar"]


def test_configured_instruments_empty_falls_back(monkeypatch):
    monkeypatch.setenv("GD_INSTRUMENTS", "")
    assert cs.configured_instruments() == ["drum"]


def test_blob_name_is_instrument_scoped():
    assert cs._blob_name("galaxywave_delta", "guitar", "charts.json") == \
        "processed/galaxywave_delta/guitar/charts.json"


def test_local_dir_nests_instrument():
    d = cs._local_dir("galaxywave_delta", "guitar")
    assert d.replace("\\", "/").endswith("processed/galaxywave_delta/guitar")
