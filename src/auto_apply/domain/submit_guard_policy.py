"""SubmitGuard 의 결정 규칙 (§A4 L3·L4·L5) — 순수 함수.

실제 드라이버와 테스트 대역이 같은 규칙을 쓴다.

창(window) 모델: 페이지를 건드리는 도구 호출 하나가 창 하나다. 창의 모드는
relaxed(단계 저장·업로드 POST 허용) 또는 strict(비-GET 전부·데이터를 싣는 GET 차단)이고,
창이 끝나도 **다음 창이 열릴 때까지 그 모드가 유지된다** — risky 클릭의 지연 제출
(setTimeout 뒤 fetch)이 창 밖에서 새지 않게.
"""

import unicodedata
from collections.abc import Iterable
from enum import StrEnum
from typing import Literal
from urllib.parse import unquote_plus, urlsplit

from auto_apply.domain.submit_classifier import detect_completion
from auto_apply.domain.unique_identifiers import contains_resident_registration_number
from auto_apply.domain.url_policy import matches_origin

# 입력값이 URL 에 실렸는지 볼 때 이보다 짧은 값은 보지 않는다 — "예"·"3" 같은 값은 어디에나 있다.
MIN_CARRIED_CHARS = 3
_BODYLESS = frozenset({"GET", "HEAD"})


class GuardMode(StrEnum):
    # Safe 클릭·입력·이동 — 문서 POST·입력값을 실은 문서 탐색만 막고 xhr/fetch/beacon 비-GET 은 통과
    RELAXED = "relaxed"
    STRICT = "strict"  # Risky 클릭 — 비-GET 전부 + 쿼리를 싣는 문서 탐색 + 입력값을 싣는 GET 차단


class BlockReason(StrEnum):
    FORBIDDEN_ORIGIN = "forbidden_origin"  # 앱 자신(승인 콘솔) — 가드가 꺼져 있어도 막는다
    DOCUMENT_POST = "document_post"  # 비-GET 문서 탐색(폼 POST) — 모든 모드
    FORM_SUBMIT = "form_submit"  # 페이지 스크립트 층: submit 이벤트·form.submit()·requestSubmit()
    STRICT_NON_GET = "strict_non_get"  # strict 창의 xhr/fetch/beacon/ping 비-GET
    STRICT_GET_QUERY = "strict_get_query"  # strict 창의 쿼리를 싣는 문서 탐색 (GET 제출)
    # 입력한 값·주민번호 꼴을 URL 에 실은 GET — 문서 탐색은 모든 모드, 요청은 strict 창
    CARRIES_INPUT = "carries_input"
    GUARD_ERROR = "guard_error"  # 판정 중 오류 — 닫힌 쪽으로 막았다


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def carried_values(values: Iterable[str]) -> tuple[str, ...]:
    """URL 대조에 쓸 입력값 — 접고, 짧은 값은 버린다."""
    folded = (_fold(v).strip() for v in values)
    return tuple(sorted({v for v in folded if len(v) >= MIN_CARRIED_CHARS}))


def carries_input(url: str, carried: Iterable[str]) -> bool:
    """URL(경로·쿼리)에 에이전트가 입력한 값이나 주민등록번호 꼴이 실렸는가."""
    decoded = _fold(unquote_plus(url))
    if contains_resident_registration_number(decoded):
        return True
    return any(v in decoded for v in carried)


def request_verdict(
    *,
    mode: GuardMode | None,
    method: str,
    url: str,
    navigation: bool,
    carried: Iterable[str] = (),
    forbidden_origins: Iterable[str] = (),
) -> BlockReason | None:
    """요청 하나를 막을지. `mode=None` 은 가드가 꺼진 상태(run 밖) — 앱 출처만 막는다.

    `navigation` = 문서(프레임) 탐색 요청. 판단이 서지 않는 값(알 수 없는 method)은
    비-GET 으로 본다.
    """
    if matches_origin(url, forbidden_origins):
        return BlockReason.FORBIDDEN_ORIGIN
    if mode is None:
        return None
    if method.strip().upper() not in _BODYLESS:
        if navigation:
            return BlockReason.DOCUMENT_POST
        return BlockReason.STRICT_NON_GET if mode is GuardMode.STRICT else None
    if mode is GuardMode.RELAXED:
        # 입력값을 실은 문서 탐색은 GET 폼 제출이다 — 페이지 스크립트 층이 비껴가져도 막는다.
        # 요청(xhr·fetch)은 relaxed 에서 본다: 주소 검색 같은 자동 완성이 입력값을 싣는다.
        return BlockReason.CARRIES_INPUT if navigation and carries_input(url, carried) else None
    if navigation and urlsplit(url).query:
        return BlockReason.STRICT_GET_QUERY
    if carries_input(url, carried):
        return BlockReason.CARRIES_INPUT
    return None


def dialog_verdict(kind: str) -> Literal["accept", "dismiss"]:
    """FILL 단계의 네이티브 대화상자 (§A4 L4).

    alert 은 버튼이 하나라 결정이 없다 — 나머지(confirm·prompt·beforeunload)는 전부 거절.
    """
    return "accept" if kind.strip().lower() == "alert" else "dismiss"


def completion_evidence(
    before_urls: Iterable[str],
    before_lines: Iterable[str],
    after_urls: Iterable[str],
    after_lines: Iterable[str],
) -> str | None:
    """동작 뒤 **새로 나타난** 완료 근거 (§A4 L5). 동작 전부터 있던 줄·URL 은 보지 않는다.

    오탐은 run 중단(안전)이고 미탐은 사고 은폐라, 새 줄끼리 이어 붙여(줄 경계를 넘는 문구도) 본다.
    """
    seen = set(before_lines)
    fresh = "\n".join(line for line in after_lines if line not in seen)
    hit = detect_completion(text=fresh) if fresh else None
    if hit:
        return hit
    old_urls = set(before_urls)
    return next(
        (h for u in after_urls if u not in old_urls and (h := detect_completion(url=u))), None
    )
