"""프로필 단일 사용 잠금 — 같은 프로세스·다른 프로세스 둘 다 막고, 프로세스가 죽으면 풀린다."""

import subprocess
import sys
import textwrap

import pytest

from auto_apply.adapters.browser.profile_lock import ProfileLock, lock_path_for
from auto_apply.domain.errors import BrowserProfileInUse


def test_lock_file_sits_next_to_profile(tmp_path):
    profile = tmp_path / "chrome-profile"
    assert lock_path_for(profile) == tmp_path / "chrome-profile.lock"


def test_second_lock_in_same_process_refused(tmp_path):
    profile = tmp_path / "data" / "chrome-profile"
    first, second = ProfileLock(profile), ProfileLock(profile)
    first.acquire()
    first.acquire()  # 이미 쥔 쪽이 다시 부르는 것은 괜찮다
    with pytest.raises(BrowserProfileInUse, match="chrome-profile"):
        second.acquire()
    assert not second.held
    first.release()
    first.release()
    second.acquire()
    assert second.held
    second.release()


def _hold_in_child(profile) -> subprocess.Popen[str]:
    code = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from auto_apply.adapters.browser.profile_lock import ProfileLock
        ProfileLock(Path({str(profile)!r})).acquire()
        print("locked", flush=True)
        sys.stdin.read()
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "locked"
    return child


def test_other_process_refused_until_it_dies(tmp_path):
    """다른 auto-apply 가 같은 DATA_DIR 로 떠 있는 경우.

    잠금 파일이 남아도 프로세스가 죽으면 풀린다."""
    profile = tmp_path / "chrome-profile"
    child = _hold_in_child(profile)
    try:
        with pytest.raises(BrowserProfileInUse):
            ProfileLock(profile).acquire()
    finally:
        child.kill()
        child.communicate(timeout=10)
    assert lock_path_for(profile).exists()
    lock = ProfileLock(profile)
    lock.acquire()
    lock.release()
