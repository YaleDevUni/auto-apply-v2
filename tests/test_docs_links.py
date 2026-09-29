"""git 추적 Markdown 의 로컬 링크가 실제 파일을 가리키는지 (T0.4 수용 기준을 게이트로 상시화).

외부 URL·앵커는 보지 않는다 — 문서를 지우거나 옮길 때 남는 상대경로 링크가 목적이다.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def broken_links(md_file: Path) -> list[str]:
    text = md_file.read_text(encoding="utf-8")
    # 코드 블록·인라인 코드 안의 [x](y) 는 예시일 뿐 링크가 아니다
    text = _INLINE_CODE.sub("", _FENCE.sub("", text))
    broken = []
    for match in _LINK.finditer(text):
        target = match.group(1)
        if _SCHEME.match(target) or target.startswith("#"):
            continue
        path = target.split("#", 1)[0].split("?", 1)[0]
        if not (md_file.parent / path).exists():
            broken.append(target)
    return broken


def _tracked_markdown() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("git 체크아웃이 아니다 (설치본)")
    out = subprocess.run(
        [git, "ls-files", "-z", "--", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        REPO_ROOT / rel
        for rel in out.split("\0")
        if rel and "node_modules" not in Path(rel).parts and (REPO_ROOT / rel).exists()
    ]


def test_tracked_markdown_has_no_broken_local_links() -> None:
    files = _tracked_markdown()
    assert files, "추적 중인 .md 가 하나도 없다 — ls-files 경로가 틀렸다"
    problems = {str(f.relative_to(REPO_ROOT)): bad for f in files if (bad := broken_links(f))}
    assert problems == {}


def test_broken_links_detects_missing_target(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ok.md").write_text("# ok\n", encoding="utf-8")
    doc = tmp_path / "README.md"
    doc.write_text(
        "\n".join(
            [
                "[좋음](docs/ok.md) [앵커 포함](docs/ok.md#ok) [디렉터리](docs/)",
                "[깨짐](docs/gone.md) ![그림](img/missing.png) [깨진 디렉터리](docs/old/)",
                "[외부](https://example.com/x.md) [메일](mailto:a@b.c) [같은 문서](#top)",
                "`[코드](nope.md)`",
                "```",
                "[펜스 안](also-nope.md)",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    assert broken_links(doc) == ["docs/gone.md", "img/missing.png", "docs/old/"]
