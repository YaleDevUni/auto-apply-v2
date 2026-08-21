"""축 2 — 지원가능성. "이걸 자동으로 지원할 수 있나". 순수 함수.

적합도(job_screening.py)와 분리한 이유는 두 판정이 직교하기 때문이다. 점수가
높은데 외부 ATS라 손도 못 대는 공고가 있고, 점수가 낮은데 원클릭인 공고가 있다.
하나의 점수로 뭉개면 "왜 지원 안 했는지"를 설명할 수 없다.

구 프로젝트(screening/applicability.py)와 갈라지는 지점: 그쪽은 이 함수 안에서
레시피 파일을 직접 읽었다. 여기서는 `recipe_exists`/`form_has_essays`처럼
호출부(미래의 activity)가 이미 확인한 값을 받는다 — domain은 파일도, DB도 모른다.
"""

import re
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlparse

from auto_apply.contracts.job import ApplicabilityVerdict, Blocker, JobPosting, ScreeningVerdict
from auto_apply.contracts.matching_config import ApplicabilityRules
from auto_apply.domain.enums import BlockerCode
from auto_apply.domain.job_identity import searchable_text

# 자사 채용 시스템(ATS) 호스트. 여기로 튀면 플랫폼 폼이 아니라 남의 폼이다.
KNOWN_ATS = (
    "greetinghr.com",
    "greeting.works",
    "recruiter.co.kr",
    "midasitcareer.com",
    "career.co.kr",
    "jobkorea.co.kr",
    "incruit.com",
    "applyin.co.kr",
    "gohr.co.kr",
    "aplim.co.kr",
    "workable.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "taleo.net",
)

_ESSAY_MARKERS = re.compile(
    r"자기소개서|자소서|지원동기|입사후\s*포부|성장과정|경험을\s*기술|"
    r"기술해\s*주(세요|십시오)|서술하(세요|시오)|\d{3,4}자\s*(이내|내외)",
)

_DOC_MARKERS = {
    "졸업증명서": re.compile(r"졸업증명"),
    "성적증명서": re.compile(r"성적증명"),
    "경력증명서": re.compile(r"경력증명"),
    "자격증사본": re.compile(r"자격증\s*사본|자격증\s*제출"),
    "포트폴리오": re.compile(r"포트폴리오\s*(제출|첨부|필수)"),
    "어학성적": re.compile(r"토익|토플|오픽|opic|텝스|어학\s*성적"),
}


def caution_documents(text: str) -> list[str]:
    """자동화가 절대 못 채우는 첨부서류 힌트(성적증명서 등) — 포트폴리오는 이미 자동
    첨부되므로 제외한다(§ wanted-application-caution-indicators-backlog). 사람이 그 서류를
    가지고 있는지(`cfg.available_documents`)와 무관하게 "이 폼은 사람이 직접 챙겨야 하는
    항목이 있다"는 사실 자체를 승인 전에 알려주는 목적이라, 지원 가능 여부를 막는
    `evaluate_applicability`의 `requires["documents"]`/`DOC_MISSING` blocker 판정과는 기준이
    다르다(그쪽은 보유 여부와 대조해 막을지 말지를 정한다).
    """
    return [name for name, pat in _DOC_MARKERS.items() if name != "포트폴리오" and pat.search(text)]


def _block(code: BlockerCode, label: str, detail: str = "") -> Blocker:
    return Blocker(code=code, label=label, detail=detail)


def _days_left(deadline: str | None) -> int | None:
    if not deadline:
        return None
    try:
        d = datetime.fromisoformat(deadline.replace("Z", "+00:00")).date()
    except ValueError:
        return None
    return (d - date.today()).days


def _is_external(url: str | None) -> str | None:
    """외부 ATS 호스트면 그 호스트명을 돌려준다."""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    for ats in KNOWN_ATS:
        if host.endswith(ats):
            return host
    return None


