"""가짜 CLI 가 받은 것을 적어 둘 디렉터리."""

from pathlib import Path

import pytest


@pytest.fixture
def out(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "fake-out"
    d.mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(d))
    return d
