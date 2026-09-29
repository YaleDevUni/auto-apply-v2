"""서비스 테스트용 결정론적 Clock·IdGen."""

from datetime import UTC, datetime


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, tzinfo=UTC)


class SeqIds:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self, prefix: str = "") -> str:
        self.n += 1
        return f"{prefix}_{self.n}"
