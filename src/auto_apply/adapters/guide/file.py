import asyncio
from pathlib import Path


class FileGuideSource:
    """`config/resume_guide.{platform}.md`를 읽고 쓴다. 파일이 없으면 빈 가이드로 취급한다 —

    아직 아무 규칙도 안 쌓였다는 뜻이라 정상이다. 이 파일들은 사람이 쓴 커스텀 프롬프트라
    `.gitignore`로 뺀다(`config/resume_guide.example.md` 참고).
    """

    def __init__(self, dir_path: Path) -> None:
        self._dir = dir_path

    def _path_for(self, platform: str) -> Path:
        return self._dir / f"resume_guide.{platform}.md"

    async def get(self, platform: str) -> str:
        path = self._path_for(platform)
        if not path.is_file():
            return ""

        def _read() -> str:
            return path.read_text(encoding="utf-8")

        return await asyncio.to_thread(_read)

    async def save(self, platform: str, text: str) -> None:
        path = self._path_for(platform)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        await asyncio.to_thread(_write)
