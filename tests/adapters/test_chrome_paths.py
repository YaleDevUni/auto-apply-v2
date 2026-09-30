"""설치 Chrome 경로 탐지 · 기본 프로필 거부 (D6) — mac/Windows 경로를 가짜 파일시스템으로."""

from pathlib import PurePath, PurePosixPath, PureWindowsPath

import pytest

from auto_apply.adapters.browser.chrome_paths import (
    chrome_candidates,
    ensure_not_default_profile,
    find_chrome,
)
from auto_apply.domain.errors import PolicyViolation

MAC_HOME = PurePosixPath("/Users/sample")
WIN_HOME = PureWindowsPath(r"C:\Users\sample")
WIN_ENV = {
    "PROGRAMFILES": r"C:\Program Files",
    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
    "LOCALAPPDATA": r"C:\Users\sample\AppData\Local",
}


def fs(*files: PurePath):
    present = set(files)
    return lambda p: p in present


def test_mac_system_applications_first():
    system = PurePosixPath("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    user = MAC_HOME / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    both = fs(system, user)
    assert find_chrome(platform="darwin", environ={}, home=MAC_HOME, is_file=both) == system
    only_user = fs(user)
    assert find_chrome(platform="darwin", environ={}, home=MAC_HOME, is_file=only_user) == user


@pytest.mark.parametrize(
    ("installed", "env"),
    [
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", WIN_ENV),
        (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", WIN_ENV),
        # 사용자 단위 설치(관리자 권한 없이)
        (r"C:\Users\sample\AppData\Local\Google\Chrome\Application\chrome.exe", WIN_ENV),
        # 64비트 Windows 의 32비트 Python — PROGRAMFILES 가 x86 을 가리킨다
        (
            r"D:\Apps\Google\Chrome\Application\chrome.exe",
            {"PROGRAMFILES": r"C:\Program Files (x86)", "ProgramW6432": r"D:\Apps"},
        ),
        # 환경변수가 비어도 표준 위치는 본다
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", {}),
    ],
    ids=["program-files", "x86", "per-user", "programw6432", "no-env"],
)
def test_windows_locations(installed, env):
    exe = PureWindowsPath(installed)
    got = find_chrome(platform="win32", environ=env, home=WIN_HOME, is_file=fs(exe))
    assert got == exe


def test_windows_env_names_are_case_insensitive():
    exe = PureWindowsPath(r"E:\PF\Google\Chrome\Application\chrome.exe")
    env = {"ProgramFiles": r"E:\PF"}
    assert find_chrome(platform="win32", environ=env, home=WIN_HOME, is_file=fs(exe)) == exe


def test_windows_candidates_have_no_duplicates():
    env = {**WIN_ENV, "ProgramW6432": r"C:\Program Files"}
    found = chrome_candidates("win32", env, WIN_HOME)
    assert len(found) == len(set(found))


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_not_installed(platform):
    home = WIN_HOME if platform == "win32" else MAC_HOME
    assert find_chrome(platform=platform, environ=WIN_ENV, home=home, is_file=fs()) is None


@pytest.mark.parametrize(
    ("platform", "home", "env", "profile"),
    [
        ("darwin", MAC_HOME, {}, "/Users/sample/Library/Application Support/Google/Chrome"),
        (
            "darwin",
            MAC_HOME,
            {},
            "/Users/sample/Library/Application Support/Google/Chrome/auto-apply/chrome-profile",
        ),
        # APFS 는 대소문자를 안 가린다
        ("darwin", MAC_HOME, {}, "/users/SAMPLE/library/application support/google/chrome"),
        ("win32", WIN_HOME, WIN_ENV, r"C:\Users\sample\AppData\Local\Google\Chrome\User Data"),
        ("win32", WIN_HOME, WIN_ENV, r"c:\users\sample\appdata\local\google\chrome\user data\x"),
        ("win32", WIN_HOME, {}, r"C:\Users\sample\AppData\Local\Google\Chrome\User Data"),
        ("linux", MAC_HOME, {}, "/Users/sample/.config/google-chrome/Default"),
    ],
)
def test_default_profile_refused(platform, home, env, profile):
    kind = PureWindowsPath if platform == "win32" else PurePosixPath
    with pytest.raises(PolicyViolation):
        ensure_not_default_profile(kind(profile), platform=platform, environ=env, home=home)


@pytest.mark.parametrize(
    ("platform", "home", "profile"),
    [
        ("darwin", MAC_HOME, "/Users/sample/Library/Application Support/auto-apply/chrome-profile"),
        ("darwin", MAC_HOME, "/Users/sample/Library/Application Support/Google/Chrome Beta"),
        ("win32", WIN_HOME, r"C:\Users\sample\AppData\Local\auto-apply\chrome-profile"),
    ],
)
def test_app_profile_allowed(platform, home, profile):
    kind = PureWindowsPath if platform == "win32" else PurePosixPath
    ensure_not_default_profile(kind(profile), platform=platform, environ=WIN_ENV, home=home)
