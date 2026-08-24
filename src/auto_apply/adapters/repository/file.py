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

from auto_apply.contracts.dto import (
    ApplicationAttempt,
    ApplicationSummary,
    CachedResume,
    PersistState,
    ScheduleConfig,
)
from auto_apply.contracts.job import JobRecord
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


class FileJobRepository:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = asyncio.Lock()

    def _path(self, platform: str, platform_job_id: str) -> Path:
        safe = f"{platform}__{platform_job_id}".replace("/", "_")
        return self._root / "jobs" / f"{safe}.json"

    async def upsert(self, record: JobRecord) -> None:
        path = self._path(record.job.platform, record.job.platform_job_id)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record.model_dump(mode="json"), indent=2))
            tmp.replace(path)  # 원자적 교체 — 같은 공고를 다시 수집해도 행이 늘지 않는다

        async with self._lock:
            await asyncio.to_thread(_write)

    async def get(self, platform: str, platform_job_id: str) -> JobRecord | None:
        return await asyncio.to_thread(self._read, self._path(platform, platform_job_id))

    def _read(self, path: Path) -> JobRecord | None:
        if not path.is_file():
            return None
        return JobRecord.model_validate(json.loads(path.read_text()))

    async def actionable(self) -> list[JobRecord]:
        def _scan() -> list[JobRecord]:
            jobs_dir = self._root / "jobs"
            if not jobs_dir.is_dir():
                return []
            records = (self._read(p) for p in jobs_dir.glob("*.json"))
            return [r for r in records if r and r.applicability and r.applicability.actionable]

        return await asyncio.to_thread(_scan)


class FileAttemptRepository:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = asyncio.Lock()

    def _path(self, application_id: str) -> Path:
        safe = application_id.replace("/", "_")
        return self._root / "attempts" / f"{safe}.json"

    def _read(self, path: Path) -> list[ApplicationAttempt]:
        if not path.is_file():
            return []
        raw = json.loads(path.read_text())
        return [ApplicationAttempt.model_validate(r) for r in raw]

    async def record(self, attempt: ApplicationAttempt) -> None:
        path = self._path(attempt.application_id)

        def _write() -> None:
            history = self._read(path)
            for i, existing in enumerate(history):
                if existing.attempt == attempt.attempt:
                    history[i] = attempt
                    break
            else:
                history.append(attempt)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps([h.model_dump(mode="json") for h in history], indent=2))
            tmp.replace(path)  # 원자적 교체

        async with self._lock:
            await asyncio.to_thread(_write)

    async def history(self, application_id: str) -> list[ApplicationAttempt]:
        return await asyncio.to_thread(self._read, self._path(application_id))


class FileScheduleConfigRepository:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = asyncio.Lock()

    def _path(self, target: str) -> Path:
        safe = target.replace("/", "_")
        return self._root / "schedule_config" / f"{safe}.json"

    async def get(self, target: str) -> ScheduleConfig | None:
        return await asyncio.to_thread(self._read, self._path(target))

    def _read(self, path: Path) -> ScheduleConfig | None:
        if not path.is_file():
            return None
        return ScheduleConfig.model_validate(json.loads(path.read_text()))

    async def set(self, config: ScheduleConfig) -> None:
        path = self._path(config.target)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(config.model_dump(mode="json"), indent=2))
            tmp.replace(path)  # 원자적 교체

        async with self._lock:
            await asyncio.to_thread(_write)


class FileResumeRepository:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = asyncio.Lock()

    def _path(self, application_id: str) -> Path:
        safe = application_id.replace("/", "_")
        return self._root / "resumes" / f"{safe}.json"

    async def get(self, application_id: str) -> CachedResume | None:
        return await asyncio.to_thread(self._read, self._path(application_id))

    def _read(self, path: Path) -> CachedResume | None:
        if not path.is_file():
            return None
        return CachedResume.model_validate(json.loads(path.read_text()))

    async def save(self, resume: CachedResume) -> None:
        path = self._path(resume.application_id)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(resume.model_dump(mode="json"), indent=2))
            tmp.replace(path)  # 원자적 교체

        async with self._lock:
            await asyncio.to_thread(_write)


class FileUnitOfWork:
    def __init__(self, root: Path) -> None:
        self.applications = FileApplicationRepository(root)
        self.jobs = FileJobRepository(root)
        self.attempts = FileAttemptRepository(root)
        self.schedule_config = FileScheduleConfigRepository(root)
        self.resumes = FileResumeRepository(root)

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
