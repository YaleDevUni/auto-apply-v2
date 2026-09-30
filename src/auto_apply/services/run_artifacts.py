"""run 이 남기는 기록 — FILL 종료 기록(ReviewRecord)과 입력 기록(FillLog) (§A3 runs, §A5).

BlobStore 의 `runs/<run_id>/` 아래 JSON 으로 둔다. 승인 화면(M4)은 ReviewRecord 를,
재진입 run(T3.4)은 직전 run 의 FillLog 부분 기록을 읽는다. 두 DTO 모두 고유식별정보가 있으면
만들어지지 않는다(절대 규칙 5) — 저장 직전에 한 번 더 본다.
"""

from auto_apply.contracts._base import ensure_identifier_free
from auto_apply.contracts.fill_log import FillLog
from auto_apply.contracts.submit_guard import ReviewRecord
from auto_apply.domain.errors import BlobNotFound
from auto_apply.ports.storage import BlobStore

_JSON = "application/json"


def _key(run_id: str, name: str) -> str:
    return f"runs/{run_id}/{name}.json"


class RunArtifacts:
    def __init__(self, store: BlobStore) -> None:
        self._store = store

    async def save_review(self, run_id: str, review: ReviewRecord) -> None:
        ensure_identifier_free(review)
        await self._store.put(
            _key(run_id, "review"), review.model_dump_json().encode(), content_type=_JSON
        )

    async def load_review(self, run_id: str) -> ReviewRecord | None:
        data = await self._get(_key(run_id, "review"))
        return None if data is None else ReviewRecord.model_validate_json(data)

    async def save_fill_log(self, run_id: str, fill_log: FillLog) -> None:
        ensure_identifier_free(fill_log)
        await self._store.put(
            _key(run_id, "fill_log"), fill_log.model_dump_json().encode(), content_type=_JSON
        )

    async def load_fill_log(self, run_id: str) -> FillLog | None:
        data = await self._get(_key(run_id, "fill_log"))
        return None if data is None else FillLog.model_validate_json(data)

    async def _get(self, key: str) -> bytes | None:
        try:
            return await self._store.get(key)
        except BlobNotFound:
            return None
