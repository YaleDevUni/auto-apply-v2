"""PlatformAdapter port 의 원티드 구현 — `FixturePlatformAdapter` 뒤에 미뤄져 있던 실제 대역.

공고 조회는 `job_source/wanted.py` 와 같은 공개 상세 API(`DETAIL_URL`)를 재사용한다 — 인증이
필요 없고, 두 트랙(공고 수집 vs 지원 실행)이 같은 데이터를 다른 DTO(`JobPosting` vs `JobRef`)로
쓸 뿐이라 파싱 로직을 또 만들 이유가 없다.
"""

import re
from typing import Any
from urllib.parse import urlparse

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.adapters.job_source.wanted import DETAIL_URL
from auto_apply.contracts.dto import Eligibility, JobRef, VerifyInput, VerifyResult
from auto_apply.domain.errors import PolicyViolation

_JOB_ID = re.compile(r"/wd/(\d+)")


def _extract_job_id(url: str) -> str | None:
    match = _JOB_ID.search(urlparse(url).path)
    return match.group(1) if match else None


def _describe(jd: dict[str, Any]) -> str:
    detail = jd.get("detail") or {}
    sections = [
        ("소개", detail.get("intro")),
        ("주요업무", detail.get("main_tasks")),
        ("자격요건", detail.get("requirements")),
        ("우대사항", detail.get("preferred_points")),
        ("혜택", detail.get("benefits")),
    ]
    description = "\n\n".join(
        f"[{name}]\n{body.strip()}" for name, body in sections if body and body.strip()
    )
    skills = jd.get("skill_tags") or []
    names = [str(s.get("title")) for s in skills if isinstance(s, dict) and s.get("title")]
    if names:
        description += "\n\n[기술스택]\n" + ", ".join(names)
    return description


class WantedPlatformAdapter:
    def __init__(self, client: ThrottledClient) -> None:
        self._client = client

    @property
    def platform(self) -> str:
        return "wanted"

    def matches(self, url: str) -> bool:
        return (urlparse(url).hostname or "").endswith("wanted.co.kr")

    async def fetch_job(self, url: str) -> JobRef:
        job_id = _extract_job_id(url)
        if job_id is None:
            raise PolicyViolation(f"원티드 공고 URL 형식이 아니다: {url}")

        data = await self._client.get_json(DETAIL_URL.format(job_id=job_id))
        jd = (data or {}).get("job") if isinstance(data, dict) else None
        if not jd:
            # 마감/비공개/삭제된 공고 — 재시도로 안 풀리는 실패라 PolicyViolation(non-retryable).
            raise PolicyViolation(f"원티드 공고를 찾을 수 없다(마감/비공개/삭제): {url}")

        company = ((jd.get("company") or {}).get("name")) or ""
        return JobRef(
            job_id=f"wanted:{job_id}",
            platform="wanted",
            url=url,
            title=str(jd.get("position") or ""),
            company=str(company),
            description=_describe(jd),
        )

    async def evaluate(self, job: JobRef) -> Eligibility:
        # 적합도/지원가능성 축(job_screening/job_applicability)은 이미 JobCollectionWorkflow 가
        # 통과시킨 공고만 여기로 넘어온다는 전제라 다시 채점하지 않는다(축 재사용은 DTO가 갈려
        # 있어 억지로 매핑하느니 분리 유지가 낫다는 판단, ARCHITECTURE.md §11.2b 설계와 같은
        # 이유). 여기서는 "지금 이 순간에도 지원 버튼이 살아있는가"만 다시 본다 — 실측(2026-08-20,
        # wanted-test-3/4): fetch_job 은 성공해도(공고 자체는 존재) 지원 마감된 공고는
        # `job.detail.status == "close"`(지원 버튼이 disabled)라 Recipe 실행이 "첨부파일
        # 선택" 텍스트를 영영 못 찾고 timeout 난다 — 이력서 생성/텔레그램 승인까지 다 끝난
        # 뒤에야 죽어서 낭비가 크다. evaluate 단계에서 걸러 REJECTED 로 조기 종료시킨다.
        job_id = _extract_job_id(job.url)
        if job_id is None:
            return Eligibility(eligible=True)  # fetch_job 이 이미 통과했으니 여기 안 온다

        data = await self._client.get_json(DETAIL_URL.format(job_id=job_id))
        jd = (data or {}).get("job") if isinstance(data, dict) else None
        detail = (jd or {}).get("detail") or {}
        if detail.get("status") == "close":
            return Eligibility(eligible=False, reason="원티드 공고가 지원마감 상태다")
        return Eligibility(eligible=True)

    async def verify_submission(self, inp: VerifyInput) -> VerifyResult:
        # TODO(M2 후속): 원티드 "내 지원 현황" API로 실제 확인하는 로직이 아직 없다.
        # 거짓 확인(verified=True 오판)이 부분 제출을 놓치는 것보다 훨씬 위험하므로, 확인
        # 수단이 생기기 전까지는 항상 unverified 를 돌려 needs_human 으로 안전하게 떨어뜨린다
        # (_execution.py 의 "부분 제출 위험 방어" 로직이 그 다음을 받는다).
        return VerifyResult(
            verified=False, detail="wanted verify_submission 미구현 — 사람 확인 필요"
        )
