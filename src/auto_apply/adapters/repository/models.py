"""SQLite 테이블 정의 (D3). Alembic 리비전(`alembic/versions/`)과 항상 같아야 한다 —
`tests/adapters/test_sqlite_migrations.py` 가 둘을 대조한다.

T0.2 최소 스키마: `applications` · `application_state_history` · `runs`. 가이드·작업 큐·문서
테이블(§A7·§A8·§A9)은 해당 마일스톤에서 리비전을 추가한다. port 가 요구하는 것은 조회 키와
이력 append 뿐이라, 나머지 값은 DTO 를 JSON 으로 그대로 담는다.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApplicationRow(Base):
    """지원 건 1개 = 1행. 최신 상태 스냅샷이라 목록 조회가 이력을 훑지 않는다."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    # 최신 PersistState 전체 — reason·scheduled_at 등 조회 전용 값
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    # 최신 이력 행의 id(= 마지막 전이). "최근 갱신" 정렬 기준 (ports/repository.py list_recent)
    last_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)


class ApplicationStateRow(Base):
    """상태 전이 이력 (§A3 감사 로그). append-only — 전이마다 새 행, `id` 가 순번이다.

    같은 run 안에서도 FILLING↔NEEDS_INPUT 처럼 같은 상태로 되돌아오므로 (run, state) 로 묶지 않는다.
    """

    __tablename__ = "application_state_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class RunRow(Base):
    """에이전트 세션 1회 = 1행 (§A3). 토큰·transcript 등 나머지 컬럼은 M3 에서 추가한다."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)  # fill | revise | submit
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
