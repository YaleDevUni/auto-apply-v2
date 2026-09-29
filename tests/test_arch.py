"""`make arch`(import-linter) 가 §A2 계층 위반을 실제로 잡는지 — 규칙이 조용히 무력화되지 않게.

소스 트리 복사본에 위반 import 를 하나 심고 같은 pyproject 설정으로 돌린다. 원본은 건드리지 않는다.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_LINT = (
    "import sys; from importlinter.cli import lint_imports; sys.exit(lint_imports(no_cache=True))"
)


def _lint_with_violation(
    tmp_path: Path, module: str, source: str
) -> subprocess.CompletedProcess[str]:
    pkg = tmp_path / "src" / "auto_apply"
    shutil.copytree(
        REPO_ROOT / "src" / "auto_apply", pkg, ignore=shutil.ignore_patterns("__pycache__")
    )
    (pkg / Path(*module.split("."))).with_suffix(".py").write_text(source, encoding="utf-8")
    shutil.copy(REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    # PYTHONPATH 가 editable 설치(.pth)보다 앞서므로 grimp 는 원본이 아니라 복사본을 읽는다.
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "src")}
    return subprocess.run(
        [sys.executable, "-c", _LINT],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.mark.parametrize(
    ("module", "source", "contract"),
    [
        (
            "runner._violation",
            "import auto_apply.adapters.clock.system\n",
            "runner 는 services·port 만 안다",
        ),
        (
            "services._violation",
            "import auto_apply.runner.job_runner\n",
            "services 는 port 만 안다",
        ),
    ],
)
def test_arch_rejects_layer_violation(tmp_path, module, source, contract):
    res = _lint_with_violation(tmp_path, module, source)
    out = res.stdout + res.stderr
    assert res.returncode != 0, out
    assert f"auto_apply.{module}" in out
    assert contract in out
