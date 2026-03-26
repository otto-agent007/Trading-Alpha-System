"""Shared pytest fixtures for the Trading Alpha System test suite."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure project root is on sys.path so tests can import project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect DATA_PATH and MEMORY_PATH to a temporary directory for test isolation."""
    import config
    monkeypatch.setattr(config, "DATA_PATH", tmp_path / "data")
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "memory")
    (tmp_path / "data").mkdir()
    (tmp_path / "memory").mkdir()
    return tmp_path
