"""PlatformAdapter port 의 원티드 구현 — `FixturePlatformAdapter` 뒤에 미뤄져 있던 실제 대역.

공고 조회는 `job_source/wanted.py` 와 같은 공개 상세 API(`DETAIL_URL`)를 재사용한다 — 인증이
필요 없고, 두 트랙(공고 수집 vs 지원 실행)이 같은 데이터를 다른 DTO(`JobPosting` vs `JobRef`)로
쓸 뿐이라 파싱 로직을 또 만들 이유가 없다.

`verify_submission`만 예외적으로 인증이 필요하다 — "내 지원 현황"(`/api/v1/applications`)은
본인 데이터라 로그인 쿠키 없인 조회가 안 된다(`AttachmentManager`와 같은 storage_state 재사용
패턴, `adapters/_wanted_auth.py`). agent-browser 라이브 탐색(2026-08-20)으로 확인한 것들:
`/api/v1/applications`는 `job_id` 쿼리 파라미터로 특정 공고만 필터링해주고, `create_time`은
`WantedAttachmentManager`의 `update_time`과 같은 타임존 표기 없는 KST 벽시계 값이다. 또한
`user_id`(numeric)가 없으면 401 이 나는데 storage_state 엔 쿠키만 있어 이 값을 안 갖고 있어서
`/api/v1/me`로 먼저 조회한다(쿠키만으로 동작, 두 API 모두 같은 도메인 `www.wanted.co.kr`).
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from auto_apply.adapters._wanted_auth import wanted_cookie_client
from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.adapters.job_source.wanted import DETAIL_URL
from auto_apply.contracts.dto import Eligibility, JobRef, VerifyInput, VerifyResult
from auto_apply.domain.errors import PolicyViolation

_JOB_ID = re.compile(r"/wd/(\d+)")
_ME_URL = "https://www.wanted.co.kr/api/v1/me"
_APPLICATIONS_URL = "https://www.wanted.co.kr/api/v1/applications"
_KST = ZoneInfo("Asia/Seoul")
# 워크플로우 시각(Temporal, UTC)과 wanted 서버 시각 사이의 오차를 흡수하는 여유.
# 너무 크면 과거의 무관한 지원 기록을 오탐하고, 너무 작으면 정상 제출을 놓친다.
_CLOCK_SKEW = timedelta(minutes=5)


def _extract_job_id(url: str) -> str | None:
    match = _JOB_ID.search(urlparse(url).path)
    return match.group(1) if match else None


def _wanted_job_id(job_id: str) -> str | None:
    """`JobRef.job_id`(예: "wanted:380443")에서 wanted 쪽 순수 job_id만 뽑는다."""
    prefix = "wanted:"
    return job_id[len(prefix) :] if job_id.startswith(prefix) else None


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
        # job_id/since 가 없으면 애초에 뭘 대조할지 모른다 — 거짓 확인(verified=True 오판)이
        # 부분 제출을 놓치는 것보다 훨씬 위험하므로 항상 unverified 로 안전하게 떨어뜨린다
        # (_execution.py 의 "부분 제출 위험 방어" 로직이 그 다음을 받는다).
        job_id = _wanted_job_id(inp.job_id)
        if job_id is None or inp.since is None:
            return VerifyResult(
                verified=False, detail="job_id/since 정보 없음 — 확인 불가, 사람 확인 필요"
            )

        async with wanted_cookie_client(self._auth_dir, transport=self._auth_transport) as client:
            # /api/v1/applications 는 본인 확인용으로 numeric user_id 를 요구한다(실측: 없으면
            # 401) — storage_state 에는 쿠키만 있어 이 값을 안 들고 있으니 /api/v1/me 로 먼저
            # 구한다. 그 응답엔 email/jwt 등 민감정보가 같이 오므로 user_id 외엔 쓰지 않는다.
            me = await client.get(_ME_URL)
            me.raise_for_status()
            user_id = me.json()["id"]

            resp = await client.get(
                _APPLICATIONS_URL,
                params={
                    "user_id": user_id,
                    "sort": "-apply_time,-create_time",
                    "limit": 5,
                    "status": "complete,pass,hire,reject",
                    "includes": "summary",
                    "job_id": job_id,
                },
            )
            resp.raise_for_status()
            body = resp.json()

        cutoff = inp.since - _CLOCK_SKEW
        for row in body.get("applications", []):
            created = datetime.fromisoformat(row["create_time"]).replace(tzinfo=_KST)
            if created >= cutoff:
                return VerifyResult(
                    verified=True, detail=f"원티드 지원 현황에서 확인됨(status={row.get('status')})"
                )
        return VerifyResult(verified=False, detail="원티드 지원 현황에서 확인 안 됨")
