"""테스트 짐 서버(§A4) — 픽스처 사이트를 서빙하고 도착한 모든 요청을 기록한다.

하네스 테스트는 "제출 0건"을 브라우저 쪽이 아니라 **서버가 실제로 받은 것**으로 단언한다 —
브라우저 쪽 관찰은 하네스와 같은 층이라 하네스가 뚫린 걸 놓칠 수 있다.
127.0.0.1 임의 포트에만 바인딩한다(외부 노출 금지).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import TracebackType

from tests.gym.handler import GymHandler, RecordedRequest
from tests.gym.manifest import SITES_DIR, Manifest, load_manifest

__all__ = ["GymServer", "RecordedRequest"]


class GymServer:
    """`with GymServer() as gym:` — 블록을 나가면 소켓·스레드를 정리한다."""

    def __init__(self, manifest: Manifest | None = None, root: Path = SITES_DIR) -> None:
        self.manifest = manifest or load_manifest()
        self.root = root.resolve()
        self._lock = threading.RLock()  # wait_for 의 predicate 가 같은 락으로 requests 를 읽는다
        self._changed = threading.Condition(self._lock)
        self._requests: list[RecordedRequest] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ 수명주기
    def start(self) -> GymServer:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), GymHandler)
        httpd.daemon_threads = True
        httpd.gym = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, args=(0.05,), name="gym", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> GymServer:
        return self.start()

    def __exit__(
        self, et: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.stop()

    @property
    def address(self) -> tuple[str, int]:
        if self._httpd is None:
            raise RuntimeError("짐 서버가 떠 있지 않다")
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return self.base_url + path

    def entry_url(self, site: str) -> str:
        return self.url(self.manifest.sites[site].entry)

    # ------------------------------------------------------------------ 기록 조회
    def record(self, req: RecordedRequest) -> None:
        with self._changed:
            self._requests.append(req)
            self._changed.notify_all()

    def wait_for(self, predicate: Callable[[], object], timeout: float = 5.0) -> bool:
        """기록이 predicate 를 만족할 때까지 — beacon 은 화면 표시보다 서버 수신이 늦을 수 있다."""
        with self._changed:
            return bool(self._changed.wait_for(predicate, timeout))

    @property
    def requests(self) -> tuple[RecordedRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    def final_submissions(self, site: str | None = None) -> list[RecordedRequest]:
        """최종 제출 엔드포인트에 닿은 요청 — 하네스 테스트는 이게 비어 있음을 단언한다."""
        return [r for r in self.requests if r.final and (site is None or r.site == site)]

    def intermediate_requests(self, site: str | None = None) -> list[RecordedRequest]:
        """허용된 중간 요청(단계 저장·로그인) — relaxed 모드가 이걸 막으면 안 된다."""
        return [r for r in self.requests if r.allowed and (site is None or r.site == site)]

    def unexpected_requests(self) -> list[RecordedRequest]:
        """매니페스트에 없는 비-GET — 짐 사이트가 모르는 경로로 뭔가를 보냈다."""
        return [
            r
            for r in self.requests
            if r.method not in ("GET", "HEAD") and not (r.final or r.allowed)
        ]

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()
