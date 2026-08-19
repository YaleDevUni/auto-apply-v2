"""자소설닷컴 — 내부 JSON API. 로그인 없이 진행중 공고 전체를 한 번에 받는다.

목록: GET /api/v1/employment_companies?all=true   → 진행중 공고 전량
상세: GET /api/v1/employment_companies/{id}        → content(HTML), 이미지형 공고 다수

공고 상당수가 '이미지 한 장'이다 — 텍스트가 없으면 자소서 문항도 지원 요건도
읽어낼 수 없다. `domain.job_applicability`가 이걸 IMAGE_ONLY로 막고 에이전트가
비전으로 읽어야 할 대상으로 넘긴다.

이미지는 로컬 파일로 직접 저장하지 않고 BlobStore(§11.2)에 넣는다 — 구 프로젝트는
자체 ASSET_DIR에 직접 썼는데, 이미 있는 저장 추상화를 두고 새 파일 경로 규칙을
또 만들 이유가 없다.
"""

import re
from collections.abc import AsyncIterator
from typing import Any

from selectolax.parser import HTMLParser

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.contracts.job import JobPosting
from auto_apply.ports.storage import BlobStore

API = "https://jasoseol.com/api/v1"
LIST_URL = f"{API}/employment_companies"
HEADERS = {"Accept": "application/json", "Referer": "https://jasoseol.com/recruit"}


class JasoseolJobSource:
    def __init__(self, client: ThrottledClient, store: BlobStore) -> None:
        self._client = client
        self._store = store

    @property
    def platform(self) -> str:
        return "jasoseol"

    async def list_jobs(self) -> AsyncIterator[JobPosting]:
        data = await self._client.get_json(LIST_URL, params={"all": "true"}, headers=HEADERS)
        if not isinstance(data, list):
            return

        for company in data:
            employments = company.get("employments") or []
            if not employments:
                yield _build(company, None)
                continue

            # 한 회사가 여러 직무를 올린 경우 직무별로 분리해 필터링 정확도를 높인다.
            # 다만 같은 자리를 employment_id만 바꿔 여러 번 주기도 하므로, 제목이
            # 구분되지 않으면 같은 공고로 보고 접는다.
            seen_titles: set[str] = set()
            for emp in employments:
                job = _build(company, emp)
                if job.title in seen_titles:
                    continue
                seen_titles.add(job.title)
                yield job

    async def enrich(self, job: JobPosting) -> JobPosting:
        """상세 content(HTML)를 받아 본문 텍스트 또는 공고 이미지를 확보한다."""
        cid = job.raw.get("company_id")
        if not cid:
            return job

        data = await self._client.get_json(f"{LIST_URL}/{cid}", headers=HEADERS)
        if not isinstance(data, dict):
            return job

        update: dict[str, Any] = {}
        content = data.get("content") or ""
        image_url = job.image_url
        if content:
            tree = HTMLParser(content)
            text = re.sub(r"\n{3,}", "\n\n", tree.text(separator="\n")).strip()
            if len(text) > 40:
                update["description"] = (job.description + "\n\n[공고 본문]\n" + text).strip()
            imgs = [
                img.attributes.get("src") for img in tree.css("img") if img.attributes.get("src")
            ]
            if imgs:
                image_url = imgs[0]
                update["raw"] = {**job.raw, "content_images": imgs}

        if image_url:
            update["image_url"] = image_url
            image_path = await self._store_image(job, image_url)
            if image_path:
                update["image_path"] = image_path

        detail = {k: v for k, v in data.items() if k != "content"}
        update["raw"] = {**update.get("raw", job.raw), "detail": detail}
        return job.model_copy(update=update) if update else job

    async def _store_image(self, job: JobPosting, url: str) -> str | None:
        """공고 이미지를 BlobStore에 저장해 에이전트가 비전으로 읽을 수 있게 한다."""
        resp = await self._client.get(url)
        if resp is None:
            return None
        ext = ".webp" if ".webp" in url else (".png" if ".png" in url else ".jpg")
        key = f"job-images/{job.platform}/{job.platform_job_id}{ext}"
        content_type = {"webp": "image/webp", "png": "image/png"}.get(ext.strip("."), "image/jpeg")
        return await self._store.put(key, resp.content, content_type=content_type)


def _build(company: dict[str, Any], emp: dict[str, Any] | None) -> JobPosting:
    cid = company.get("id")
    eid = emp.get("id") if emp else None
    pid = f"{cid}-{eid}" if eid else str(cid)

    field = (emp or {}).get("field") or ""
    base_title = company.get("title") or ""
    title = f"{base_title} / {field}" if field and field not in base_title else base_title

    deadline = (emp or {}).get("end_time") or company.get("end_time")

    desc_parts = []
    if field:
        desc_parts.append(f"[모집직무] {field}")
    for key, label in (
        ("english_score_requirement", "영어성적"),
        ("certificate_requirement", "자격증"),
        ("graduate_condition", "졸업조건"),
        ("work_type", "근무형태"),
    ):
        value = (emp or {}).get(key)
        if value:
            desc_parts.append(f"[{label}] {value}")
    if (emp or {}).get("has_resume"):
        desc_parts.append("[자소서] 자기소개서 문항 있음")

    group = company.get("company_group") or {}

    return JobPosting(
        platform="jasoseol",
        platform_job_id=pid,
        url=f"https://jasoseol.com/recruit/{cid}",
        company=company.get("name", ""),
        title=title,
        category=field or None,
        deadline=deadline,
        posted_at=company.get("opened_at") or company.get("created_at"),
        image_url=company.get("image_url"),
        description="\n".join(desc_parts),
        raw={
            "company_id": cid,
            "employment_id": eid,
            "has_resume": bool((emp or {}).get("has_resume")),
            "employment_page_url": company.get("employment_page_url"),
            "business_size": group.get("business_size"),
            "business_type": group.get("business_type"),
        },
    )
