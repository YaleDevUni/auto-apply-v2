"""GuideSource contract test — file/static 두 대역이 같은 계약을 지키는지."""

from pathlib import Path

import pytest

from auto_apply.adapters.guide.file import FileGuideSource
from auto_apply.adapters.guide.static import StaticGuideSource


@pytest.fixture(params=["static", "file"])
def make_source(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "static":
        return StaticGuideSource("초기 가이드")
    (tmp_path / "resume_guide.wanted.md").write_text("초기 가이드", encoding="utf-8")
    return FileGuideSource(tmp_path)


async def test_get_returns_current_text(make_source):
    assert await make_source.get("wanted") == "초기 가이드"


async def test_save_then_get_roundtrips(make_source):
    await make_source.save("wanted", "바뀐 가이드")
    assert await make_source.get("wanted") == "바뀐 가이드"


async def test_platforms_are_independent(make_source):
    await make_source.save("wanted", "원티드 전용 가이드")
    assert await make_source.get("saramin") != "원티드 전용 가이드"


async def test_missing_file_reads_as_empty_string(tmp_path: Path):
    source = FileGuideSource(tmp_path)
    assert await source.get("wanted") == ""


async def test_save_creates_missing_parent_directories(tmp_path: Path):
    source = FileGuideSource(tmp_path / "nested")
    await source.save("wanted", "새 가이드")
    assert await source.get("wanted") == "새 가이드"
