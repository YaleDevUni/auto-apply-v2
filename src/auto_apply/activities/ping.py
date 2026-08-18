"""Activity 구현 = port 주입 지점 (ARCHITECTURE.md §11.3).

클래스로 만들고 생성자에서 port 를 받는다. worker 에는 '바인딩된 메서드' 를 등록한다.
"""

from collections.abc import Callable
from typing import Any

from temporalio import activity

from auto_apply.ports.clock import Clock
from auto_apply.ports.storage import BlobStore


class PingActivities:
    def __init__(self, clock: Clock, store: BlobStore) -> None:
        self._clock = clock
        self._store = store

    @activity.defn(name="ping")
    async def ping(self, message: str) -> str:
        key = f"smoke/{activity.info().workflow_id}.txt"
        await self._store.put(key, message.encode(), content_type="text/plain")
        return f"pong:{message}@{self._clock.now().isoformat()}"

    def all(self) -> list[Callable[..., Any]]:
        """worker 등록용. 새 activity 를 추가하면 여기에도 넣는다."""
        return [self.ping]
