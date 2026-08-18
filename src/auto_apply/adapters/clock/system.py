import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdGen:
    def new_id(self, prefix: str = "") -> str:
        raw = uuid.uuid4().hex[:16]
        return f"{prefix}_{raw}" if prefix else raw
