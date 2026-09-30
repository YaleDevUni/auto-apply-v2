"""가짜 `claude` CLI — stream-json 을 흉내 낸다. 동작은 env 로 고른다(테스트 전용).

FAKE_CLAUDE_MODE: ok · auth · quota · foreign · crash · hang · silent
FAKE_CLAUDE_OUT: 받은 argv·env·cwd·pid 를 적어 둘 디렉터리
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

MODE = os.environ["FAKE_CLAUDE_MODE"]
OUT = Path(os.environ["FAKE_CLAUDE_OUT"])
OURS = ["mcp__auto_apply__navigate", "mcp__auto_apply__snapshot"]
KEYS = (
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
    "MCP_TOOL_TIMEOUT",
    "AUTO_APPLY_RUN_TOKEN",
)


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main() -> None:
    args = sys.argv[1:]
    prompt_file = Path(args[args.index("--system-prompt-file") + 1])
    seen = {
        "argv": args,
        "env": {k: os.environ.get(k) for k in KEYS},
        "cwd": os.getcwd(),
        "system_prompt": prompt_file.read_text(encoding="utf-8"),
    }
    (OUT / "seen.json").write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    if MODE == "silent":  # init 없이 멈춰 있다
        time.sleep(600)
    tools = ["Bash", *OURS] if MODE == "foreign" else OURS
    servers = [{"name": "auto_apply", "status": "connected"}]
    emit({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": servers})
    if MODE == "auth":
        emit({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"})
        sys.exit(1)
    if MODE == "quota":
        emit({"type": "result", "is_error": True, "subtype": "error_max_budget_usd",
              "terminal_reason": "budget_exhausted", "result": ""})  # fmt: skip
        sys.exit(1)
    if MODE == "crash":
        sys.stderr.write("boom: segfault-ish")
        sys.exit(3)
    if MODE == "ok":
        leak = f"{os.environ.get('AUTO_APPLY_RUN_TOKEN')} 주민 900101-1234567"
        usage = {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}
        emit(
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [{"type": "text", "text": leak}],
                    "usage": usage,
                },
            }
        )
        emit({"type": "result", "is_error": False, "result": "끝", "usage": usage})  # fmt: skip
        return
    # hang·foreign: 자식을 하나 띄우고 멈춰 있는다 — 끝낼 때 자식까지 정리되는지 본다
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    (OUT / "pids").write_text(f"{os.getpid()} {child.pid}")
    time.sleep(600)


main()
