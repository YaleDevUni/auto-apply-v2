"""SQLite 테이블 정의 (D3). Alembic 리비전(`alembic/versions/`)과 항상 같아야 한다 —
`tests/adapters/test_sqlite_migrations.py` 가 둘을 대조한다.

0001(T0.2): `applications` · `application_state_history` · `runs`.
0002(T1.1): 프로필·지식베이스(§A7) — `profiles` · `experiences` · `answers` · `documents`.
0003(T3.1): v3 상태기계(§A3) — 지원 건 `url`·`domain`·`submit_mode`, 이력 `run_id` nullable,
`runs` 결과·토큰·transcript.
가이드·작업 큐(§A8·§A9)는 해당 마일스톤에서 리비전을 추가한다. 조회 키만 컬럼으로 두고 나머지는
DTO 를 JSON 으로 그대로 담는다 — 필드가 늘어도 마이그레이션 없이 pydantic 기본값으로 흡수된다.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApplicationRow(Base):
    """지원 건 1개 = 1행. 최신 상태 스냅샷이라 목록 조회가 이력을 훑지 않는다."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    # 최신 PersistState 전체 — reason·at 등 조회 전용 값
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    # 최신 이력 행의 id(= 마지막 전이). "최근 갱신" 정렬 기준 (ports/repository.py list_recent)
    last_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    # 0003 이전 행을 위한 server_default. 새 행은 저장소가 항상 값을 넣는다.
    url: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    domain: Mapped[str] = mapped_column(String, nullable=False, server_default="", index=True)
    submit_mode: Mapped[str] = mapped_column(String, nullable=False, server_default="dry_run")


class ApplicationStateRow(Base):
    """상태 전이 이력 (§A3 감사 로그). append-only — 전이마다 새 행, `id` 가 순번이다.

    같은 run 안에서도 FILLING↔NEEDS_INPUT 처럼 같은 상태로 되돌아오므로 (run, state) 로 묶지 않는다.
    """

    __tablename__ = "application_state_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # 사람 조작(trigger·approve·cancel) 전이는 run 이 없다.
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class RunRow(Base):
    """에이전트 세션 1회 = 1행 (§A3). kind·status 값은 domain/enums `RunKind`·`RunStatus`."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)  # fill | revise | submit
    status: Mapped[str] = mapped_column(String, nullable=False)  # running|done|failed|interrupted
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # run 이 남긴 지원 건 상태(예: awaiting_approval·needs_login) — 끝나기 전엔 비어 있다.
    result: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # DATA_DIR 기준 상대 경로 (§A6 run 디렉터리) — 설치 위치가 바뀌어도 유효하게.
    transcript_path: Mapped[str | None] = mapped_column(String, nullable=True)


# ── 프로필 · 지식베이스 (§A7, 0002) ──────────────────────────────────────────
# 시각 컬럼은 UTC naive 로 저장한다 (SQLite DateTime 은 tzinfo 를 버린다) — 변환은 저장소 몫.


class ProfileRow(Base):
    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)  # Profile


class ExperienceRow(Base):
    __tablename__ = "experiences"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    # 목록(= 이력서 등장) 순서. 새 행은 사용자별 max+1, 수정해도 유지한다.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)  # Experience


class AnswerRow(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("user_id", "question_key", name="uq_answers_user_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    question_key: Mapped[str] = mapped_column(String, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # FK 를 걸지 않는다 — 답은 지원 건이 지워져도 남아야 하고, 출처는 추적용 메모다.
    source_application_id: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fact_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
