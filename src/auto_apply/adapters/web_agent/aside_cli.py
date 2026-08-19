"""Aside(범용 브라우저 에이전트) CLI 를 subprocess 로 구동하는 WebAgentExecutor 구현.

(ARCHITECTURE.md §2.4b) `adapters/llm/claude_code_cli.py`(`ClaudeCodeCliLLM`)와 같은 패턴 —
로그인된 로컬 구독으로 CLI 를 headless 호출하고, stdout 텍스트로 실패를 분류한다.

실측(2026-08-20, aside 1.26.810.1915)한 것과 아직 못한 것을 구분해서 적는다:
  - `aside exec "<프롬프트>"`는 실제 clickable submit 버튼 앞에서도 "누르지 마라" 지시를
    지킨다(httpbin.org/forms/post). `--session <id>`로 이어서 "이제 제출해라"를 보내면 같은
    세션에서 실제 제출까지 완주한다.
  - `--session` 없이 부르면 지금 사람이 포커스한 탭에 붙는다(실측: 관련 없는 탭을 잡음) —
    그래서 fill()의 첫 호출(세션을 새로 여는 호출) 이후로는 반드시 `--session`을 명시해야
    한다. 같은 이유로 `fill()` 안에서 탭을 미리 열어 세션 id를 확보하기 전에는 로그인/채우기
    프롬프트를 보내지 않는다(§ 아래 `fill()` 구현 참고).
  - `aside repl`(별도 명령)은 LLM 을 거치지 않고 JS 를 직접 실행한다(응답 61ms). 하지만
    `--session` 옵션이 없어서 `exec`가 만든 세션과 프로세스 경계를 못 넘는다 — 그래서 여기서는
    repl 코드를 직접 부르지 않고, `exec` 프롬프트 **안에서** 모델에게 repl 도구를 쓰라고
    지시한다(모델이 내부적으로 그 repl 호출을 함). 파일 경로만 프롬프트에 넣고 실제 자격증명
    값은 절대 프롬프트 텍스트에 넣지 않는다 — the-internet.herokuapp.com 공개 테스트 계정으로
    로그인 성공 + 값이 출력 어디에도 안 찍히는 것까지 실측 확인.
  - Aside 는 `~/.aside/u/<account>/sessions/<id>/attachments/` 밖의 파일 접근을 샌드박스로
    막는다("Path escapes Project and session roots") — 그래서 자격증명 파일은 반드시 그 경로
    안에 써야 한다.
  - **아직 실측 못한 것**: 로그인 실패/CAPTCHA 조우 시 Aside 가 실제로 어떤 문구를 stdout 에
    남기는지는 라이브로 재현하지 못했다. 아래 `_LOGIN_FAIL_PATTERN`/`_CAPTCHA_PATTERN` 은
    `claude_code_cli.py`의 실측 패턴만큼 신뢰할 수 없는 최선 추정이다 — 실패 사례를 관찰하는
    대로 갱신해야 한다.
  - **세션 id 추출은 공식 API가 아니라 실측 기반 휴리스틱이다**: `aside exec`가 세션 id를
    구조화로 돌려주는 옵션을 `--help`에서 찾지 못해서, 호출 전후 세션 디렉터리 목록을 diff
    해서 새로 생긴 디렉터리를 세션으로 간주한다. 동시에 다른 프로세스가 세션을 열면 깨질 수
    있어 platform/company 당 동시성 1(browser 큐 정책, ARCHITECTURE.md "Task Queue를 3개로
    나누는 이유")이 이 어댑터에도 그대로 적용돼야 한다.
"""

import asyncio
import json
import re
from datetime import UTC
from pathlib import Path

import structlog

from auto_apply.contracts.resume_content import AssembledResume
from auto_apply.contracts.web_agent import (
    WebAgentFillResult,
    WebAgentSessionRef,
    WebAgentSubmitResult,
    WebAgentTask,
)
from auto_apply.domain.enums import AttemptOutcome
from auto_apply.domain.errors import (
    CaptchaEncountered,
    WebAgentExecutionError,
    WebAgentLoginFailed,
    WebAgentTaskFailed,
)
from auto_apply.ports.clock import Clock
from auto_apply.ports.credentials import CredentialSource
from auto_apply.ports.storage import BlobStore

logger = structlog.get_logger()

# 최선 추정 — 모듈 docstring 참고. claude_code_cli.py의 패턴만큼 실측되지 않았다.
_CAPTCHA_PATTERN = re.compile(r"captcha|recaptcha|보안\s*문자|자동입력\s*방지", re.IGNORECASE)
_LOGIN_FAIL_PATTERN = re.compile(
    r"login failed|log ?in failed|incorrect (password|username|credentials)"
    r"|invalid (password|username|credentials)|인증에?\s*실패|비밀번호가\s*(틀렸|맞지\s*않)"
    r"|계정을\s*찾을\s*수\s*없",
    re.IGNORECASE,
)


