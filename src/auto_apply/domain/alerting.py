"""조용한 실패를 알림 문구로 바꾸는 순수 규칙 (ARCHITECTURE.md §11.2d).

watchdog(`watchdog.py`)은 Temporal 이 FAILED/TERMINATED/TIMED_OUT 으로 닫은 워크플로우만
본다. 그런데 이 프로젝트에는 **성공으로 끝나는 실패**가 있다 — 스케줄로 도는 두 워크플로우가
그렇다:

  - `JobCollectionWorkflow` 는 플랫폼 하나가 죽어도 나머지를 살리려고 예외를 결과 필드
    (`PlatformCollectionResult.error`)로 삼킨다. 워크플로우는 COMPLETED 로 끝나므로 watchdog
    의 시야 밖이다. 셀렉터가 바뀌어 `found=0` 이 되는 경우는 예외조차 안 난다.
  - `ApplyIntakeWorkflow` 는 후보가 0건이어도 정상 종료한다 — 정작 사람이 기대한 "매일 N건
    지원 시작"은 일어나지 않았는데도.

둘 다 "아무 일도 안 일어난 것처럼 보이는" 실패라 로그를 뒤지지 않으면 며칠도 모른다. 판정을
워크플로우 코드가 아니라 여기 순수 함수로 두는 이유는 Recipe/가이드 patch 와 같다 — 조건을
바꿀 때 Temporal 없이 테스트로 고정할 수 있어야 한다.

`None` = 알릴 것 없음. 문자열 = 그대로 사람에게 보낼 본문.
"""

from collections.abc import Sequence

from auto_apply.contracts.job import PlatformCollectionResult

# 상세 조회는 공고 하나하나에 대해 도는 루프라 네트워크 사정으로 한둘 실패하는 건 정상이다.
# 무더기로 깨지는 건 사이트 구조가 바뀌었다는 신호라 사람이 알아야 한다.
ENRICH_ERROR_ALERT_MIN = 5


def collection_alert(results: Sequence[PlatformCollectionResult]) -> str | None:
    """공고 수집 결과에서 사람이 알아야 할 이상만 골라낸다.

    셋 중 하나라도 걸리면 알린다: (1) 플랫폼 활동이 아예 실패, (2) 예외 없이 0건 수집
    (셀렉터 변경/세션 만료의 전형적 증상 — 예외가 안 나서 제일 조용하다), (3) 상세 조회가
    무더기로 실패.
    """
    if not results:
        return "공고 수집이 플랫폼을 하나도 처리하지 않았다 — Schedule 인자를 확인해라."

    problems: list[str] = []
    for r in results:
        if r.error:
            problems.append(f"- {r.platform}: 수집 실패 — {r.error}")
        elif r.found == 0:
            problems.append(f"- {r.platform}: 0건 수집 (셀렉터 변경/세션 만료 의심)")
        elif r.enrich_errors >= ENRICH_ERROR_ALERT_MIN:
            problems.append(f"- {r.platform}: 상세 조회 {r.enrich_errors}건 실패 (found={r.found})")
    if not problems:
        return None

    healthy = [r for r in results if not r.error and r.found > 0]
    lines = ["공고 수집에 문제가 있다", *problems]
    if healthy:
        summary = ", ".join(f"{r.platform} {r.actionable}건" for r in healthy)
        lines.append(f"정상 수집: {summary}")
    return "\n".join(lines)


def intake_alert(*, started: int, skipped: int, candidates: int) -> str | None:
    """자동 지원 시작 결과. "한 건도 시작 못 했다"가 이 워크플로우의 조용한 실패다.

    후보 자체가 0건이면 상류(수집/캐시 TTL)가 끊긴 것이고, 후보는 있는데 시작이 0건이면
    전부 기존 이력에 걸러진 것이다 — 원인이 다르니 문구를 나눈다. 한 건이라도 시작했으면
    스케줄이 제 일을 한 것이라 알리지 않는다(매일 도는 루틴이라 성공 알림은 소음이 된다).
    """
    if started > 0:
        return None
    if candidates == 0:
        return (
            "자동 지원 시작: 후보 공고가 0건이다 — 공고 수집이 안 돌았거나 캐시(24시간)가"
            " 전부 만료됐다. `make watchdog`/수집 Schedule 상태를 확인해라."
        )
    return (
        f"자동 지원 시작: 한 건도 시작하지 못했다 (후보 {candidates}건, 기존 이력으로"
        f" 제외 {skipped}건). 새로 지원할 공고가 없다는 뜻이니 수집 조건을 넓힐지 확인해라."
    )
