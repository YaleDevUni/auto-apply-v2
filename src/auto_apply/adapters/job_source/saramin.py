"""사람인 — 검색결과 정적 HTML 파싱.

검색 리스트 카드 하나에 지역/경력/학력/고용형태/연봉/직무키워드/마감일이 모두
들어있다. 그래서 상세 페이지를 따로 받지 않아도 하드컷 필터가 동작한다
(요청 수를 크게 줄이는 핵심 지점) — `enrich()`는 그래서 no-op 이다.
"""

import re
from collections.abc import AsyncIterator, Sequence
from datetime import date
from urllib.parse import quote, urljoin

from selectolax.parser import HTMLParser, Node

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.contracts.job import JobPosting

BASE = "https://www.saramin.co.kr"
SEARCH = BASE + "/zf_user/search/recruit"

# 구 프로젝트 config.yaml 이식 — 트랙별로 조준한 검색어 + 놓치면 안 되는 회사명.
# 사람인 검색은 관련도순이라 직무명이 정확히 맞아야 상위에 뜬다. 검색어만으로는
# 특정 공고를 보장 못 하므로 관심 기업은 회사명으로 따로 훑는다.
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "신입 개발자",
    "신입 백엔드",
    "MES 생산관리",
    "ERP 시스템 담당",
    "신입 서비스기획",
    "기술영업 신입",
    "솔루션 엔지니어",
    "신입 경영지원",
    "신입 영업",
    "신입 구매",
    "신입 사무직",
    "리테일 MD",
    "정산 담당",
)
DEFAULT_COMPANIES: tuple[str, ...] = ("쿠팡", "네이버", "카카오", "배달의민족", "토스", "당근")


def parse_deadline(raw: str) -> str | None:
    """'~ 08/29(토)' → '2026-08-29'. '상시채용'/'채용시' 등은 None."""
    raw = raw.strip()
    m = re.search(r"(\d{1,2})/(\d{1,2})", raw)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    today = date.today()
    year = today.year
    if month < today.month - 1:  # 연말/연초 경계: 이미 지난 달이면 내년 공고
        year += 1
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_posted(raw: str) -> str | None:
    """'등록일 26/07/30' → '2026-07-30'"""
    m = re.search(r"(\d{2})/(\d{2})/(\d{2})", raw)
    if not m:
        return None
    yy, mm, dd = (int(x) for x in m.groups())
    try:
        return date(2000 + yy, mm, dd).isoformat()
    except ValueError:
        return None


def _text(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


class SaraminJobSource:
    def __init__(
        self,
        client: ThrottledClient,
        *,
        keywords: Sequence[str] = DEFAULT_KEYWORDS,
        companies: Sequence[str] = DEFAULT_COMPANIES,
        pages: int = 3,
    ) -> None:
        self._client = client
        self._queries = (*keywords, *companies)
        self._pages = pages

    @property
    def platform(self) -> str:
        return "saramin"

    async def list_jobs(self) -> AsyncIterator[JobPosting]:
        seen: set[str] = set()
        for keyword in self._queries:
            for page in range(1, self._pages + 1):
                url = (
                    f"{SEARCH}?searchType=search&searchword={quote(keyword)}"
                    f"&recruitPage={page}&recruitSort=relation&recruitPageCount=40"
                )
                resp = await self._client.get(url)
                if resp is None:
                    break
                nodes = HTMLParser(resp.text).css(".item_recruit")
                if not nodes:
                    break
                for node in nodes:
                    job = _parse_card(node, keyword)
                    if job is None or job.platform_job_id in seen:
                        continue
                    seen.add(job.platform_job_id)
                    yield job
                if len(nodes) < 40:
                    break

    async def enrich(self, job: JobPosting) -> JobPosting:
        return job


def _parse_card(node: Node, keyword: str) -> JobPosting | None:
    rec_idx = node.attributes.get("value")
    if not rec_idx:
        return None

    link = node.css_first(".job_tit a")
    if link is None:
        return None
    title = link.attributes.get("title") or _text(link)
    href = link.attributes.get("href") or ""

    company = _text(node.css_first(".area_corp .corp_name a")) or _text(
        node.css_first(".area_corp .corp_name")
    )

    # 조건 영역: 지역은 <a>, 나머지(경력/학력/고용형태/연봉)는 <span>
    cond = node.css_first(".job_condition")
    location = experience = education = emp_type = salary = None
    if cond is not None:
        locs = [_text(a) for a in cond.css("a") if _text(a)]
        location = " ".join(dict.fromkeys(locs)) or None
        spans = [_text(sp) for sp in cond.css("span") if _text(sp)]
        spans = [sp for sp in spans if sp not in locs and sp != (location or "").replace(" ", "")]
        for sp in spans:
            if re.search(r"신입|경력|무관|인턴", sp) and experience is None:
                experience = sp
            elif "졸" in sp or "학력" in sp:
                education = sp
            elif re.search(r"정규직|계약직|인턴|파견|프리랜서|아르바이트", sp):
                emp_type = sp
            elif "만원" in sp or "연봉" in sp or "회사내규" in sp:
                salary = sp

    sector_raw = _text(node.css_first(".job_sector"))
    posted_at = parse_posted(sector_raw)
    sector = re.sub(r"등록일.*$", "", sector_raw).strip(" ,")

    deadline = parse_deadline(_text(node.css_first(".job_date .date")))

    # 리스트 카드 자체가 필터에 필요한 정보를 다 담고 있으므로 description으로 합성
    description = "\n".join(
        f"[{k}] {v}"
        for k, v in (
            ("직무분야", sector),
            ("지역", location),
            ("경력", experience),
            ("학력", education),
            ("고용형태", emp_type),
            ("급여", salary),
            ("검색어", keyword),
        )
        if v
    )

    return JobPosting(
        platform="saramin",
        platform_job_id=str(rec_idx),
        url=urljoin(BASE, href.replace("&amp;", "&")),
        company=company,
        title=title,
        category=sector or None,
        location=location,
        employment_type=emp_type,
        experience_req=experience,
        education_req=education,
        salary=salary,
        deadline=deadline,
        posted_at=posted_at,
        description=description,
        raw={"rec_idx": rec_idx, "sector": sector, "keyword": keyword},
    )
