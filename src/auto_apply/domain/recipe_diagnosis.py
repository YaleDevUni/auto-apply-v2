"""실행 실패의 **진짜 원인**을 DOM 스냅샷으로 먼저 판정한다 (§2.4a).

`domain/recipe_repair.classify_failure`가 실패 "문구"만 보고 TIMEOUT/SELECTOR 를 가르는 데
비해, 여기서는 실패한 시점의 **페이지 자체**를 보고 "애초에 recipe 문제가 아닌 것"을 골라낸다.

왜 필요했나 — 2026-08-24 실측. wanted 지원 한 건이 `wait_for(text=첨부파일 선택)` 15초
timeout 으로 실패해 `AutomationRepairWorkflow`가 돌았는데, 그 시점 스냅샷의 지원 버튼은
`지원하기`가 아니라 **`지원완료`**였다 — 이미 지원한 공고라 지원 패널이 아예 안 열린 것이다.
recipe 는 멀쩡했고, LLM 은 고칠 게 없는 selector 를 두 번 고쳐 보다 샌드박스에서 실패했다.
"recipe 실행이 실패했다 = recipe 가 깨졌다"는 전제가 틀렸던 것이고, 이 모듈이 그 전제를
깨는 자리다. 판정 결과는 (1) 사람에게 보내는 "진짜 깨진 거 맞나요?" 확인 메시지의 근거로,
(2) LLM diff 프롬프트의 힌트로 쓰인다.

**판정은 사람을 대신하지 않는다.** ALREADY_APPLIED 로 보여도 자동으로 수선을 건너뛰지
않는다 — 최종 판단은 사람이 텔레그램 버튼으로 한다(§2.4a). 여기서 하는 일은 "사람이
누르기 전에 볼 근거"를 만드는 것뿐이다.

마커는 스냅샷 실측으로 고른다. `마감`처럼 정상 페이지에도 늘 있는 단어(`마감일 상시채용`)는
쓰지 않는다 — 9개 스냅샷 전부에 있어서 아무것도 못 가른다(2026-08-24 계측).
"""

import re
from enum import StrEnum

_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


class PageVerdict(StrEnum):
    ALREADY_APPLIED = "already_applied"
    LOGIN_REQUIRED = "login_required"
    POSTING_CLOSED = "posting_closed"
    PAGE_NOT_LOADED = "page_not_loaded"
    RECIPE_SUSPECTED = "recipe_suspected"


# (verdict, 마커들). 위에서부터 먼저 걸리는 것이 이긴다 — 로그인 화면에도 "지원완료" 같은
# 단어가 섞여 들어올 수 있어서, 더 결정적인(=페이지 전체를 설명하는) 쪽을 앞에 둔다.
_MARKERS: tuple[tuple[PageVerdict, tuple[str, ...]], ...] = (
    (
        PageVerdict.LOGIN_REQUIRED,
        ("로그인이 필요", "로그인 후 이용", "로그인해 주세요", "로그인하기", "회원가입하기"),
    ),
    (
        PageVerdict.POSTING_CLOSED,
        ("마감된 공고", "채용이 마감", "마감되었습니다", "지원이 마감", "종료된 공고"),
    ),
    (
        PageVerdict.ALREADY_APPLIED,
        ("지원완료", "지원 완료", "이미 지원한", "지원한 공고입니다"),
    ),
)

_HINTS = {
    PageVerdict.ALREADY_APPLIED: (
        "이미 지원한 공고로 보인다 — 지원 패널이 안 열리는 게 정상이므로 recipe 문제가 아닐 "
        "가능성이 높다"
    ),
    PageVerdict.LOGIN_REQUIRED: (
        "로그인 세션이 풀린 것으로 보인다 — scripts/save_auth_state.py 로 다시 로그인해야 "
        "한다. recipe 를 고쳐도 해결되지 않는다"
    ),
    PageVerdict.POSTING_CLOSED: (
        "공고가 마감된 것으로 보인다 — 지원 폼이 없는 게 정상이라 recipe 문제가 아니다"
    ),
    PageVerdict.PAGE_NOT_LOADED: (
        "페이지 자체가 뜨지 않았다(스냅샷이 비었거나 goto 가 실패) — 네트워크/사이트 문제이지 "
        "selector 문제가 아니다"
    ),
    PageVerdict.RECIPE_SUSPECTED: (
        "페이지는 정상으로 보이는데 액션이 실패했다 — recipe 가 실제로 안 맞을 가능성이 높다"
    ),
}

# 이보다 짧은 스냅샷은 "페이지가 안 떴다"로 본다. 정상 공고 페이지는 실측상 25만자를 넘고,
# 조회 실패 시 activities/repair.py 가 넣는 대체 문구는 수십 자다.
_EMPTY_SNAPSHOT_CHARS = 500


def page_text(html: str) -> str:
    """태그/스크립트를 걷어낸 본문 텍스트. 마커 매칭은 항상 이 결과에 대고 한다 —

    `지원완료`가 클래스명/JSON 페이로드에 우연히 들어 있는 경우를 배제하기 위해서다.
    """
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html))


def diagnose_page(snapshot_html: str, failure_reason: str = "") -> tuple[PageVerdict, str]:
    """(판정, 근거) 를 돌려준다. 근거는 사람에게 그대로 보여줄 한 줄이다.

    `failure_reason`은 goto 단계에서 이미 죽어 스냅샷이 의미 없는 경우를 가르는 데만 쓴다 —
    나머지 판정은 전부 페이지 내용으로 한다.
    """
    if len(snapshot_html) < _EMPTY_SNAPSHOT_CHARS or "goto 실패" in failure_reason:
        return PageVerdict.PAGE_NOT_LOADED, "실패 시점의 페이지 내용이 없다"

    text = page_text(snapshot_html)
    for verdict, markers in _MARKERS:
        hit = next((m for m in markers if m in text), None)
        if hit is not None:
            return verdict, f"페이지에서 {hit!r} 를 찾았다"
    return PageVerdict.RECIPE_SUSPECTED, "페이지에서 비정상 신호(이미 지원/로그인/마감)를 못 찾았다"


def hint_for(verdict: PageVerdict) -> str:
    return _HINTS[verdict]
