"""Composition root — 구현체 선택과 조립이 존재하는 유일한 패키지 (§A2).

여기(bootstrap 패키지) 말고 어디서도 어댑터를 생성하지 않는다. import-linter 가 이를 강제한다.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.extract.pdf_docx import PdfDocxTextExtractor
from auto_apply.adapters.facts.repository import RepositoryFactSource
from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.profile.repository import RepositoryProfileSource
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.bootstrap.adapters import build_guide, build_llm, build_store, build_uow
from auto_apply.bootstrap.agent import (
    BrowserPair,
    agent_limits,
    build_agent_runtime,
    build_browser,
    forbidden_origins,
    server_origin,
)
from auto_apply.bootstrap.data import ensure_session_token
from auto_apply.config import Settings
from auto_apply.contracts.dto import ApplicationRecord
from auto_apply.contracts.jobs import JobKind
from auto_apply.ports.agent import AgentRuntime
from auto_apply.ports.browser import BrowserHost, GuardedPageDriver
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.facts import FactSource
from auto_apply.ports.guide import GuideSource
from auto_apply.ports.human_gate import HumanGate
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.pdf import PdfRenderer
from auto_apply.ports.profile import ProfileSource
from auto_apply.ports.repository import UnitOfWork
from auto_apply.ports.resume import ResumeGenerator, ResumeReviewer
from auto_apply.ports.storage import BlobStore
from auto_apply.ports.text_extract import DocumentTextExtractor
from auto_apply.runner.fill import FillRunHandler, ToolboxFactory
from auto_apply.runner.fill_reentry import FillReentry, HeldAnswers
from auto_apply.runner.job_runner import JobRunner
from auto_apply.services.application import ApplicationService
from auto_apply.services.browser_toolbox import BrowserToolbox
from auto_apply.services.document import DocumentService
from auto_apply.services.profile import DEFAULT_USER_ID, ProfileService
from auto_apply.services.profile_drafts import ProfileDraftService
from auto_apply.services.run_artifacts import RunArtifacts
from auto_apply.services.run_tokens import RunTokens
from auto_apply.services.uploads import UploadService


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    idgen: IdGen
    store: BlobStore
    llm: LLMClient
    uow: Callable[[], UnitOfWork]
    generator: ResumeGenerator
    reviewer: ResumeReviewer
    pdf: PdfRenderer
    facts: FactSource
    profile: ProfileSource
    guide: GuideSource
    documents: DocumentService
    profiles: ProfileService
    uploads: UploadService
    drafts: ProfileDraftService
    # 지원 건 상태의 유일한 쓰기 통로 (§A3). API 와 러너가 같은 인스턴스를 쓴다.
    applications: ApplicationService
    runner: JobRunner
    # 첫 사용 때 뜨고 앱 종료 때 닫힌다(api/main.py lifespan).
    # `pages` 는 이 호스트와 짝이다(BrowserPair — 가드가 에이전트의 바로 그 브라우저에)
    browser: BrowserHost
    pages: GuardedPageDriver
    # run 이 사람(로그인·CAPTCHA·질문)을 기다리는 통로 — UI 가 pending()·answer() 한다 (§A5)
    human_gate: HumanGate
    # 답이 늦게 온 ask_user 질문 → 재진입 fill run (D8·D10). UI 연결은 M4
    reentry: FillReentry
    # 변경 API 가 요구하는 설치별 토큰 (§A10). 웹은 `GET /api/session` 으로 받는다.
    session_token: str
    # run 별 MCP 토큰 (§A5·§A10) — CLI 런타임이 run() 안에서 open 한다(T3.6), /mcp 가 resolve 한다
    run_tokens: RunTokens
    # fill run 을 모는 에이전트 (§A6, llm_provider 로 고른다)와 그 run 의 도구 상자 조립
    agent: AgentRuntime
    toolbox: ToolboxFactory
    # 앱이 실제로 받는 출처(`http://127.0.0.1:<port>`) — 포트를 모르는 테스트 전송이면 None
    server_origin: str | None


def build_container(
    cfg: Settings,
    *,
    port: int | None = None,
    browser: BrowserPair | None = None,
    agent: AgentRuntime | None = None,
) -> Container:
    """`port` 는 앱이 바인드한 실제 포트(§A1). `browser`·`agent` 는 테스트 대역 자리 — 설정 선택지가
    아니다(대역 브라우저는 등록한 가짜 사이트만 연다)."""
    idgen = UuidIdGen()
    clock = SystemClock()
    store = build_store(cfg)
    llm = build_llm(cfg)
    uow = build_uow(cfg)
    # 프로필·경험은 repository 에 있다 (§A7) — 이력서 파이프라인은 같은 UoW 로 읽는다.
    facts: FactSource = RepositoryFactSource(uow)
    profile: ProfileSource = RepositoryProfileSource(uow)
    guide = build_guide(cfg)
    generator = SimpleResumeGenerator(
        llm,
        idgen,
        facts,
        profile,
        guide,
        max_project_blocks=cfg.resume_max_project_blocks,
        max_career_blocks_per_entity=cfg.resume_max_career_blocks_per_entity,
    )
    reviewer = SimpleResumeReviewer(facts)
    # §A7: WeasyPrint 는 제거했고 Chrome `page.pdf()` 구현은 M5 에서 같은 port 로 붙인다.
    pdf = StubPdfRenderer(store)
    # 대역(FakeTextExtractor)은 등록한 바이트만 읽는 테스트 전용이라 설정 선택지로 두지 않는다.
    extractor: DocumentTextExtractor = PdfDocxTextExtractor()
    uploads = UploadService(
        uow, store, extractor, clock, idgen, max_document_bytes=cfg.document_max_bytes
    )
    applications = ApplicationService(uow, clock, idgen)
    pair = browser or build_browser(cfg)
    human_gate = InMemoryHumanGate()
    origin = server_origin(port)
    forbidden = forbidden_origins(cfg, origin)
    run_tokens = RunTokens()
    runtime = agent or build_agent_runtime(cfg, run_tokens, origin)
    profiles = ProfileService(uow, clock, idgen)
    held = HeldAnswers()  # 가린 답은 프로세스 메모리에만 (절대 규칙 5)

    def toolbox(
        record: ApplicationRecord, run_id: str, hidden: Mapping[str, str]
    ) -> BrowserToolbox:
        return BrowserToolbox(
            pair.host, pair.pages, uploads, human_gate=human_gate, human_wait_s=cfg.human_wait_s,
            answers=profiles, held_answers=hidden, user_id=DEFAULT_USER_ID,
            application_id=record.application_id, run_id=run_id, forbidden_origins=forbidden,
        )  # fmt: skip

    artifacts = RunArtifacts(store)
    fill = FillRunHandler(
        uow, applications, artifacts, runtime, toolbox, profile, clock, idgen,
        limits=agent_limits(cfg), answers=profiles, held=held,
    )  # fmt: skip
    # revise·submit·generate 는 이후 카드가 등록한다 — 없는 kind 는 FAILED.
    runner = JobRunner(uow, applications, clock, idgen, handlers={JobKind.FILL: fill})
    return Container(
        settings=cfg,
        clock=clock,
        idgen=idgen,
        store=store,
        llm=llm,
        uow=uow,
        generator=generator,
        reviewer=reviewer,
        pdf=pdf,
        facts=facts,
        profile=profile,
        guide=guide,
        documents=DocumentService(generator, reviewer, pdf),
        profiles=profiles,
        uploads=uploads,
        drafts=ProfileDraftService(uow, store, uploads, extractor, llm, clock, idgen),
        applications=applications,
        runner=runner,
        browser=pair.host,
        pages=pair.pages,
        human_gate=human_gate,
        reentry=FillReentry(uow, applications, artifacts, profiles, held, runner.enqueue),
        session_token=ensure_session_token(cfg.session_token_path),
        run_tokens=run_tokens,
        agent=runtime,
        toolbox=toolbox,
        server_origin=origin,
    )
