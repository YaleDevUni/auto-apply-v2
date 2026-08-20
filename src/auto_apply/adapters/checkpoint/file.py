"""CheckpointStore 실제 구현 — 파일 하나 = 결정 하나 (§ supervised-checkpoint-design).

nonce 발급 프로세스(worker, activity 안에서 폴링)와 승인 프로세스(webhook 서버/리스너)가
갈라져서 프로세스 메모리로는 공유가 안 된다 — `FileApplicationRepository`와 같은 원자적
쓰기 패턴(tmp 파일 write 후 rename)을 그대로 쓴다.
"""

import asyncio
import json
from pathlib import Path


class FileCheckpointStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, nonce: str) -> Path:
        safe = nonce.replace("/", "_")
        return self._root / "checkpoints" / f"{safe}.json"

    async def record_decision(self, nonce: str, *, approved: bool) -> None:
        path = self._path(nonce)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"approved": approved}))
            tmp.replace(path)  # 원자적 교체

        await asyncio.to_thread(_write)

    async def get_decision(self, nonce: str) -> bool | None:
        def _read() -> bool | None:
            path = self._path(nonce)
            if not path.is_file():
                return None
            return bool(json.loads(path.read_text())["approved"])

        return await asyncio.to_thread(_read)
