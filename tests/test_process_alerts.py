"""process_alerts.py — 상주 프로세스(worker/listener/watchdog)가 죽으면 알린다.

`build_container`/`load_settings`를 실제로 부르면 `.env` 와 파일시스템에 의존하게 되므로
(테스트는 개발자 `.env` 를 읽으면 안 된다 — 기존 dry_run_only 회귀와 같은 이유) 그 둘만
대역으로 갈아끼운다.
"""

import pytest

from auto_apply import process_alerts
from auto_apply.contracts.dto import NotifyEvent
from tests.ports.test_notifier_contract import RecordingNotifier


class _Boom(Exception):
    pass


class _ExplodingNotifier:
    async def request_decision(self, req):  # pragma: no cover - 이 테스트에선 안 쓴다
        raise AssertionError("호출되지 않아야 한다")

    async def notify(self, event: NotifyEvent) -> None:
        raise _Boom("텔레그램이 죽었다")


@pytest.fixture
def recording(monkeypatch) -> RecordingNotifier:
    notifier = RecordingNotifier()
    monkeypatch.setattr(process_alerts, "load_settings", lambda: object())
    monkeypatch.setattr(
        process_alerts, "build_container", lambda cfg: type("C", (), {"notifier": notifier})()
    )
    return notifier


async def test_notify_safely_swallows_a_broken_channel():
    """알림은 이미 뭔가 잘못된 자리에서 부른다 — 거기서 또 터지면 원래 오류가 가려진다."""
    sent = await process_alerts.notify_safely(_ExplodingNotifier(), NotifyEvent(kind="X"))

    assert sent is False


async def test_run_guarded_alerts_and_reraises_on_crash(recording):
    async def main() -> None:
        raise _Boom("워커가 죽었다")

    with pytest.raises(_Boom):
        await process_alerts.run_guarded("worker", main)

    assert [e.kind for e in recording.events] == ["PROCESS_CRASHED"]
    assert "worker" in recording.events[0].message
    assert "워커가 죽었다" in recording.events[0].message


async def test_run_guarded_is_silent_on_a_clean_exit(recording):
    async def main() -> None:
        return None

    await process_alerts.run_guarded("worker", main)

    assert recording.events == []


async def test_run_guarded_does_not_alert_on_deliberate_exits(recording):
    """`SystemExit`(설정 오류로 인한 조기 종료)/`KeyboardInterrupt`(사람이 껐다)는 사고가 아니다."""

    async def bails() -> None:
        raise SystemExit("NOTIFIER=telegram 이어야 리스너를 돌릴 수 있다")

    with pytest.raises(SystemExit):
        await process_alerts.run_guarded("telegram listener", bails)

    assert recording.events == []


async def test_alert_survives_a_container_that_cannot_be_built(monkeypatch):
    def boom(_cfg):
        raise _Boom("어댑터 조립 실패")

    monkeypatch.setattr(process_alerts, "load_settings", lambda: object())
    monkeypatch.setattr(process_alerts, "build_container", boom)

    assert await process_alerts.alert("PROCESS_CRASHED", "x") is False
