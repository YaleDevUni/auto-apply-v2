"""상태 쓰기 봉인 (절대 규칙 6, §A3) — 지원 건 저장소는 `ApplicationService` 만 만진다.

import-linter 는 메서드 호출을 못 본다. 그래서 src 전체 AST 를 훑어 `ApplicationService` 모듈과
저장소 구현 밖에서 `.applications`(UoW 의 지원 건 저장소)·`.append_state` 에 닿는 코드를 거부한다.
별칭(`repo = uow.applications`)도 속성 접근 시점에 잡힌다. 읽기도 막는다 — 읽기 통로가 서비스
밖에 열려 있으면 쓰기도 곧 따라 나간다.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "auto_apply"
_ALLOWED = {
    SRC / "services" / "application.py",
    SRC / "adapters" / "repository" / "memory.py",
    SRC / "adapters" / "repository" / "sqlite.py",
}
_SEALED_ATTRS = {"applications", "append_state"}


def _violations(source: str) -> list[str]:
    return [
        f"{node.lineno}: .{node.attr}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr in _SEALED_ATTRS
    ]


def test_no_state_write_outside_application_service():
    found = {
        str(path.relative_to(SRC)): v
        for path in SRC.rglob("*.py")
        if path not in _ALLOWED and (v := _violations(path.read_text(encoding="utf-8")))
    }
    assert found == {}, f"지원 건 상태는 ApplicationService.transition() 으로만 쓴다: {found}"


def test_allowed_modules_exist():
    """허용 목록이 옮겨진 파일을 가리키면 봉인이 헛돈다."""
    assert all(p.is_file() for p in _ALLOWED)


@pytest.mark.parametrize(
    "planted",
    [
        "async def f(uow, s):\n    await uow.applications.append_state(s, expected=s.state)\n",
        "async def f(uow, r, s):\n    await uow.applications.add(r, s)\n",
        "def f(uow):\n    repo = uow.applications\n    return repo\n",
        "async def f(repo, s):\n    await repo.append_state(s, expected=s.state)\n",
    ],
)
def test_planted_write_is_caught(planted):
    """위반을 심으면 실제로 잡는다 — 검사가 조용히 무력화되지 않게."""
    assert _violations(planted)


def test_service_module_really_writes_through_repository():
    """허용된 서비스가 실제로 그 통로를 쓴다 — 속성 이름이 바뀌면 이 봉인도 같이 고쳐야 한다."""
    assert _violations((SRC / "services" / "application.py").read_text(encoding="utf-8"))
