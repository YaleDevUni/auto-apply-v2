"""파일 기반 repository.

M1 은 DB 없이 진행한다 (ARCHITECTURE.md §10 M1). 프로세스를 재시작해도 projection 이
남아야 하므로 in-memory 로는 부족하고, 그렇다고 Alembic 까지 끌어오면 M1 범위를 넘는다.
SQLAlchemy 어댑터는 M2 에서 같은 port 로 추가한다.
"""

import asyncio
import json
from pathlib import Path
from types import TracebackType
from typing import Self

from auto_apply.contracts.dto import ApplicationSummary, PersistState
from auto_apply.domain.enums import ApplicationState


def _summary(application_id: str, latest: PersistState) -> ApplicationSummary:
    return ApplicationSummary(
        application_id=application_id,
        state=latest.state,
        reason=latest.reason,
        scheduled_at=latest.scheduled_at,
        submitted_at=latest.submitted_at,
    )


class FileApplicationRepository:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = asyncio.Lock()

    def _path(self, application_id: str) -> Path:
        safe = application_id.replace("/", "_")
        return self._root / "applications" / f"{safe}.json"

    def _read(self, path: Path) -> list[PersistState]:
        if not path.is_file():
            return []
        raw = json.loads(path.read_text())
        return [PersistState.model_validate(r) for r in raw]

    async def upsert_state(self, state: PersistState) -> None:
        path = self._path(state.application_id)

        def _write() -> None:
            history = self._read(path)
            for i, existing in enumerate(history):
                if (
                    existing.workflow_run_id == state.workflow_run_id
                    and existing.state == state.state
                ):
                    history[i] = state
                    break
            else:
                history.append(state)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps([h.model_dump(mode="json") for h in history], indent=2))
            tmp.replace(path)  # 원자적 교체

        async with self._lock:
            await asyncio.to_thread(_write)

    async def history(self, application_id: str) -> list[PersistState]:
        return await asyncio.to_thread(self._read, self._path(application_id))

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        def _scan() -> list[ApplicationSummary]:
            apps_dir = self._root / "applications"
            if not apps_dir.is_dir():
                return []
            paths = sorted(apps_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            out = []
            for path in paths[:limit]:
                history = self._read(path)
                if history:
                    out.append(_summary(history[-1].application_id, history[-1]))
            return out

        return await asyncio.to_thread(_scan)

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        def _scan() -> dict[str, ApplicationState]:
            out: dict[str, ApplicationState] = {}
            for application_id in application_ids:
                history = self._read(self._path(application_id))
                if history:
                    out[application_id] = history[-1].state
            return out

        return await asyncio.to_thread(_scan)


class FileUnitOfWork:
    def __init__(self, root: Path) -> None:
        self.applications = FileApplicationRepository(root)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None
