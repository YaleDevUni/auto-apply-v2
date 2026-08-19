"""축 1 — 적합도. 하드컷 + 트랙 판정 + 스코어링. 전부 순수 함수, LLM 미사용.

제외된 공고는 사유와 함께 남기되 어떤 LLM 호출도 발생시키지 않는다 — 그래야
수천 건을 걸러도 비용이 0이다. 로직 구조는 구 프로젝트(screening/rules.py)에서
수천 건으로 튠된 것을 그대로 가져왔다. 트랙/하드컷/키워드 값 자체는
config/matching.yaml(MatchingConfig)에서 온다 — 이 파일은 값을 모른다.
"""

import functools
import re
from datetime import date, datetime

from auto_apply.contracts.job import JobPosting, ScreeningVerdict
from auto_apply.contracts.matching_config import MatchingConfig
from auto_apply.domain.job_identity import headline_text, searchable_text

_HANGUL = re.compile(r"[가-힣]")

# 제목·직무그룹에서 잡힌 키워드를 본문 키워드 몇 개만큼으로 칠지.
# 본문은 회사소개·복리후생·채용절차를 함께 담고 있어 직무와 무관한 단어가 깔린다.
HEADLINE_WEIGHT = 3
# 플랫폼이 붙인 직무그룹이 일치할 때의 값. 우리가 짐작한 단어가 아니라
# 플랫폼이 분류해 준 사실이라 제목 키워드 하나보다 무겁다.
CATEGORY_WEIGHT = 4


@functools.lru_cache(maxsize=4096)
def _matcher(kw: str) -> re.Pattern[str] | None:
    """라틴 문자 키워드는 단어 경계를 요구한다. 한글은 부분일치 그대로.

    부분일치로 통일하면 짧은 영문 약어가 아무 데나 걸린다(`ba`가 database 안에,
    `pm`이 12.8% 걸려 백엔드 공고가 PM 트랙으로 판정된 사례). 한글은 조사가
    붙어 이어 쓰므로 경계를 요구하면 대부분을 놓친다 — 문자 종류로 갈라 다룬다.
    None 이면 부분일치로 보라는 뜻이다.
    """
    if _HANGUL.search(kw):
        return None
    return re.compile(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])")


def _hits(text: str, keywords: list[str]) -> list[str]:
    out: list[str] = []
    for kw in keywords:
        pat = _matcher(kw)
        if pat.search(text) if pat is not None else kw in text:
            out.append(kw)
    return out


def _deadline_passed(deadline: str | None) -> bool:
    if not deadline:
        return False  # 상시채용
    try:
        d = datetime.fromisoformat(deadline.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return d < date.today()


def detect_track(
    text: str, cfg: MatchingConfig, headline: str = "", category: str | None = None
) -> tuple[str | None, str | None, list[str]]:
    """가장 많이 매칭된 트랙을 고른다. 동점이면 weight 가 높은 쪽.

    단순히 세지 않고 어디서 잡혔는지를 함께 본다 — 본문 전체를 평등하게 세면
    '채용'·'운영' 같은 잡음 단어가 거의 모든 공고에서 공짜 점수를 준다
    (구 프로젝트 실측: 원티드 764건 중 19건이 그렇게 오분류됨).
    """
    head = headline or ""
    cat = (category or "").strip().lower()
    best: tuple[int, int, str] | None = None
    best_hits: list[str] = []

    for key, track in cfg.tracks.items():
        strong = set(_hits(head, track.keywords))
        strong |= set(_hits(head, track.headline_keywords))
        weak = [kw for kw in _hits(text, track.keywords) if kw not in strong]
        # 직무그룹은 정확히 일치할 때만. 부분일치면 '사업개발'이 개발이 된다.
        cat_hit = cat in track.categories
        if not strong and not weak and not cat_hit:
            continue
        score = (
            (CATEGORY_WEIGHT if cat_hit else 0) + len(strong) * HEADLINE_WEIGHT + len(weak),
            track.weight,
        )
        if best is None or score > best[:2]:
            best = (*score, key)
            best_hits = ([f"직무그룹:{cat}"] if cat_hit else []) + sorted(strong) + weak

    if best is None:
        return None, None, []
    track_key = best[2]
    return track_key, cfg.tracks[track_key].label, best_hits


def screen(job: JobPosting, cfg: MatchingConfig) -> ScreeningVerdict:
    """공고 하나를 판정한다."""
    text = searchable_text(job)
    track_key, track_label, track_hits = detect_track(text, cfg, headline_text(job), job.category)

    # 이 트랙이 무력화하는 하드컷 (예: MES/생산관리 사무직은 TRADE_FIELD 면제)
    overrides = cfg.tracks[track_key].overrides_hardcut if track_key else []

    if _deadline_passed(job.deadline):
        return _excluded("CLOSED", "마감", [job.deadline or ""], track_key, track_label)

    for code, cut in cfg.hardcuts.items():
        if code == "CLOSED":
            continue
        # always 는 트랙 예외로도 못 넘는다.
        always_hits = _hits(text, cut.always)
        if code in overrides and not always_hits:
            continue
        hits = always_hits or _hits(text, cut.keywords)
        if not hits:
            continue
        # unless 키워드가 있으면 하드컷 취소. 단 always 신호는 못 되돌린다.
        if not always_hits and cut.unless and _hits(text, cut.unless):
            continue
        return _excluded(code, cut.label, hits[:6], track_key, track_label)

    # 지원 대상 트랙에 아예 안 걸리면 관심 밖 직무로 간주
    if track_key is None:
        return _excluded("OFF_TRACK", "지원 트랙 외", [], None, None)

    sc = cfg.scoring
    detail: dict[str, object] = {}

    base = cfg.tracks[track_key].weight // 2
    detail["트랙"] = {"track": track_label, "점수": base, "근거": track_hits[:5]}
    score = base

    stack_hits = _hits(text, sc.keywords_stack)
    stack_pts = min(len(stack_hits) * sc.stack_bonus, sc.stack_max)
    if stack_pts:
        detail["기술스택"] = {"점수": stack_pts, "근거": stack_hits[:8]}
        score += stack_pts

    for label, kws, pts in (
        ("신입채용", sc.keywords_newbie, sc.newbie_bonus),
        ("영어우대", sc.keywords_english, sc.english_bonus),
        ("원격근무", sc.keywords_remote, sc.remote_bonus),
        ("제조·산업도메인", sc.keywords_domain, sc.domain_bonus),
    ):
        hits = _hits(text, kws)
        if hits:
            detail[label] = {"점수": pts, "근거": hits[:4]}
            score += pts

    loc_hits = _hits(text, cfg.location.preferred)
    if loc_hits:
        detail["선호지역"] = {"점수": cfg.location.preferred_bonus, "근거": loc_hits[:3]}
        score += cfg.location.preferred_bonus

    # 상한을 두지 않는다. 100에서 자르면 상위권이 전부 뭉개져 구분이 안 된다.
    return ScreeningVerdict(
        verdict="pass",
        track=track_key,
        track_label=track_label,
        fit_score=score,
        score_detail=detail,
    )


def _excluded(
    code: str, label: str, hits: list[str], track: str | None, track_label: str | None
) -> ScreeningVerdict:
    return ScreeningVerdict(
        verdict="excluded",
        exclude_code=code,
        exclude_label=label,
        exclude_hits=hits,
        track=track,
        track_label=track_label,
    )
