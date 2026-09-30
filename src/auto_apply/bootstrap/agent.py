"""fill run 조립 — 브라우저 짝·에이전트 런타임 선택·run 한도 (§A5·§A6).

앱의 실제 출처(`--port 0` 이면 OS 가 고른 포트)가 두 곳에 **같은 값으로** 들어간다:
BrowserToolbox 의 금지 출처(자동화 브라우저가 앱의 승인 API 를 같은 출처로 열지 못하게, §A5)와
CLI 런타임의 MCP URL.
"""

from dataclasses import dataclass

from auto_apply.adapters.agent.anthropic_api import AnthropicApiAgentRuntime
from auto_apply.adapters.agent.claude_cli import ClaudeCliAgentRuntime
from auto_apply.adapters.agent.scripted import ScriptedAgentRuntime
from auto_apply.adapters.browser.playwright_guarded import PlaywrightGuardedPageDriver
from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.bootstrap.data import StartupError
from auto_apply.config import Settings
from auto_apply.contracts.agent import AgentLimits
from auto_apply.ports.agent import AgentRuntime
from auto_apply.ports.browser import BrowserHost, GuardedPageDriver
from auto_apply.services.run_tokens import RunTokens

# §A10: 바인드는 127.0.0.1 고정. api/mcp.py 의 경로와 같아야 한다(bootstrap 은 api 를 못 본다 —
# tests/test_bootstrap_agent.py 가 둘을 대조한다).
LOOPBACK = "127.0.0.1"
MCP_PATH = "/mcp"


@dataclass(frozen=True, slots=True)
class BrowserPair:
    """브라우저 호스트와 **그 호스트의 페이지만** 다루는 가드 달린 드라이버 (T2.4).

    짝이 어긋나면 SubmitGuard 가 에이전트가 쓰는 브라우저가 아닌 다른 브라우저에 설치된다 — 그래서
    둘은 한 번에 만들고 한 묶음으로만 넘긴다.
    """

    host: BrowserHost
    pages: GuardedPageDriver


def build_browser(cfg: Settings) -> BrowserPair:
    host = PlaywrightBrowserHost(cfg.chrome_profile_dir)
    return BrowserPair(host, PlaywrightGuardedPageDriver(host))


def server_origin(port: int | None) -> str | None:
    """앱이 실제로 받는 출처. 포트를 모르면(테스트 전송) None."""
    return None if port is None else f"http://{LOOPBACK}:{port}"


def forbidden_origins(cfg: Settings, origin: str | None) -> tuple[str, ...]:
    """자동화 브라우저가 열면 안 되는 출처 — 앱 자신과 웹 콘솔 개발 서버 (§A5 navigate)."""
    return tuple(o for o in (origin, cfg.web_cors_origin) if o)


def agent_limits(cfg: Settings) -> AgentLimits:
    return AgentLimits(max_tool_calls=cfg.agent_max_tool_calls, max_seconds=cfg.agent_max_seconds)


def build_agent_runtime(cfg: Settings, run_tokens: RunTokens, origin: str | None) -> AgentRuntime:
    match cfg.llm_provider:
        case "stub":
            # 오프라인 조합이라 브라우저를 띄우지 않는다 — 빈 스크립트라 fill run 은 도구를
            # 하나도 부르지 않고 FAILED 로 닫힌다.
            return ScriptedAgentRuntime()
        case "anthropic":
            # 도구를 같은 프로세스에서 부른다 — MCP·앱 포트가 필요 없다
            return AnthropicApiAgentRuntime(
                cfg.anthropic_api_key, model=cfg.anthropic_model, runs_dir=cfg.runs_dir
            )
        case "claude_cli":
            if origin is None:
                raise StartupError(
                    "LLM_PROVIDER=claude_cli 는 앱 포트를 알아야 한다(MCP URL)"
                    " — create_app(port=...)"
                )
            return ClaudeCliAgentRuntime(
                run_tokens.open,
                mcp_url=f"{origin}{MCP_PATH}",
                runs_dir=cfg.runs_dir,
                human_wait_s=cfg.human_wait_s,
                command=(cfg.claude_cli_binary,),
                model=cfg.claude_cli_model,
            )
