"""PortfolioSource contract test."""

from pathlib import Path

import pytest

from auto_apply.adapters.portfolio.static import StaticPortfolioSource
from auto_apply.adapters.portfolio.yaml_source import YamlPortfolioSource
from auto_apply.contracts.portfolio import PortfolioMap

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_YAML = """
- user_id: u1
  categories:
    개발자: "박예일_포트폴리오_풀스택.pdf"
    AX개발자: "박예일_포트폴리오_AX개발자.pdf"
"""


def _write_sample_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "portfolio_map.yaml"
    path.write_text(SAMPLE_YAML, encoding="utf-8")
    return path


@pytest.fixture(params=["static", "yaml"])
def make_source(request: pytest.FixtureRequest, tmp_path):
    if request.param == "static":
        m = PortfolioMap(
            user_id="u1",
            categories={
                "개발자": "박예일_포트폴리오_풀스택.pdf",
                "AX개발자": "박예일_포트폴리오_AX개발자.pdf",
            },
        )
        return StaticPortfolioSource([m])
    return YamlPortfolioSource(_write_sample_yaml(tmp_path))


async def test_get_returns_that_users_map(make_source):
    m = await make_source.get("u1")
    assert m.categories["개발자"] == "박예일_포트폴리오_풀스택.pdf"


async def test_get_returns_empty_map_for_unknown_user(make_source):
    """ProfileSource 와 다르게 예외를 던지지 않는다 — 매칭되는 게 없으면 그냥 포트폴리오를

    안 붙이는 쪽으로 안전하게 낮춰 잡는다(ports/portfolio.py)."""
    m = await make_source.get("nobody")
    assert m.categories == {}


async def test_yaml_source_returns_empty_map_when_file_missing(tmp_path: Path):
    source = YamlPortfolioSource(tmp_path / "does-not-exist.yaml")
    m = await source.get("u1")
    assert m.categories == {}


async def test_repo_portfolio_map_example_yaml_is_valid():
    """config/portfolio_map.example.yaml(placeholder 템플릿, git 추적)이 스키마를 통과하는지."""
    path = REPO_ROOT / "config" / "portfolio_map.example.yaml"
    m = await YamlPortfolioSource(path).get("default")
    assert m.categories
