"""composition root 테스트 — 설정만 바꿔 구현이 교체되는지 확인 (§A2)."""

from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.bootstrap import build_container
from auto_apply.config import Settings


def test_offline_profile_builds():
    c = build_container(Settings(storage="memory", llm_provider="stub"))
    assert isinstance(c.store, InMemoryBlobStore)


def test_storage_swap_by_config(tmp_path):
    c = build_container(Settings(storage="local", data_dir=tmp_path))
    assert isinstance(c.store, LocalBlobStore)


def test_resume_llm_keeps_slash_commands_locked():
    """외부 공고 텍스트가 프롬프트에 들어가는 인스턴스는 슬래시커맨드 표면을 열지 않는다

    (adapters/llm/claude_code_cli.py "turn()" 절)."""
    c = build_container(Settings(storage="memory", llm_provider="claude_cli"))
    assert isinstance(c.llm, ClaudeCodeCliLLM)
    assert c.llm._allow_slash_commands is False


def test_dry_run_only_defaults_true():
    """안전장치는 기본값이 안전한 쪽이어야 한다 (00-product 절대 규칙 2).

    `_env_file=None` 으로 개발자의 `.env` 를 일부러 안 읽는다 — 안 그러면 이 테스트는
    "코드의 기본값"이 아니라 "지금 이 머신의 설정"을 검사하게 된다.
    """
    assert Settings(_env_file=None).dry_run_only is True
