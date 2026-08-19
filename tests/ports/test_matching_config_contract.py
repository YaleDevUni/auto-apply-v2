"""MatchingConfigSource contract test."""

from pathlib import Path

import pytest

from auto_apply.adapters.matching_config.static import StaticMatchingConfigSource
from auto_apply.adapters.matching_config.yaml_file import YamlMatchingConfigSource
from auto_apply.contracts.matching_config import MatchingConfig, TrackRule

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_YAML = """
tracks:
  dev:
    label: 개발
    weight: 100
    keywords: [백엔드, python]
hardcuts:
  CLOSED:
    label: 마감
scoring:
  stack_bonus: 4
  stack_max: 32
  keywords_stack: [react]
location:
  preferred: [서울]
  preferred_bonus: 10
applicability:
  min_fit_score: 75
"""


def _write_sample_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "matching.yaml"
    path.write_text(SAMPLE_YAML, encoding="utf-8")
    return path


@pytest.fixture(params=["static", "yaml"])
def make_source(request: pytest.FixtureRequest, tmp_path):
    if request.param == "static":
        track = TrackRule(label="개발", weight=100, keywords=["백엔드"])
        return StaticMatchingConfigSource(MatchingConfig(tracks={"dev": track}))
    return YamlMatchingConfigSource(_write_sample_yaml(tmp_path))


async def test_load_returns_matching_config(make_source):
    cfg = await make_source.load()
    assert isinstance(cfg, MatchingConfig)
    assert "dev" in cfg.tracks


async def test_yaml_source_parses_nested_rules(tmp_path):
    """yaml 은 static 과 달리 실제 값을 읽는다 — 값 자체가 맞게 파싱되는지 확인."""
    cfg = await YamlMatchingConfigSource(_write_sample_yaml(tmp_path)).load()
    assert cfg.tracks["dev"].keywords == ["백엔드", "python"]
    assert cfg.hardcuts["CLOSED"].label == "마감"
    assert cfg.scoring.stack_max == 32
    assert cfg.location.preferred == ["서울"]
    assert cfg.applicability.min_fit_score == 75


async def test_static_source_returns_empty_config_by_default():
    cfg = await StaticMatchingConfigSource().load()
    assert cfg.tracks == {}
    assert cfg.hardcuts == {}


async def test_repo_matching_config_is_valid():
    """실제 config/matching.yaml(운영에 쓰는 파일) 이 스키마를 통과하는지 확인한다."""
    cfg = await YamlMatchingConfigSource(REPO_ROOT / "config" / "matching.yaml").load()
    assert set(cfg.tracks) == {"dev", "mes_erp", "pm", "presales", "biz"}
    assert cfg.applicability.min_fit_score == 75
    assert "react" in cfg.scoring.keywords_stack
