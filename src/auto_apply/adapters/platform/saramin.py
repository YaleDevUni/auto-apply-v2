"""PlatformAdapter port 의 사람인(saramin) 구현 — `wanted-platform-adapter-gap` 과 같은 공백을

여기서도 메운다(job_source 는 있어도 지원 실행용 어댑터는 없었다).

공고 조회는 로그인 없이 열리는 relay-view 페이지(`/zf_user/jobs/relay/view?rec_idx=`)를 쓴다 —
실측(2026-08-21): 비로그인 상태에선 본문 HTML이 SPA 셸만 내려오고 실제 채용공고 텍스트는 없지만,
`<meta property="og:title">`/`<meta name="description">` 는 서버사이드로 채워져 있어(회사명이
`[괄호]`로, 요약이 "회사, 직무, 경력:.., 학력:.., 마감일:.., 홈페이지:.." 형식 CSV로) 이력서
매칭에 필요한 title/company/description 을 로그인 없이도 뽑을 수 있다. 마감/비공개/삭제된
rec_idx 는 404 또는 og:title 이 "사람인"(브랜드명 그대로)뿐이라 이 두 경우를 못 찾은 것으로
취급한다.

`verify_submission` 은 아직 항상 `unverified` — "내 지원 현황" 페이지(`/zf_user/persons/
apply-status-list`)는 로그인 세션이 필요한데, 저장된 storage_state 쿠키를 그대로 실어도(httpx,
Referer/UA 포함) 로그인 페이지로 리다이렉트되는 걸 실측 확인했다(2026-08-21) — wanted 와 달리
쿠키만으로는 안 풀리는 걸로 보인다(WAF 가 브라우저가 아닌 요청을 세션 유효성과 무관하게
막을 가능성, `saramin-recipe-progress` 메모리의 "사람인은 headless 도 WAF 에 막힌다" 실측과
같은 결의 문제로 추정 — 확정하려면 실제 로그인 브라우저 세션으로 재검증 필요). 거짓 확인
(verified=True 오판)이 부분 제출 누락보다 훨씬 위험하므로(§5) 지금은 wanted 가 verify_submission
을 실제로 구현하기 전과 같은 안전한 기본값을 택한다.
"""

import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.contracts.dto import Eligibility, JobRef, VerifyInput, VerifyResult
from auto_apply.domain.errors import PolicyViolation

BASE = "https://www.saramin.co.kr"
DETAIL_URL = BASE + "/zf_user/jobs/relay/view?rec_idx={rec_idx}"

_OG_TITLE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"')
_OG_DESCRIPTION = re.compile(r'<meta\s+property="og:description"\s+content="([^"]*)"')
_BRACKET_TITLE = re.compile(r"^\[(?P<company>[^\]]+)\]\s*(?P<title>.+)$")
_DDAY_SUFFIX = re.compile(r"\((?:D-\d+|상시채용|채용시)\)$")
_DEADLINE = re.compile(r"마감일:(\d{4}-\d{2}-\d{2})")


def _extract_rec_idx(url: str) -> str | None:
    values = parse_qs(urlparse(url).query).get("rec_idx")
    return values[0] if values else None


def _parse_meta(html: str) -> tuple[str, str, str] | None:
    """(company, title, description) 을 뽑는다. 마감/비공개/삭제 등으로 못 뽑으면 None."""
    og_title = _OG_TITLE.search(html)
    if og_title is None:
        return None
    raw_title = og_title.group(1).removesuffix(" - 사람인").strip()
    m = _BRACKET_TITLE.match(raw_title)
    if m is None:
        return None  # 브랜드명뿐인 og:title("사람인") — 공고를 못 찾은 것

    og_description = _OG_DESCRIPTION.search(html)
    description = og_description.group(1).strip() if og_description else ""
    description = "\n".join(p.strip() for p in description.split(",") if p.strip())
    return m.group("company").strip(), m.group("title").strip(), description


class SaraminPlatformAdapter:
    def __init__(self, client: ThrottledClient) -> None:
        self._client = client

    @property
    def platform(self) -> str:
        return "saramin"

    def matches(self, url: str) -> bool:
        return (urlparse(url).hostname or "").endswith("saramin.co.kr")

    async def fetch_job(self, url: str) -> JobRef:
        rec_idx = _extract_rec_idx(url)
        if rec_idx is None:
            raise PolicyViolation(f"사람인 공고 URL 형식이 아니다(rec_idx 없음): {url}")

        resp = await self._client.get(DETAIL_URL.format(rec_idx=rec_idx))
        parsed = _parse_meta(resp.text) if resp is not None else None
        if parsed is None:
            raise PolicyViolation(f"사람인 공고를 찾을 수 없다(마감/비공개/삭제): {url}")

        company, title, description = parsed
        title = _DDAY_SUFFIX.sub("", title).strip()
        return JobRef(
            job_id=f"saramin:{rec_idx}",
            platform="saramin",
            url=url,
            title=title,
            company=company,
            description=description,
        )

    async def evaluate(self, job: JobRef) -> Eligibility:
        # collect 시점(job_source)에 이미 하드컷을 통과한 공고지만, 이력서 생성/승인을 거치는
        # 동안 마감됐을 수 있다 — wanted evaluate() 와 같은 재확인(ARCHITECTURE.md §11.2b).
        m = _DEADLINE.search(job.description)
        if m and date.fromisoformat(m.group(1)) < date.today():
            return Eligibility(eligible=False, reason="사람인 공고가 지원마감 상태다")
        return Eligibility(eligible=True)

    async def verify_submission(self, inp: VerifyInput) -> VerifyResult:
        return VerifyResult(
            verified=False,
            detail="사람인 verify_submission 미구현 — 로그인 세션 확보 방식 재검증 필요",
        )