def _summarize_resume(resume: AssembledResume) -> str:
    """AssembledResume 을 에이전트 프롬프트에 넣을 평문 요약으로 직렬화한다.

    PdfRenderer(§2.3)처럼 이 모양만 알면 되고, 실제 폼 필드가 뭔지는 모른다 — 필드 매칭은
    에이전트(모델)가 스냅샷을 보고 알아서 한다.
    """
    lines = [f"이름: {resume.name}"]
    if resume.email:
        lines.append(f"이메일: {resume.email}")
    if resume.phone:
        lines.append(f"전화: {resume.phone}")
    lines.append(f"자기소개/요약: {resume.summary}")
    for h in resume.highlights:
        lines.append(f"- 핵심 강점: {h.text}")
    for entry in resume.career:
        lines.append(f"[경력] {entry.company} ({entry.period or '기간 미상'})")
        for block in entry.blocks:
            lines.append(f"  {block.title} ({block.period or ''})")
            for b in block.bullets:
                lines.append(f"    - {b.text}")
    for block in resume.projects:
        lines.append(f"[프로젝트] {block.title} ({block.period or ''})")
        for b in block.bullets:
            lines.append(f"  - {b.text}")
    for edu in resume.education:
        lines.append(f"[학력] {edu.school} {edu.degree} ({edu.period}) {edu.status}")
    if resume.skills:
        lines.append(f"스킬: {', '.join(resume.skills)}")
    for lang in resume.languages:
        lines.append(f"[언어] {lang.name} — {lang.level}")
    return "\n".join(lines)


