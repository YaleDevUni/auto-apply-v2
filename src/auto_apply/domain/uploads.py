"""업로드 파일 규칙 (§A7) — 허용 형식·크기·파일명 정리.

입력은 사용자·브라우저가 보낸 그대로라 믿지 않는다.
"""

import unicodedata
from pathlib import PurePosixPath

from auto_apply.domain.errors import UploadRejected

MAX_FILENAME_CHARS = 255

# 확장자 → (저장할 content type, 파일 머리 시그니처). 클라이언트가 보낸 content type 은 믿지
# 않는다 — 확장자와 실제 바이트가 둘 다 맞아야 받는다. DOCX 는 zip 컨테이너라 zip 머리를 본다.
_ALLOWED: dict[str, tuple[str, tuple[bytes, ...]]] = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        (b"PK\x03\x04",),
    ),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}
ALLOWED_UPLOAD_EXTENSIONS = tuple(_ALLOWED)


def sanitize_filename(raw: str) -> str:
    """표시용 파일명. 제어·서식 문자(NUL, 줄바꿈, RTL override 등)를 지우고 경로 앞부분을 뗀다.

    구분자는 `/`·`\\` 둘 다 본다 — 실행 OS 와 무관하게 `..\\..\\x.pdf` 가 `x.pdf` 가 되어야 한다.
    """
    cleaned = "".join(ch for ch in raw if unicodedata.category(ch) not in {"Cc", "Cf"})
    return cleaned.replace("\\", "/").rsplit("/", 1)[-1].strip()


def _cap_length(name: str, suffix: str) -> str:
    if len(name) <= MAX_FILENAME_CHARS:
        return name
    return name[: MAX_FILENAME_CHARS - len(suffix)] + suffix


def validate_upload(filename: str, data: bytes, *, max_bytes: int) -> tuple[str, str]:
    """(정리한 파일명, 저장할 content type). 허용 형식·크기가 아니면 `UploadRejected`."""
    name = sanitize_filename(filename)
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in _ALLOWED:
        allowed = ", ".join(ALLOWED_UPLOAD_EXTENSIONS)
        raise UploadRejected("unsupported_type", f"허용하지 않는 파일 형식이다 (허용: {allowed})")
    if not data:
        raise UploadRejected("empty", "빈 파일이다")
    if len(data) > max_bytes:
        raise UploadRejected("too_large", f"파일이 {max_bytes} 바이트 상한을 넘는다")
    content_type, signatures = _ALLOWED[suffix]
    if not data.startswith(signatures):
        raise UploadRejected("unsupported_type", f"파일 내용이 {suffix} 형식이 아니다")
    return _cap_length(name, PurePosixPath(name).suffix), content_type
