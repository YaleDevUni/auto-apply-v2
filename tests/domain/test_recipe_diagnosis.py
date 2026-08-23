"""domain/recipe_diagnosis.py — "recipe 실행 실패 = recipe 파손" 이라는 전제를 깨는 판정 (§2.4a).

회귀의 원본은 실측 스냅샷이다(2026-08-24): wanted 공고 페이지의 지원 버튼이 `지원하기`가
아니라 `지원완료`인 상태에서 `wait_for(text=첨부파일 선택)` 이 15초 timeout 으로 죽었고,
수선 워크플로우가 그걸 selector 문제로 오진했다. 아래 첫 테스트가 정확히 그 상황이다.

두 번째로 중요한 건 **거짓 양성이 없어야 한다**는 것 — 정상 공고 페이지에도 `마감일 상시채용`
처럼 마커와 겹치는 문구가 늘 있다. 실측한 9개 스냅샷 전부에 `마감`이 있었다.
"""

from auto_apply.domain.recipe_diagnosis import PageVerdict, diagnose_page, page_text

_FILLER = "본 채용정보는 원티드랩의 동의없이 무단전재할 수 없습니다. " * 40


def _page(button_label: str, extra: str = "") -> str:
    """실측 스냅샷의 뼈대만 남긴 축소판 — 지원 버튼 라벨과 `마감일 상시채용` 문구를 유지한다."""
    return (
        "<html><head><style>.Button_Button__root{color:red}</style></head><body>"
        "<h1>[인턴] 풀스택 엔지니어</h1>"
        f"<p>{_FILLER}</p>"
        "<div>마감일 상시채용</div>"
        f'<button class="Button_Button__root__MS62F">{button_label}</button>'
        f"{extra}</body></html>"
    )


def test_already_applied_page_is_not_a_recipe_problem() -> None:
    verdict, evidence = diagnose_page(_page("지원완료"))
    assert verdict is PageVerdict.ALREADY_APPLIED
    assert "지원완료" in evidence


def test_normal_posting_with_apply_button_is_recipe_suspected() -> None:
    """`마감일 상시채용`이 있어도 마감으로 오판하면 안 된다 — 정상 스냅샷 9개 전부에 있었다."""
    verdict, _ = diagnose_page(_page("지원하기"))
    assert verdict is PageVerdict.RECIPE_SUSPECTED


def test_login_wall_beats_other_markers() -> None:
    verdict, _ = diagnose_page(_page("지원완료", extra="<p>로그인이 필요한 서비스입니다</p>"))
    assert verdict is PageVerdict.LOGIN_REQUIRED


def test_closed_posting_is_detected() -> None:
    verdict, _ = diagnose_page(_page("지원하기", extra="<p>마감된 공고입니다</p>"))
    assert verdict is PageVerdict.POSTING_CLOSED


def test_missing_snapshot_is_page_not_loaded() -> None:
    assert diagnose_page("(스냅샷을 불러오지 못했다)")[0] is PageVerdict.PAGE_NOT_LOADED


def test_goto_failure_ignores_snapshot_content() -> None:
    """goto 에서 죽었으면 스냅샷이 뭐든 페이지 문제다 — DOM 판정으로 selector 를 의심하지 않는다."""
    verdict, _ = diagnose_page(_page("지원하기"), "goto 실패 (None): Page.goto: Timeout 5000ms")
    assert verdict is PageVerdict.PAGE_NOT_LOADED


def test_marker_in_markup_only_does_not_count() -> None:
    """클래스명/속성에 우연히 들어간 문자열로는 판정하지 않는다 — 본문 텍스트만 본다."""
    html = _page("지원하기").replace(
        '<button class="Button_Button__root__MS62F">',
        '<button class="지원완료-btn" data-state="지원완료">',
    )
    assert "지원완료" not in page_text(html)
    assert diagnose_page(html)[0] is PageVerdict.RECIPE_SUSPECTED
