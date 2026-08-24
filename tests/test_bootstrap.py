"""composition root 테스트 — 환경변수만 바꿔 구현이 교체되는지 확인 (§11.4)."""

from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.bootstrap import build_container
from auto_apply.config import Settings


def test_offline_profile_builds():
    c = build_container(Settings(storage="memory", llm_provider="stub", notifier="console"))
    assert isinstance(c.store, InMemoryBlobStore)


def test_storage_swap_by_config(tmp_path):
    c = build_container(Settings(storage="local", data_dir=tmp_path))
    assert isinstance(c.store, LocalBlobStore)


def test_chat_llm_alone_allows_slash_commands_for_turn_reuse():
    """§ claude_code_cli.py "turn()" 절 — 이력서 생성용 llm 은 외부 공고 텍스트를 다뤄서

    계속 잠가두고, 텔레그램 전용 chat_llm 만 turn()(§ /clear 재사용)을 쓸 수 있게 연다."""
    c = build_container(Settings(storage="memory", llm_provider="claude_cli", notifier="console"))
    assert isinstance(c.llm, ClaudeCodeCliLLM)
    assert isinstance(c.chat_llm, ClaudeCodeCliLLM)
    assert c.llm._allow_slash_commands is False
    assert c.chat_llm._allow_slash_commands is True


def test_dry_run_only_defaults_true():
    """안전장치는 기본값이 안전한 쪽이어야 한다 (§9.5).

    `_env_file=None` 으로 개발자의 `.env` 를 일부러 안 읽는다 — 안 그러면 이 테스트는
    "코드의 기본값"이 아니라 "지금 이 머신의 설정"을 검사하게 돼서, DRY_RUN_ONLY=false 로
    라이브 실행을 켜둔 머신에서 항상 빨간불이 된다(실측).
    """
    assert Settings(_env_file=None).dry_run_only is True
