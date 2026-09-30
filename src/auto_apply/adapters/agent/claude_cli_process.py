"""`claude` 프로세스 띄우기·끝내기 — 자식까지 남기지 않는다 (§A6).

프로세스를 자기 프로세스 그룹(POSIX 세션·Windows 새 그룹)으로 띄워, 끝낼 때 그룹째 죽인다 —
CLI 가 띄운 자식이 고아로 남지 않게. 끝내기는 동기 신호로 먼저 보내고 기다림은 그 뒤다: 취소가
기다림을 끊어도 프로세스는 이미 죽는 중이다.
"""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

STDERR_TAIL = 2000  # 에러 메시지에 붙일 stderr 끝부분(바이트)
KILL_GRACE_S = 3.0  # SIGTERM 뒤 SIGKILL 까지


async def spawn(
    args: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> asyncio.subprocess.Process:
    extra: dict[str, object] = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True
    return await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,  # 첫 메시지는 argv(-p) — 다른 입력을 기다리지 않게
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,  # stream-json 한 줄(snapshot 결과)이 기본 64KiB 를 넘는다
        **extra,  # type: ignore[arg-type]
    )


def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            # 그룹째(/T) 강제(/F) — Windows 에는 그룹 SIGTERM 이 없다
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


async def stop(proc: asyncio.subprocess.Process) -> None:
    """그룹에 SIGTERM → 유예 뒤 SIGKILL. 이미 끝났으면 남은 그룹만 정리한다."""
    _signal_group(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
    except TimeoutError:
        _signal_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
        await proc.wait()
    finally:
        # 리더가 먼저 끝나도 그룹에 남은 자식이 있을 수 있다
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


async def drain_tail(stream: asyncio.StreamReader | None) -> bytes:
    """stderr 를 끝까지 읽어 끝부분만 남긴다 — 안 읽으면 파이프가 차서 CLI 가 멈춘다."""
    if stream is None:
        return b""
    tail = b""
    while chunk := await stream.read(65536):
        tail = (tail + chunk)[-STDERR_TAIL:]
    return tail
