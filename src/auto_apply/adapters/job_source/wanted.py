"""원티드 — 공개 JSON API. 로그인/브라우저 불필요.

목록: /api/chaos/navigation/v1/results
상세: /api/chaos/jobs/v1/{id}/details   (본문 전문 포함)
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.contracts.job import JobPosting

LIST_URL = "https://www.wanted.co.kr/api/chaos/navigation/v1/results"
DETAIL_URL = "https://www.wanted.co.kr/api/chaos/jobs/v1/{job_id}/details"
PAGE_SIZE = 100


@dataclass(frozen=True)
class JobGroup:
    id: int
    name: str


# 원티드 직무 그룹 ID — 실제 API 응답으로 검증된 값 (구 프로젝트 config.yaml 이식)
DEFAULT_JOB_GROUPS: tuple[JobGroup, ...] = (
    JobGroup(518, "개발"),
    JobGroup(507, "경영·비즈니스·기획"),
    JobGroup(523, "마케팅"),
    JobGroup(510, "고객서비스·CX"),
    JobGroup(513, "엔지니어링·설계"),
    JobGroup(517, "HR·채용"),
    JobGroup(508, "금융·재무"),
)


class WantedJobSource:
    def __init__(
        self,
        client: ThrottledClient,
        *,
        job_groups: Sequence[JobGroup] = DEFAULT_JOB_GROUPS,
        pages: int = 3,
        years: int = 0,  # 0=신입, -1=전체
    ) -> None:
        self._client = client
        self._job_groups = job_groups
        self._pages = pages
        self._years = years

    @property
    def platform(self) -> str:
        return "wanted"

    async def list_jobs(self) -> AsyncIterator[JobPosting]:
        seen: set[str] = set()
        for group in self._job_groups:
            for page in range(self._pages):
                params = {
                    "job_group_id": group.id,
                    "country": "kr",
                    "job_sort": "job.latest_order",
                    "years": self._years,
                    "locations": "all",
                    "limit": PAGE_SIZE,
                    "offset": page * PAGE_SIZE,
                }
                data = await self._client.get_json(LIST_URL, params=params)
                items: list[Any] = (data.get("data") or []) if isinstance(data, dict) else []
                if not items:
                    break
                for item in items:
                    job_id = str(item.get("id"))
                    if job_id in seen:
                        continue
                    seen.add(job_id)
                    yield _parse_list_item(item, group.name)
                if len(items) < PAGE_SIZE:
                    break

    async def enrich(self, job: JobPosting) -> JobPosting:
        """상세 API로 본문 전문을 채운다. 하드컷 통과 건에만 호출한다."""
        data = await self._client.get_json(DETAIL_URL.format(job_id=job.platform_job_id))
        if not isinstance(data, dict):
            return job

        jd = data.get("job") or {}
        detail = jd.get("detail") or {}
        sections = [
            ("소개", detail.get("intro")),
            ("주요업무", detail.get("main_tasks")),
            ("자격요건", detail.get("requirements")),
            ("우대사항", detail.get("preferred_points")),
            ("혜택", detail.get("benefits")),
            ("채용절차", detail.get("hire_rounds")),
        ]
        description = "\n\n".join(
            f"[{name}]\n{body.strip()}" for name, body in sections if body and body.strip()
        )

        skills = jd.get("skill_tags") or []
        names = [str(s.get("title")) for s in skills if isinstance(s, dict) and s.get("title")]
        if names:
            description += "\n\n[기술스택]\n" + ", ".join(names)

        addr = jd.get("address") or {}
        update: dict[str, Any] = {
            "description": description,
            "deadline": jd.get("due_time") or job.deadline,
            "raw": {**job.raw, "detail": data},
        }
        if addr.get("full_location"):
            update["location"] = addr["full_location"]
        return job.model_copy(update=update)


def _parse_list_item(item: dict[str, Any], group_name: str) -> JobPosting:
    company = (item.get("company") or {}).get("name", "")
    addr = item.get("address") or {}
    location = " ".join(x for x in (addr.get("location"), addr.get("district")) if x) or addr.get(
        "country"
    )
    return JobPosting(
        platform="wanted",
        platform_job_id=str(item.get("id")),
        url=f"https://www.wanted.co.kr/wd/{item.get('id')}",
        company=company,
        title=item.get("position", ""),
        category=group_name,
        location=location,
        deadline=item.get("due_time"),
        image_url=(item.get("title_img") or {}).get("origin"),
        salary=str(item.get("reward_total")) if item.get("reward_total") else None,
        raw=item,
    )
