# tests/test_updater_sync.py
"""The scheduled Cloud Function ships its own copies of the pipeline scripts
(updater/ deploys via buildpacks with no repo root). They MUST stay byte-identical
to pipeline/ so the daily rebuild matches local builds."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), "rb") as fh:
        return fh.read()


def test_fetch_data_copies_identical():
    assert _read("pipeline", "fetch_data.py") == _read("updater", "fetch_data.py")


def test_build_dataset_copies_identical():
    assert _read("pipeline", "build_dataset.py") == _read("updater", "build_dataset.py")
