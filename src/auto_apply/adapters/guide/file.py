import asyncio
from pathlib import Path


class FileGuideSource:
    """`config/resume_guide.md`를 읽고 쓴다. 파일이 없으면 빈 가이드로 취급한다 —

    REVISE(general) 을 한 번도 안 썼으면 아직 아무 규칙도 안 쌓였다는 뜻이라 정상이다.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    async def get(self) -> str:
        if not self._path.is_file():
            return ""

        def _read() -> str:
            return self._path.read_text(encoding="utf-8")

        return await asyncio.to_thread(_read)

    async def save(self, text: str) -> None:
        def _write() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(text, encoding="utf-8")

        await asyncio.to_thread(_write)
