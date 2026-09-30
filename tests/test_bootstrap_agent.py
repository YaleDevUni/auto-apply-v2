"""bootstrap 의 fill run 배선 — 런타임 선택 표·앱 포트 주입·브라우저 짝·run 한도 (§A5·§A6, T3.7)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

import auto_apply.bootstrap as bootstrap_pkg
from auto_apply.adapters.agent.anthropic_api import AnthropicApiAgentRuntime
from auto_apply.adapters.agent.claude_cli import ClaudeCliAgentRuntime
from auto_apply.adapters.agent.scripted import ScriptedAgentRuntime
from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.browser.playwright_guarded import PlaywrightGuardedPageDriver
from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.api.mcp import MCP_PATH as API_MCP_PATH
from auto_apply.bootstrap import BrowserPair, StartupError, build_container
from auto_apply.bootstrap.agent import MCP_PATH
from auto_apply.config import Settings
from auto_apply.contracts.agent import AgentLimits
from auto_apply.contracts.browser_tools import ToolError
from auto_apply.contracts.jobs import JobKind

PORT = 49152


def _cfg(tmp_path: Path, **kw) -> Settings:
    base = {"storage": "memory", "repository": "memory", "data_dir": tmp_path}
    return Settings(**(base | kw))


@pytest.mark.parametrize(
    ("provider", "runtime"),
    [
        ("stub", ScriptedAgentRuntime),  # 오프라인 — 브라우저를 띄우지 않는다
        ("anthropic", AnthropicApiAgentRuntime),  # 도구를 같은 프로세스에서(T3.8)
        ("claude_cli", ClaudeCliAgentRuntime),  # 기본(D5)
    ],
)
def test_runtime_is_chosen_by_llm_provider(tmp_path, provider, runtime):
    cfg = _cfg(tmp_path, llm_provider=provider, anthropic_api_key="k")
    c = build_container(cfg, port=PORT)
    assert type(c.agent) is runtime
    assert c.runner._handlers[JobKind.FILL]._runtime is c.agent


def test_cli_runtime_gets_app_port_runs_dir_model_and_human_wait(tmp_path):
    cfg = _cfg(tmp_path, llm_provider="claude_cli", claude_cli_binary="my-claude",
               claude_cli_model="claude-haiku-4-5", human_wait_s=90)  # fmt: skip
    c = build_container(cfg, port=PORT)
    rt = c.agent
    assert isinstance(rt, ClaudeCliAgentRuntime)
    assert rt._mcp_url == f"http://127.0.0.1:{PORT}/mcp" == f"{c.server_origin}{MCP_PATH}"
    assert rt._runs_dir == tmp_path / "runs" == cfg.runs_dir
    assert rt._command == ("my-claude",) and rt._model == "claude-haiku-4-5"
    assert rt._tool_timeout_ms == (90 + 60) * 1000  # ask_user 대기보다 길게
    assert rt._open_token.__self__ is c.run_tokens  # /mcp 가 resolve 하는 그 토큰표


def test_api_runtime_gets_key_model_and_runs_dir_without_a_port(tmp_path):
    """API 런타임은 MCP 를 거치지 않는다 — 앱 포트를 몰라도 조립된다."""
    cfg = _cfg(tmp_path, llm_provider="anthropic", anthropic_api_key="sk-x",
               anthropic_model="claude-opus-5-5")  # fmt: skip
    rt = build_container(cfg).agent
    assert isinstance(rt, AnthropicApiAgentRuntime)
    assert rt._api_key == "sk-x" and rt._model == "claude-opus-5-5"
    assert rt._runs_dir == cfg.runs_dir and rt._transport is None


def test_cli_runtime_without_port_refuses_to_build(tmp_path):
    with pytest.raises(StartupError, match="포트"):
        build_container(_cfg(tmp_path, llm_provider="claude_cli"))


def test_bootstrap_mcp_path_matches_api():
    assert MCP_PATH == API_MCP_PATH


async def test_app_origin_is_forbidden_to_the_agent_browser(tmp_path):
    """앱의 실제 포트가 금지 출처로 — 루프백 이름이 달라도, 브라우저를 띄우기 전에 거부 (§A5)."""
    cfg = _cfg(tmp_path, web_cors_origin="http://localhost:5173")
    # 대역 브라우저 — 금지가 빠지면 실제 Chrome 대신 "없는 사이트" 로 실패해 드러난다
    host = FakeBrowserHost(tmp_path / "chrome-profile")
    pair = BrowserPair(host, FakeGuardedPageDriver(host, {}))
    c = build_container(cfg, port=PORT, browser=pair)
    assert c.server_origin == f"http://127.0.0.1:{PORT}"
    record = await c.applications.create("https://jobs.example.com/1")
    box = c.toolbox(record, "run_x", {})
    for url in (f"http://127.0.0.1:{PORT}/api/session", f"http://localhost:{PORT}/",
                "http://localhost:5173/"):  # fmt: skip
        res = await box.call("navigate", {"url": url})
        assert not res.ok and res.error is ToolError.FORBIDDEN_URL, url
    assert host.running is False


def test_without_port_only_web_origin_is_forbidden(tmp_path):
    c = build_container(_cfg(tmp_path, web_cors_origin="http://localhost:5173"))
    assert c.server_origin is None


def test_browser_host_and_guarded_driver_are_one_pair(tmp_path):
    """가드는 에이전트가 쓰는 바로 그 브라우저에 설치돼야 한다 (T2.4 이관)."""
    c = build_container(_cfg(tmp_path), port=PORT)
    assert isinstance(c.browser, PlaywrightBrowserHost)
    assert isinstance(c.pages, PlaywrightGuardedPageDriver)
    assert c.pages._host is c.browser


def test_run_limits_are_settings(tmp_path):
    c = build_container(_cfg(tmp_path, agent_max_tool_calls=7, agent_max_seconds=900), port=PORT)
    fill = c.runner._handlers[JobKind.FILL]
    assert fill._limits == AgentLimits(max_tool_calls=7, max_seconds=900)
    with pytest.raises(ValidationError, match="HUMAN_WAIT_S"):
        Settings(human_wait_s=600, agent_max_seconds=600)
    with pytest.raises(ValidationError):
        Settings(agent_max_tool_calls=0)


def test_llm_provider_defaults_to_claude_cli_with_current_model(monkeypatch):
    """D5 — 기본은 로그인된 Claude Code 구독. 게이트(conftest)는 LLM_PROVIDER=stub 로 돈다."""
    monkeypatch.delenv("LLM_PROVIDER")
    cfg = Settings(_env_file=None)
    assert cfg.llm_provider == "claude_cli"
    assert cfg.claude_cli_model == cfg.anthropic_model == "claude-sonnet-5-5"


def test_bootstrap_modules_stay_small():
    pkg = Path(bootstrap_pkg.__file__).parent
    sizes = {p.name: len(p.read_text(encoding="utf-8").splitlines()) for p in pkg.glob("*.py")}
    assert len(sizes) >= 4
    assert all(n <= 200 for n in sizes.values()), sizes
