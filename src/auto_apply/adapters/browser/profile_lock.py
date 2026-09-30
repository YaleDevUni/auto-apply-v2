"""브라우저 프로필 디렉터리 단일 사용 잠금 (BrowserHost 계약).

OS 파일 잠금이라 프로세스가 죽으면 OS 가 풀어 준다 — 남은 잠금 파일 때문에 영영 못 뜨는 일이 없다.
POSIX 는 `flock`(열린 파일마다 따로 잠기므로 같은 프로세스의 두 번째 호스트도 막힌다. `lockf` 는
프로세스 단위라 못 막는다), Windows 는 `msvcrt.locking`(핸들 단위).
"""

import os
import sys
from pathlib import Path

from auto_apply.domain.errors import BrowserProfileInUse


def lock_path_for(profile_dir: Path) -> Path:
    """잠금 파일은 프로필 옆에 둔다 — 프로필 안은 Chrome 이 관리하는 곳이다."""
    return profile_dir.with_name(f"{profile_dir.name}.lock")


class ProfileLock:
    def __init__(self, profile_dir: Path) -> None:
        self._profile_dir = profile_dir
        self._path = lock_path_for(profile_dir)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock(fd)
        except OSError as e:
            os.close(fd)
            raise BrowserProfileInUse(
                f"브라우저 프로필을 다른 auto-apply 가 쓰고 있다 — {self._profile_dir}. "
                "같은 데이터 디렉터리로 떠 있는 auto-apply 를 먼저 종료하라"
            ) from e
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
