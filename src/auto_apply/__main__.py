"""`auto-apply` 콘솔 스크립트 — 한 프로세스에 API·JobRunner 를 띄운다 (§A1).

소켓은 여기서 직접 바인드한다: `--port 0` 이면 OS 가 고른 실제 포트를 알아야 사용자·테스트에게
주소를 알려줄 수 있는데, uvicorn 은 설정값(0)만 로그에 찍는다.
"""

import argparse
import asyncio
import os
import socket
from collections.abc import Sequence

import structlog
import uvicorn

from auto_apply.api.main import create_app
from auto_apply.bootstrap import StartupError, prepare_data_dir
from auto_apply.config import load_settings

log = structlog.get_logger(__name__)

# §A10·D1: 인증 없는 로컬 전용 서버다. 바인드 주소는 옵션으로 열지 않는다.
HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="auto-apply", description="auto-apply 로컬 서버")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="0 이면 빈 포트를 고른다")
    return parser.parse_args(argv)


class _Server(uvicorn.Server):
    """startup 이 끝난 뒤(lifespan: 마이그레이션·JobRunner 기동 완료) 실제 주소를 한 줄 알린다."""

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started and sockets:
            port = sockets[0].getsockname()[1]
            print(f"auto-apply ready: http://{HOST}:{port}", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    cfg = load_settings()
    # uvicorn lifespan 안에서 실패하면 트레이스백만 남는다 — 준비는 서버 밖에서 먼저 하고,
    # 실패하면 무엇이·어디서·왜 한 줄만 남기고 끝낸다.
    try:
        prepare_data_dir(cfg)
    except StartupError as e:
        log.error(str(e), application_id=None, run_id=None)
        raise SystemExit(1) from None
    app = create_app(cfg, prepare=False)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 재기동 직후 TIME_WAIT 로 바인드가 막히지 않게. Windows 의 SO_REUSEADDR 는 남의 포트까지
    # 가로챌 수 있는 다른 의미라 켜지 않는다.
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, args.port))
    except OSError as e:
        sock.close()
        raise SystemExit(f"auto-apply: {HOST}:{args.port} 에 바인드할 수 없다 — {e}") from e
    config = uvicorn.Config(app, log_level="info", lifespan="on")
    server = _Server(config)
    try:
        asyncio.run(server.serve(sockets=[sock]))
    except KeyboardInterrupt:
        # uvicorn 은 정상 종료(lifespan shutdown)를 끝낸 뒤 받은 SIGINT 를 다시 올린다 — Ctrl+C 는
        # 사용자가 의도한 종료라 트레이스백 없이 0 으로 끝낸다.
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
