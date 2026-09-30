"""fill run 핸들러 테스트 공용 — 대역 브라우저·사이트·스크립트 걸음·조립 (§A6)."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from auto_apply.adapters.agent.scripted import ScriptedAgentRuntime, ScriptedCall, ScriptStep
from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_effects import SubmitForm
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement
from auto_apply.adapters.human_gate.scripted import HumanStep, ScriptedHumanGate
from auto_apply.adapters.repository.memory import InMemoryDatabase, InMemoryUnitOfWork
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.agent import AgentLimits, ToolReply
from auto_apply.contracts.dto import ApplicationRecord
from auto_apply.contracts.jobs import JobKind, JobRecord, RunRecord
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import ProfileNotFound
from auto_apply.ports.agent import AgentRuntime
from auto_apply.runner.fill import FillRunHandler
from auto_apply.runner.fill_reentry import FillReentry, HeldAnswers
from auto_apply.services.browser_toolbox import BrowserToolbox
from auto_apply.services.profile import ProfileService
from auto_apply.services.run_artifacts import RunArtifacts
from tests.runner.kit import T0, Rig
from tests.toolbox_kit import FakeDocuments

B = "http://jobs.test"
FORM = f"{B}/apply"
LOGIN = f"{B}/login"
NOSIGNAL = f"{B}/nosignal"
FINAL = f"{B}/api/submit"
PROFILE = {"kind": "profile", "key": "name"}


def _submit(name: str, to: str) -> FakeElement:
    return FakeElement(
        "button", name, tag="button", type="submit", in_form=True, default_button=True,
        on_click=(SubmitForm("POST", to, by_click=True),),
    )  # fmt: skip


def sites() -> dict[str, FakeDocument]:
    return {
        FORM: FakeDocument(
            "지원서", [FakeElement("textbox", "이름"), _submit("지원서 제출", FINAL)]
        ),
        LOGIN: FakeDocument(
            "로그인",
            [FakeElement("textbox", "아이디"), FakeElement("textbox", "비밀번호", type="password")],
        ),
        # 마지막 단계 신호 없이 "다음" 이 최종 제출 — 받아들인 위험, L5 가 멈춘다(D17)
        NOSIGNAL: FakeDocument(
            "지원서", [FakeElement("textbox", "이름"), _submit("다음", f"{B}/done")]
        ),
        f"{B}/done": FakeDocument(
            "완료", [FakeElement("status", "지원이 완료되었습니다", tag="p")]
        ),
    }


def ref(name: str) -> str:
    """스크립트 안에서 쓰는 자리표시 — `last_snapshot_ref` 가 바꿔 끼운다."""
    return f"<{name}>"


def step(tool: str, **args: object) -> ScriptStep:
    """마지막 snapshot 답에서 `<이름>` 자리표시를 ref 로 바꿔 부르는 걸음."""

    def resolve(replies: Sequence[ToolReply]) -> ScriptedCall:
        refs: dict[str, str] = {}
        for reply in reversed(replies):
            snap = json.loads(reply.content).get("snapshot")
            if snap is not None:
                refs = {n["name"]: n["ref"] for n in snap["nodes"] if n.get("ref")}
                break
        filled = {
            k: refs.get(v[1:-1], v) if isinstance(v, str) and v.startswith("<") else v
            for k, v in args.items()
        }
        return ScriptedCall(tool, filled)

    return resolve


def fill_form_script(*, final: Sequence[ScriptStep] | None = None) -> list[ScriptStep]:
    """지원서를 열어 이름을 채우고 제출 버튼으로 ready_for_review."""
    review = [step("ready_for_review", submit_ref=ref("지원서 제출"))]
    return [
        ScriptedCall("navigate", {"url": FORM}),
        ScriptedCall("snapshot"),
        step("fill", ref=ref("이름"), value="홍길동", source=PROFILE),
        *(final if final is not None else review),
    ]


class FakeProfiles:
    def __init__(self, profile: Profile | None) -> None:
        self._profile = profile

    async def get(self, user_id: str) -> Profile:
        if self._profile is None or user_id != self._profile.user_id:
            raise ProfileNotFound(user_id)
        return self._profile


class FillRig(Rig):
    def __init__(self, tmp_path: Path, *, human: Sequence[HumanStep] = ()) -> None:
        db = self.db = InMemoryDatabase()
        super().__init__(lambda: InMemoryUnitOfWork(db))
        self.store = InMemoryBlobStore()
        self.artifacts = RunArtifacts(self.store)
        self.host = FakeBrowserHost(tmp_path / "chrome-profile")
        self.driver = FakeGuardedPageDriver(self.host, sites())
        self.gate = ScriptedHumanGate(human)
        self.toolboxes: list[BrowserToolbox] = []
        self.profiles = ProfileService(self.uow, self.clock, self.ids)
        self.held = HeldAnswers()
        self.human_wait_s = 1.0

    def make_toolbox(
        self, record: ApplicationRecord, run_id: str, held: Mapping[str, str]
    ) -> BrowserToolbox:
        box = BrowserToolbox(
            self.host, self.driver, FakeDocuments({}), human_gate=self.gate,
            human_wait_s=self.human_wait_s, answers=self.profiles, held_answers=held,
            user_id="local", application_id=record.application_id, run_id=run_id,
        )  # fmt: skip
        self.toolboxes.append(box)
        return box

    def handler(
        self, runtime: AgentRuntime, *, limits: AgentLimits | None = None, profile=None
    ) -> FillRunHandler:
        return FillRunHandler(
            self.uow, self.apps, self.artifacts, runtime, self.make_toolbox,
            FakeProfiles(profile), self.clock, self.ids, limits=limits,
            answers=self.profiles, held=self.held,
        )  # fmt: skip

    def reentry(self, enqueue) -> FillReentry:
        return FillReentry(self.uow, self.apps, self.artifacts, self.profiles, self.held, enqueue)

    async def queued(self) -> tuple[str, JobRecord]:
        app = await self.app_in(S.QUEUED)
        job = JobRecord(
            job_id="job_fill", kind=JobKind.FILL, application_id=app, created_at=T0, run_after=T0
        )
        return app, job

    async def run_record(self, application_id: str) -> RunRecord:
        history = await self.history(application_id)
        run_id = next(h.run_id for h in history if h.run_id is not None)
        async with self.uow() as u:
            run = await u.runs.get(run_id)
        assert run is not None
        return run

    async def history(self, application_id: str):
        async with self.uow() as u:
            return await u.applications.history(application_id)

    def final_submits(self) -> list[tuple[str, str]]:
        return [s for s in self.driver.sent if s == ("POST", FINAL)]


def scripted(script: Sequence[ScriptStep], **kw: str) -> ScriptedAgentRuntime:
    return ScriptedAgentRuntime(script, **kw)
