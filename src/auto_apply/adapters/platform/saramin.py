"""PlatformAdapter port 의 사람인(saramin) 구현 — `wanted-platform-adapter-gap` 과 같은 공백을

여기서도 메운다(job_source 는 있어도 지원 실행용 어댑터는 없었다).

공고 조회는 로그인 없이 열리는 relay-view 페이지(`/zf_user/jobs/relay/view?rec_idx=`)를 쓴다 —
실측(2026-08-21): 비로그인 상태에선 본문 HTML이 SPA 셸만 내려오고 실제 채용공고 텍스트는 없지만,
`<meta property="og:title">`/`<meta name="description">` 는 서버사이드로 채워져 있어(회사명이
`[괄호]`로, 요약이 "회사, 직무, 경력:.., 학력:.., 마감일:.., 홈페이지:.." 형식 CSV로) 이력서
매칭에 필요한 title/company/description 을 로그인 없이도 뽑을 수 있다. 마감/비공개/삭제된
rec_idx 는 404 또는 og:title 이 "사람인"(브랜드명 그대로)뿐이라 이 두 경우를 못 찾은 것으로
취급한다.

`verify_submission`은 "내 지원 현황" 페이지(`/zf_user/persons/apply-status-list`)를 쿠키
인증(`_saramin_auth.saramin_cookie_client`)으로 조회해 구현했다. 이전엔 저장된 storage_state
쿠키로도 로그인 페이지로 리다이렉트돼서 "WAF 가 비-브라우저 요청을 막는다"고 추정했는데,
재실측(2026-08-21)해보니 원인은 그게 아니라 **저장된 세션 자체가 이미 만료**돼 있었던
것이었다 — 신선한 세션으로는 UA/Referer 헤더만 실으면 httpx 로도 200이 온다(wanted 와 같은
쿠키-only 패턴, 헤더만 추가로 필요). 페이지는 서버사이드 렌더링이라(SPA API 콜 없음)
`[data-rec_idx]` 를 가진 각 지원 항목 div 를 selectolax 로 파싱해 rec_idx 매칭 + `.col_date`
날짜 대조로 확인한다. 세션이 다시 만료돼 로그인 페이지로 리다이렉트되면(`/zf_user/auth`)
`AuthRequired`를 던진다 — `_execution.py` 가 그걸 안전하게 unverified 로 떨어뜨린다.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from selectolax.parser import HTMLParser, Node

from auto_apply.adapters._saramin_auth import saramin_cookie_client
from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.contracts.dto import Eligibility, JobRef, VerifyInput, VerifyResult
from auto_apply.domain.errors import AuthRequired, PolicyViolation

BASE = "https://www.saramin.co.kr"
DETAIL_URL = BASE + "/zf_user/jobs/relay/view?rec_idx={rec_idx}"
_APPLY_STATUS_URL = BASE + "/zf_user/persons/apply-status-list"
_LOGIN_PATH_MARKER = "/zf_user/auth"

_OG_TITLE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"')
_OG_DESCRIPTION = re.compile(r'<meta\s+property="og:description"\s+content="([^"]*)"')
_BRACKET_TITLE = re.compile(r"^\[(?P<company>[^\]]+)\]\s*(?P<title>.+)$")
_DDAY_SUFFIX = re.compile(r"\((?:D-\d+|상시채용|채용시)\)$")
_DEADLINE = re.compile(r"마감일:(\d{4}-\d{2}-\d{2})")

_KST = ZoneInfo("Asia/Seoul")
# 워크플로우 시각(UTC)과 사람인 서버 시각 사이 오차 흡수 — wanted verify_submission 과 같은 값.
_CLOCK_SKEW = timedelta(minutes=5)


def _text(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


def _saramin_rec_idx(job_id: str) -> str | None:
    """`JobRef.job_id`(예: "saramin:54646145")에서 사람인 쪽 순수 rec_idx만 뽑는다."""
    prefix = "saramin:"
    return job_id[len(prefix) :] if job_id.startswith(prefix) else None


def _parse_apply_datetime(text: str) -> datetime | None:
    """ "2026.08.21 00:24" 형식(실측) — 타임존 표기 없는 KST 벽시계 값이다."""
    try:
        return datetime.strptime(text.strip(), "%Y.%m.%d %H:%M").replace(tzinfo=_KST)
    except ValueError:
        return None


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
    def __init__(
        self,
        client: ThrottledClient,
        *,
        auth_dir: Path,
        auth_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = client
        self._auth_dir = auth_dir
        self._auth_transport = auth_transport  # 테스트에서만 httpx.MockTransport 로 주입한다

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
        # job_id/since 가 없으면 애초에 뭘 대조할지 모른다 — wanted verify_submission 과 같은
        # 이유로 항상 unverified 로 안전하게 떨어뜨린다(§5 부분 제출 위험 방어).
        rec_idx = _saramin_rec_idx(inp.job_id)
        if rec_idx is None or inp.since is None:
            return VerifyResult(
                verified=False, detail="job_id/since 정보 없음 — 확인 불가, 사람 확인 필요"
            )

        async with saramin_cookie_client(self._auth_dir, transport=self._auth_transport) as client:
            resp = await client.get(_APPLY_STATUS_URL)
            if _LOGIN_PATH_MARKER in str(resp.url):
                # 세션 만료 — scripts/auto_login.py 로 재로그인해야 풀린다(재시도로 안 풀림).
                raise AuthRequired("사람인 로그인 세션이 만료됐다 — scripts/auto_login.py saramin")
            resp.raise_for_status()
            html = resp.text

        cutoff = inp.since - _CLOCK_SKEW
        tree = HTMLParser(html)
        for node in tree.css("[data-rec_idx]"):
            if node.attributes.get("data-rec_idx") != rec_idx:
                continue
            applied_at = _parse_apply_datetime(_text(node.css_first(".col_date")))
            if applied_at is not None and applied_at >= cutoff:
                detail = f"사람인 지원 현황에서 확인됨({applied_at})"
                return VerifyResult(verified=True, detail=detail)
        return VerifyResult(verified=False, detail="사람인 지원 현황에서 확인 안 됨")
