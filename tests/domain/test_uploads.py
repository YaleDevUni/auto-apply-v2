"""업로드 파일 규칙 (§A7) — 형식 화이트리스트·크기·파일명 정리."""

import pytest

from auto_apply.domain.errors import UploadRejected
from auto_apply.domain.uploads import MAX_FILENAME_CHARS, sanitize_filename, validate_upload

PDF = b"%PDF-1.7\n..."
PNG = b"\x89PNG\r\n\x1a\n...."
JPG = b"\xff\xd8\xff\xe0...."
DOCX = b"PK\x03\x04...."


@pytest.mark.parametrize(
    ("name", "data", "ctype"),
    [
        ("r.pdf", PDF, "application/pdf"),
        ("R.PDF", PDF, "application/pdf"),
        ("p.png", PNG, "image/png"),
        ("p.jpg", JPG, "image/jpeg"),
        ("p.jpeg", JPG, "image/jpeg"),
        (
            "cv.docx",
            DOCX,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
def test_whitelist(name, data, ctype):
    assert validate_upload(name, data, max_bytes=64) == (name, ctype)


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("x.exe", b"MZ....", "unsupported_type"),
        ("x.html", b"<html>", "unsupported_type"),
        ("x.doc", b"\xd0\xcf\x11\xe0", "unsupported_type"),
        ("noext", PDF, "unsupported_type"),
        (".pdf", PDF, "unsupported_type"),
        ("..", PDF, "unsupported_type"),
        ("fake.pdf", b"<script>alert(1)</script>", "unsupported_type"),
        ("fake.png", PDF, "unsupported_type"),
        ("empty.pdf", b"", "empty"),
        ("big.pdf", PDF + b"x" * 64, "too_large"),
    ],
)
def test_rejections(name, data, reason):
    with pytest.raises(UploadRejected) as e:
        validate_upload(name, data, max_bytes=64)
    assert e.value.reason == reason


@pytest.mark.parametrize(
    "raw",
    [
        "C:\\fakepath\\이력서.pdf",
        "..\\..\\이력서.pdf",  # 회귀 ③: `....이력서.pdf` 가 되면 안 된다
        "../../etc/이력서.pdf",
        "a/b\\c/이력서.pdf",
        " 이력서.pdf ",
    ],
)
def test_path_parts_are_dropped(raw):
    assert sanitize_filename(raw) == "이력서.pdf"
    assert validate_upload(raw, PDF, max_bytes=64)[0] == "이력서.pdf"


@pytest.mark.parametrize(
    "raw",
    [
        "이력\x00서.pdf",  # 회귀 ④: NUL
        "이력\r\n서.pdf",
        "이력\x1b서.pdf",
        "이력\u202e서.pdf",  # RTL override — 확장자 위장
        "이력\u200b서.pdf",
    ],
)
def test_control_and_format_chars_removed(raw):
    assert sanitize_filename(raw) == "이력서.pdf"


def test_long_filename_capped_keeping_extension():
    """회귀 ④: 60KB 파일명이 그대로 저장되면 안 된다."""
    name, _ = validate_upload("가" * 60_000 + ".pdf", PDF, max_bytes=64)
    assert len(name) == MAX_FILENAME_CHARS
    assert name.endswith(".pdf")