def _count_essays(text: str) -> int:
    """문항 수를 어림한다. 정확할 필요는 없다 — 0인지 아닌지가 먼저다.

    0으로 잘못 세면 에이전트가 '자소서 없음'으로 믿고 폼 중간에서 막히는데,
    그게 더 나쁜 실패라 마커가 있으면 최소 1을 돌려준다.
    """
    limits = re.findall(r"\d{3,4}자\s*(?:이내|내외)", text)
    if limits:
        return len(limits)
    numbered = re.findall(r"^\s*(?:\d|[①-⑩])[.)]\s*\S+", text, re.MULTILINE)
    if numbered and _ESSAY_MARKERS.search(text):
        return len(numbered)
    return 1 if _ESSAY_MARKERS.search(text) else 0


def evaluate_applicability(
    job: JobPosting,
    screening: ScreeningVerdict,
    cfg: ApplicabilityRules,
    *,
    recipe_exists: bool,
    form_has_essays: bool = True,
    session_ok: bool | None = None,
    required_gaps: int = 0,
) -> ApplicabilityVerdict:
    """공고 하나의 자동지원 가능 여부를 판정한다.

    session_ok — 이 공고 플랫폼의 로그인 세션이 유효한지. None이면 '모른다'로
    보고 LOGIN_REQUIRED는 달지 않되 confidence를 깎는다 (호출부가 activity에서
    확인해 넘긴다).
    form_has_essays — 이 플랫폼의 지원 폼에 자소서 문항 입력란이 실제로 있는지
    (RecipeSource가 안다). 없으면(예: 원티드) 본문의 '자기소개서' 언급은 이력서
    문서에 담을 내용이지 폼에서 막히는 지점이 아니다.
    required_gaps — 이 공고로 이력서를 조립해봤을 때 필수요건 중 근거 없는 항목 수.
    """
    text = searchable_text(job)
    blockers: list[Blocker] = []
    requires: dict[str, object] = {}
    confidence = 1.0

    # ── 채널 판별 ──────────────────────────────────────────────
    external_host = _is_external(job.url) or _is_external(
        str(job.raw.get("employment_page_url") or "") or None
    )
    channel: Literal["platform_form", "external_ats", "image_only"]
    if external_host:
        channel = "external_ats"
        apply_url = str(job.raw.get("employment_page_url") or job.url)
    elif job.image_path:
        # 본문 자리에 이미지가 있어 저장해둔 공고. image_url이 아니라 image_path로
        # 판별한다 — image_url은 표지 썸네일에도 흔히 붙어 오탐이 크다.
        channel = "image_only"
        apply_url = job.url
    else:
        channel = "platform_form"
        apply_url = job.url

    # ── blocker 판정 ───────────────────────────────────────────

    # 1) 점수 하한. 완전 자동화의 마지막 안전장치.
    if screening.fit_score < cfg.min_fit_score:
        blockers.append(
            _block(
                BlockerCode.SCORE_BELOW_BAR,
                "자동지원 최소 점수 미달",
                f"{screening.fit_score}점 < 기준 {cfg.min_fit_score}점",
            )
        )

    # 2) 마감. 스크리닝 이후 날짜가 지났을 수 있고, 마감 당일 지원은 실패 위험이 크다.
    left = _days_left(job.deadline)
    if left is not None:
        if left < 0:
            blockers.append(_block(BlockerCode.CLOSED, "마감됨", job.deadline or ""))
        elif left < cfg.min_days_left:
            blockers.append(
                _block(BlockerCode.CLOSING_TOO_SOON, "마감 임박 — 자동지원 위험", f"{left}일 남음")
            )

    # 3) 채널별 구조적 제약
    if channel == "external_ats":
        blockers.append(
            _block(BlockerCode.EXTERNAL_ATS, "외부 채용시스템으로 이동", external_host or "")
        )
        confidence = min(confidence, 0.6)
    elif channel == "image_only":
        blockers.append(
            _block(
                BlockerCode.IMAGE_ONLY,
                "이미지형 공고 — 본문 텍스트 없음",
                job.image_path or job.image_url or "",
            )
        )
        confidence = min(confidence, 0.4)

    # 4) 로그인 세션. 로그인은 자동화 대상이 아니라 '한 번 해두고 재사용하는 상태'다.
    if session_ok is None:
        requires["login"] = True
        confidence = min(confidence, 0.7)  # 세션 상태를 모른다
    else:
        requires["login"] = True
        if not session_ok:
            blockers.append(
                _block(
                    BlockerCode.LOGIN_REQUIRED,
                    "로그인 세션 없음 — 사람이 한 번 로그인해야 함",
                    job.platform,
                )
            )

    # 5) 폼 레시피. 없으면 어디를 눌러야 할지 모른다.
    if channel == "platform_form":
        if not recipe_exists:
            blockers.append(_block(BlockerCode.NO_RECIPE, "지원 폼 레시피 없음", job.platform))
        requires["resume"] = "공고별 조립"

    # 5-b) 이력서를 실제로 조립해봤더니 필수요건 근거가 없더라 — 되먹임.
    # 적합도 점수는 키워드 존재만 세고 사용자가 실제로 할 수 있는지와 대조하지
    # 않는다 — 이 값이 그 사각지대를 메운다.
    if required_gaps > cfg.max_required_gaps:
        blockers.append(
            _block(
                BlockerCode.REQUIREMENT_GAP,
                "필수요건 대응 근거 부족",
                f"이력서 조립 시 필수 미충족 {required_gaps}건 > 기준 {cfg.max_required_gaps}건",
            )
        )

    # 6) 자소서 문항 — 설정에 따라 blocker이거나 requirement다.
    n_essays = _count_essays(job.description)
    if job.raw.get("has_resume"):  # 자소설닷컴이 명시적으로 주는 플래그
        n_essays = max(n_essays, 1)

    if n_essays:
        requires["essays"] = n_essays
        if not form_has_essays:
            # 막지 않는다. 이력서를 조립할 때 반영할 요구사항으로만 남긴다.
            requires["essay_in_document"] = True
        elif not cfg.essays.autowrite:
            blockers.append(
                _block(
                    BlockerCode.ESSAY_REQUIRED,
                    "자소서 문항 있음 — 자동작성 꺼져 있음",
                    f"약 {n_essays}문항",
                )
            )
        elif n_essays > cfg.essays.max_autowrite:
            blockers.append(
                _block(
                    BlockerCode.ESSAY_TOO_MANY,
                    "자소서 문항이 자동작성 한도 초과",
                    f"{n_essays}문항 > 한도 {cfg.essays.max_autowrite}",
                )
            )

    # 7) 첨부서류. 미리 준비 안 돼 있으면 폼 중간에서 막힌다.
    docs = [name for name, pat in _DOC_MARKERS.items() if pat.search(text)]
    if docs:
        requires["documents"] = docs
        missing = [d for d in docs if d not in cfg.available_documents]
        if missing:
            blockers.append(_block(BlockerCode.DOC_MISSING, "요구 서류 미보유", ", ".join(missing)))

    # 8) 본문 미확보. 요건을 읽지도 않고 지원하는 것은 무인 시스템이 해서는 안 되므로
    # 낮은 confidence가 아니라 blocker로 세운다.
    if len(job.description) < cfg.min_description_chars and channel != "image_only":
        blockers.append(
            _block(
                BlockerCode.NO_DETAIL,
                "공고 본문 미확보 — 요건 확인 불가",
                f"본문 {len(job.description)}자 < 기준 {cfg.min_description_chars}자",
            )
        )
        confidence = min(confidence, 0.5)

    return ApplicabilityVerdict(
        actionable=not blockers,
        channel=channel,
        apply_url=apply_url,
        blockers=blockers,
        requires=requires,
        confidence=round(confidence, 2),
    )
