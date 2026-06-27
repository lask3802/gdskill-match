"""Pytest bootstrap: ensure the repo root is on sys.path so `from server import X`
resolves when tests run from any working directory. Mirrors `pytest.ini`'s
`pythonpath = .` for environments/tools that bypass the ini setting."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
