"""GuideSource contract test — file/static 두 대역이 같은 계약을 지키는지."""

from pathlib import Path

import pytest

from auto_apply.adapters.guide.file import FileGuideSource
from auto_apply.adapters.guide.static import StaticGuideSource


@pytest.fixture(params=["static", "file"])
def make_source(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "static":
        return StaticGuideSource("초기 가이드")
    path = tmp_path / "resume_guide.md"
    path.write_text("초기 가이드", encoding="utf-8")
    return FileGuideSource(path)


async def test_get_returns_current_text(make_source):
    assert await make_source.get() == "초기 가이드"


async def test_save_then_get_roundtrips(make_source):
    await make_source.save("바뀐 가이드")
    assert await make_source.get() == "바뀐 가이드"


async def test_missing_file_reads_as_empty_string(tmp_path: Path):
    source = FileGuideSource(tmp_path / "does-not-exist.md")
    assert await source.get() == ""


async def test_save_creates_missing_parent_directories(tmp_path: Path):
    source = FileGuideSource(tmp_path / "nested" / "resume_guide.md")
    await source.save("새 가이드")
    assert await source.get() == "새 가이드"