class AsideCliExecutor:
    """`ports/web_agent.py`의 구현. `aside` CLI(로그인된 구독)를 headless 로 호출한다."""

    def __init__(
        self,
        credentials: CredentialSource,
        store: BlobStore,
        clock: Clock,
        *,
        binary: str = "aside",
        account: str | None = None,
        timeout_seconds: float = 240.0,
        sessions_root: Path | None = None,
    ) -> None:
        self._credentials = credentials
        self._store = store
        self._clock = clock
        self._binary = binary
        self._account = account
        self._timeout_seconds = timeout_seconds
        self._sessions_root = sessions_root or (
            Path.home() / ".aside" / "u" / (account or "0") / "sessions"
        )

    async def fill(self, task: WebAgentTask) -> WebAgentFillResult:
        before = self._list_session_dirs()
        open_prompt = (
            f"Open a new tab to {task.apply_url}. Do not fill in anything yet. "
            "Just report once the page has finished loading."
        )
        stdout = await self._run(open_prompt)
        self._raise_on_failure_signature(stdout)

        session_dir = self._diff_new_session(before)
        if session_dir is None:
            raise WebAgentExecutionError("새 Aside 세션 디렉터리를 찾지 못했다(세션 id 추출 실패)")
        session_id = self._session_id_from_dir(session_dir)
        attachments = self._ensure_attachments_dir(session_dir)

        login_step = ""
        cred_path: Path | None = None
        if task.credential_key:
            cred = await self._credentials.get(task.credential_key)
            if cred is None:
                raise WebAgentLoginFailed(f"credential 큐에 '{task.credential_key}' 키가 없다")
            cred_path = attachments / "login.json"
            cred_path.write_text(json.dumps({"username": cred.username, "password": cred.password}))
            login_step = self._login_step_instruction(cred_path)

        screenshot_path = attachments / "fill_screenshot.png"
        fill_prompt = self._build_fill_prompt(
            task, login_step=login_step, screenshot_path=screenshot_path
        )
        try:
            stdout = await self._run(fill_prompt, session_id=session_id)
        finally:
            # 로그인 직후가 아니라 fill 프롬프트가 끝난 뒤 지운다 — 프롬프트 실행 도중 파일이
            # 필요하다.
            if cred_path is not None:
                cred_path.unlink(missing_ok=True)
        self._raise_on_failure_signature(stdout, credential_key=task.credential_key)

        screenshot_key = await self._upload_screenshot(task.application_id, screenshot_path)
        return WebAgentFillResult(
            session=WebAgentSessionRef(session_id=session_id),
            screenshot_key=screenshot_key,
            summary=f"{task.apply_url} 채움 — 제출 전 확인 필요",
        )

    async def submit(self, session: WebAgentSessionRef) -> WebAgentSubmitResult:
        prompt = (
            "승인됐다. 지금까지 채워둔 값을 다시 채우지 말고, 최종 제출/전송/지원완료 버튼을 "
            "눌러 지원을 완료해라. 완료되면 결과 페이지 상태를 보고해라."
        )
        stdout = await self._run(prompt, session_id=session.session_id)
        self._raise_on_failure_signature(stdout)
        return WebAgentSubmitResult(
            outcome=AttemptOutcome.SUCCEEDED,
            submitted_at=self._clock.now().astimezone(UTC),
            detail=stdout[-2000:],
        )

    # ── 세션 id 추출 (실측 기반 휴리스틱, 모듈 docstring 참고) ──────────────────
    def _list_session_dirs(self) -> set[str]:
        if not self._sessions_root.is_dir():
            return set()
        return {p.name for p in self._sessions_root.iterdir() if p.is_dir()}

    def _diff_new_session(self, before: set[str]) -> str | None:
        new = self._list_session_dirs() - before
        if len(new) != 1:
            logger.warning("aside_session_diff_ambiguous", before=len(before), new=sorted(new))
        return next(iter(new), None)

    @staticmethod
    def _session_id_from_dir(dir_name: str) -> str:
        # 관찰된 형식: "YYYY-MM-DD_<shortid>". --session 은 <shortid>만 받는다(실측).
        return dir_name.split("_", 1)[-1] if "_" in dir_name else dir_name

    def _ensure_attachments_dir(self, session_dir: str) -> Path:
        attachments = self._sessions_root / session_dir / "attachments"
        attachments.mkdir(parents=True, exist_ok=True)
        return attachments

    # ── 프롬프트 구성 ─────────────────────────────────────────────────────
    def _login_step_instruction(self, cred_path: Path) -> str:
        return (
            f"repl 도구를 써서 이 파일에서 로그인 정보를 읽어라: {cred_path} "
            "(예: const creds = JSON.parse(await fs.readFile('...', 'utf8'))). "
            "그 값을 절대 콘솔에 출력하거나 응답에 반복하지 말고, 현재 페이지에서 아이디/이메일과 "
            "비밀번호 입력란, 로그인 버튼을 찾아 creds.username / creds.password 로 채우고 "
            "로그인해라. 로그인 성공 여부만 보고해라."
        )

    def _build_fill_prompt(
        self, task: WebAgentTask, *, login_step: str, screenshot_path: Path
    ) -> str:
        resume_summary = _summarize_resume(task.resume)
        parts: list[str] = [login_step] if login_step else []
        essay_block = ""
        if task.essay_answers:
            qa = "\n".join(f"- {q}: {a}" for q, a in task.essay_answers.items())
            essay_block = f"\n\n자기소개서/지원동기 문항에는 다음 답변을 그대로 채워라:\n{qa}"
        parts.append(
            "현재 열려있는 탭의 지원 폼에 아래 이력서 내용을 각 필드에 맞게 채워라. "
            "웹폼 입력란이 있으면 직접 입력하고, 파일 업로드만 지원하면 업로드는 건너뛰어도 "
            f"된다(별도로 처리한다):\n{resume_summary}{essay_block}\n\n"
            "다 채운 뒤 **절대 제출/전송/완료/지원하기 버튼을 누르지 마라.** "
            f"채운 상태 그대로 전체 페이지 스크린샷을 정확히 이 경로에 저장해라: "
            f"{screenshot_path}\n완료되면 무엇을 채웠는지만 요약해서 보고해라."
        )
        return "\n\n".join(parts)

    # ── subprocess ────────────────────────────────────────────────────────
    async def _run(self, prompt: str, *, session_id: str | None = None) -> str:
        args = [self._binary, "exec"]
        if self._account:
            args += ["--account", self._account]
        if session_id:
            args += ["--session", session_id]
        args.append(prompt)

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise WebAgentExecutionError(f"aside CLI 타임아웃({self._timeout_seconds}s)") from e

        text = stdout.decode(errors="replace")
        if proc.returncode != 0:
            raise WebAgentExecutionError(
                f"aside CLI 종료 코드 {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
            )
        return text

    def _raise_on_failure_signature(
        self, output: str, *, credential_key: str | None = None
    ) -> None:
        if _CAPTCHA_PATTERN.search(output):
            raise CaptchaEncountered("Aside 실행 중 CAPTCHA로 추정되는 신호 감지")
        if credential_key is not None and _LOGIN_FAIL_PATTERN.search(output):
            raise WebAgentLoginFailed(f"'{credential_key}' 계정으로 로그인 실패로 추정됨")

    @staticmethod
    def _read_screenshot(path: Path) -> bytes:
        if not path.is_file():
            raise WebAgentTaskFailed(f"스크린샷 파일이 생성되지 않았다: {path}", screenshot_key="")
        return path.read_bytes()

    async def _upload_screenshot(self, application_id: str, path: Path) -> str:
        data = self._read_screenshot(path)
        key = f"application-artifacts/{application_id}/web-agent-fill.png"
        await self._store.put(key, data, content_type="image/png")
        return key
