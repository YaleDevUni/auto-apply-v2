"""job_source 어댑터가 공유하는 요청 헬퍼. port 가 아니다 — httpx 가 이미

HTTP 엔진 추상화이므로 그 위에 또 한 겹을 두지 않는다 (ARCHITECTURE.md §11.6).
레이트리밋 + 지수 백오프 재시도만 얇게 얹는다. 구 프로젝트 http.py 를 async 로 옮김.
"""

import asyncio
import time

import httpx
import structlog

log = structlog.get_logger()

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ThrottledClient:
    """인스턴스 하나가 커넥션 풀 하나. job_source 어댑터 생성자가 주입받는다."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        delay: float = 0.7,
        retries: int = 3,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"},
        )
        self._delay = delay
        self._retries = retries
        self._last_request = 0.0

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay:
            await asyncio.sleep(self._delay - elapsed)
        self._last_request = time.monotonic()

    async def get(self, url: str, **kwargs: object) -> httpx.Response | None:
        last_err: Exception | None = None
        for attempt in range(self._retries):
            await self._throttle()
            try:
                resp = await self._client.get(url, **kwargs)  # type: ignore[arg-type]
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"status {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp
            except Exception as exc:
                last_err = exc
                log.warning("job_source.get 실패", url=url, attempt=attempt + 1, error=str(exc))
                await asyncio.sleep(1.5**attempt)
        log.error("job_source.get 최종 실패", url=url, error=str(last_err))
        return None

    async def get_json(self, url: str, **kwargs: object) -> object | None:
        resp = await self.get(url, **kwargs)
        if resp is None:
            return None
        try:
            data: object = resp.json()
            return data
        except Exception as exc:
            log.error("job_source.get_json 파싱 실패", url=url, error=str(exc))
            return None

    async def aclose(self) -> None:
        await self._client.aclose()
