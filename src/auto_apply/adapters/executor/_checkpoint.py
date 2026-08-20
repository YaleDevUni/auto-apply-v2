"""SUPERVISED 페이지 경계 체크포인트 대기 (§ supervised-checkpoint-design).

port 가 아니라 `Notifier`/`CheckpointStore`/`BlobStore`/`IdGen` 을 조합하는 클래스다 —
executor 어댑터(`playwright.py`)가 액션 실행 전 이 안에서 멈춰 선다. 실제 시간을 기다리는
코드라 activity 안에서만 쓸 수 있다(workflow 결정성 규칙과 무관 — 여기는 activity 다).
"""

import asyncio
import time
from collections.abc import Callable
from datetime import timedelta

from auto_apply.contracts.dto import DecisionRequest
from auto_apply.domain.errors import CheckpointDeclined
from auto_apply.ports.checkpoint_store import CheckpointStore
from auto_apply.ports.clock import IdGen
from auto_apply.ports.notifier import Notifier
from auto_apply.ports.storage import BlobStore


class CheckpointWaiter:
    def __init__(
        self,
        notifier: Notifier,
        store: CheckpointStore,
        blob: BlobStore,
        idgen: IdGen,
        *,
        timeout: timedelta,
        poll_interval: timedelta,
    ) -> None:
        self._notifier = notifier
        self._store = store
        self._blob = blob
        self._idgen = idgen
        self._timeout = timeout
        self._poll_interval = poll_interval

    async def wait(
        self,
        *,
        application_id: str,
        attempt: int,
        label: str,
        screenshot: bytes,
        heartbeat: Callable[[str], None] | None,
    ) -> None:
        key = f"checkpoints/{application_id}/{attempt}/{self._idgen.new_id('chk')}.png"
        await self._blob.put(key, screenshot, content_type="image/png")

        ticket = await self._notifier.request_decision(
            DecisionRequest(
                application_id=application_id,
                workflow_id=f"application-{application_id}",
                title=f"{application_id} — {label} 체크포인트 승인",
                summary=f"attempt {attempt}: 다음 단계로 진행하려면 승인하세요.",
                artifact_url=key,
                checkpoint=True,
            )
        )

        deadline = time.monotonic() + self._timeout.total_seconds()
        poll_s = self._poll_interval.total_seconds()
        while True:
            decision = await self._store.get_decision(ticket.nonce)
            if decision is True:
                return
            if decision is False:
                raise CheckpointDeclined(f"{label}: 체크포인트가 거절됐다")
            if time.monotonic() >= deadline:
                raise CheckpointDeclined(f"{label}: 체크포인트 응답 시간 초과")
            if heartbeat is not None:
                heartbeat(f"waiting for checkpoint approval: {label}")
            await asyncio.sleep(poll_s)
