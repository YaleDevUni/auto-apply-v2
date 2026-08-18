"""composition root 테스트 — 환경변수만 바꿔 구현이 교체되는지 확인 (§11.4)."""

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


def test_dry_run_only_defaults_true():
    """안전장치는 기본값이 안전한 쪽이어야 한다 (§9.5)."""
    assert Settings().dry_run_only is True
