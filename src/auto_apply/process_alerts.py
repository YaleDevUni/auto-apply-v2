"""상주 프로세스가 죽으면 알린다 (ARCHITECTURE.md §11.2d).

이 시스템은 상주 프로세스 3개에 기대고 있다 — `make worker`(활동 실행), `make telegram-listen`
(사람의 승인 버튼 수신), `make watchdog`(워크플로우 감시). 셋 중 하나가 죽으면 시스템은
"에러 없이 아무 일도 안 일어나는" 상태가 된다:

  - worker 가 죽으면 워크플로우는 FAILED 가 아니라 Running 인 채로 멈춘다 → watchdog 은
    닫힌 워크플로우만 보므로 못 잡는다.
  - listener 가 죽으면 승인 버튼이 그냥 안 먹는다 — 워크플로우는 approval_timeout(기본 72시간)
    이 지나서야 EXPIRED 로 끝난다.
  - watchdog 이 죽으면 감시 자체가 사라진다. 이때만은 아무도 안 알려준다.

셋 다 지금은 콘솔 traceback 뿐이라, 백그라운드로 띄워두면 죽은 걸 알 방법이 없다. `cli.py`/
`watchdog.py`/`apply_intake.py`와 같은 운영 진입점 계층이다 — 새 port 를 만들지 않고 이미
있는 `Notifier` 를 그대로 쓴다(§11.6).

알림 자체가 실패해도(텔레그램 장애 등) 원래 예외를 삼키지 않는다 — 알림은 부가 기능이고
프로세스의 종료 코드/traceback 이 여전히 1차 진실이다.
"""

from collections.abc import Awaitable, Callable

import structlog

from auto_apply.bootstrap import build_container
from auto_apply.config import load_settings
from auto_apply.contracts.dto import NotifyEvent
from auto_apply.ports.notifier import Notifier

log = structlog.get_logger(__name__)


async def notify_safely(notifier: Notifier, event: NotifyEvent) -> bool:
    """알림 전송 실패로 호출자가 죽지 않게 감싼다. 보냈으면 True.

    알림을 보내는 자리는 대부분 이미 뭔가 잘못된 지점(크래시 핸들러, 예외 처리기)이다 —
    거기서 알림이 또 터지면 원래 오류가 traceback 에서 가려진다.
    """
    try:
        await notifier.notify(event)
        return True
    except Exception:
        log.exception("process_alerts.notify_failed", kind=event.kind)
        return False


async def alert(kind: str, message: str) -> bool:
    """컨테이너를 갖고 있지 않은 자리(프로세스 크래시 핸들러 등)에서 한 건 알린다.

    컨테이너를 새로 만드는 이유 — 크래시는 컨테이너 조립 전에도 날 수 있고, 조립된 컨테이너를
    들고 있어도 그게 이미 망가진 상태일 수 있다. 조립 자체가 실패하면 로그만 남긴다.
    """
    try:
        container = build_container(load_settings())
    except Exception:
        log.exception("process_alerts.container_failed", kind=kind)
        return False
    return await notify_safely(container.notifier, NotifyEvent(kind=kind, message=message))


async def run_guarded(name: str, main: Callable[[], Awaitable[None]]) -> None:
    """상주 프로세스의 main() 을 감싼다. 예외로 죽으면 알리고 그대로 재던진다.

    `Exception` 만 잡는다 — `SystemExit`(설정 오류로 인한 조기 종료, 사람이 콘솔에서 바로
    본다)와 `KeyboardInterrupt`(사람이 직접 껐다)는 알릴 일이 아니다.
    """
    try:
        await main()
    except Exception as e:
        log.exception("process_alerts.process_crashed", process=name)
        await alert(
            "PROCESS_CRASHED",
            f"{name} 프로세스가 죽었다: {type(e).__name__}: {e}\n"
            "재기동 전까지 이 프로세스가 담당하던 일은 조용히 멈춘 상태다.",
        )
        raise
