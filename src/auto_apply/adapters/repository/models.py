"""SQLAlchemy 테이블 정의.

ARCHITECTURE.md §4 의 ERD 를 그대로 정규화하지 않는다 — port(`ports/repository.py`)가
실제로 요구하는 건 "행 전체를 payload 로 왕복시키는 조회/멱등 upsert"뿐이고, 그건 파일
어댑터(`adapters/repository/file.py`)가 이미 증명한 계약이다. 그래서 각 테이블은
멱등 upsert 의 유니크 키 + 조회에 쓰는 컬럼만 진짜 컬럼으로 두고, 나머지는 DTO 를 그대로
JSONB 에 담는다 (§4 의 `raw`/`spec`/`profile` JSONB 관례와 같은 방식). 스키마가 자주 바뀌는
지금 단계에서 컬럼마다 마이그레이션을 만드는 비용을 피한다.
"""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApplicationStateRow(Base):
    """`PersistState` 이력. §4.1 — `persist_state` activity 만 이 테이블에 쓴다."""

    __tablename__ = "application_state_history"
    __table_args__ = (
        UniqueConstraint("application_id", "workflow_run_id", "state", name="uq_application_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
