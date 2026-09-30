"""설치된 Chrome 탐지 · 사용자 기본 프로필 거부 (D6).

Playwright `channel="chrome"` 이 못 찾을 때(비표준 설치 위치 등)의 대안이다. 플랫폼·환경변수·홈·
파일 존재 확인을 인자로 받아, mac/Windows 경로를 어느 OS 에서든 가짜 파일시스템으로 검증할 수 있다.
"""

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from auto_apply.domain.errors import PolicyViolation

_MAC_BUNDLE = "Google Chrome.app/Contents/MacOS/Google Chrome"
_WIN_EXE = "Google/Chrome/Application/chrome.exe"
# 32비트 Python 이 64비트 Windows 에서 돌면 PROGRAMFILES 가 x86 쪽이다 — ProgramW6432 도 본다.
_WIN_ROOT_VARS = ("PROGRAMFILES", "ProgramW6432", "PROGRAMFILES(X86)", "LOCALAPPDATA")
_WIN_DEFAULT_ROOTS = (r"C:\Program Files", r"C:\Program Files (x86)")
_LINUX = ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome")


def _win_env(environ: Mapping[str, str], name: str) -> str | None:
    """Windows 환경변수는 대소문자를 가리지 않는다."""
    folded = name.casefold()
    return next((v for k, v in environ.items() if k.casefold() == folded and v), None)


def chrome_candidates(platform: str, environ: Mapping[str, str], home: PurePath) -> list[PurePath]:
    """표준 설치 위치 후보, 우선순위 순."""
    if platform == "darwin":
        return [PurePosixPath("/Applications") / _MAC_BUNDLE, home / "Applications" / _MAC_BUNDLE]
    if platform == "win32":
        roots = [r for r in (_win_env(environ, v) for v in _WIN_ROOT_VARS) if r]
        roots += [r for r in _WIN_DEFAULT_ROOTS if r not in roots]
        found: list[PurePath] = []
        for root in roots:
            path = PureWindowsPath(root) / _WIN_EXE
            if path not in found:
                found.append(path)
        return found
    return [PurePosixPath(p) for p in _LINUX]


def find_chrome(
    *,
    platform: str = sys.platform,
    environ: Mapping[str, str] | None = None,
    home: PurePath | None = None,
    is_file: Callable[[PurePath], bool] = lambda p: Path(p).is_file(),
) -> PurePath | None:
    env = os.environ if environ is None else environ
    for path in chrome_candidates(platform, env, home or Path.home()):
        if is_file(path):
            return path
    return None


def default_user_data_dirs(
    platform: str, environ: Mapping[str, str], home: PurePath
) -> list[PurePath]:
    """사용자가 평소 쓰는 Chrome 의 user-data-dir."""
    if platform == "darwin":
        return [home / "Library/Application Support/Google/Chrome"]
    if platform == "win32":
        local = _win_env(environ, "LOCALAPPDATA") or str(PureWindowsPath(home) / "AppData/Local")
        return [PureWindowsPath(local) / "Google/Chrome/User Data"]
    return [home / ".config/google-chrome", home / ".config/chromium"]


def ensure_not_default_profile(
    profile_dir: PurePath,
    *,
    platform: str = sys.platform,
    environ: Mapping[str, str] | None = None,
    home: PurePath | None = None,
) -> None:
    """사용자 기본 Chrome 프로필(또는 그 안)을 앱 프로필로 쓰지 않는다 (D6).

    평소 브라우저의 로그인·쿠키를 에이전트가 몰고 다니게 되고, Chrome 136+ 는 거기에 CDP 도 막는다.
    DATA_DIR 을 잘못 지정한 경우를 기동 전에 막는다. mac·Windows 파일시스템은 대소문자를 안 가린다.
    """
    env = os.environ if environ is None else environ
    fold = platform in {"darwin", "win32"}
    kind = PureWindowsPath if platform == "win32" else PurePosixPath

    def norm(p: PurePath) -> PurePath:
        s = str(p)
        return kind(s.casefold() if fold else s)

    target = norm(profile_dir)
    for default in default_user_data_dirs(platform, env, home or Path.home()):
        d = norm(default)
        if target == d or target.is_relative_to(d):
            raise PolicyViolation(
                f"사용자 기본 Chrome 프로필 안은 앱 프로필로 쓰지 않는다 — {profile_dir}. "
                "DATA_DIR 을 다른 곳으로 지정하라"
            )
