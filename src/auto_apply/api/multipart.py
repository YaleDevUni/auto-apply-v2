"""multipart/form-data 업로드 읽기 — python-multipart 스트리밍 + 본문 크기 상한.

Starlette `request.form()` 과 같은 파서(`MultiPartParser`)를 쓰되 두 가지를 더한다:
- 본문을 상한(파일 상한 + 경계·헤더 여유분)까지만 흘려보낸다. 파일 파트는 파서가 1 MiB 넘으면
  임시 파일로 흘리므로 요청 전체가 메모리에 올라오지 않는다.
- 닫는 경계(`--boundary--`)까지 왔는지 본다. python-multipart 는 잘린 본문을 에러 없이 끝내서
  `request.form()` 만으로는 반쯤 받은 파일이 정상 업로드로 저장된다.
파트 수 제한(파일 1·필드 소수)은 파트를 무한히 보내 파서를 붙잡는 요청을 초반에 끊는다.
"""

from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

from fastapi import Request
from python_multipart.exceptions import ParseError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from auto_apply.domain.errors import InvalidInput, UploadRejected

# 파일 바이트 외의 multipart 경계·헤더 여유분.
_ENVELOPE_BYTES = 64 * 1024
_MAX_FIELDS = 4
_MAX_FIELD_BYTES = 4 * 1024


@dataclass(frozen=True, slots=True)
class UploadedFile:
    filename: str
    data: bytes


class _CompleteMultiPartParser(MultiPartParser):
    completed = False

    def on_end(self) -> None:
        self.completed = True


async def read_single_file(request: Request, *, field: str, max_bytes: int) -> UploadedFile:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise UploadRejected("unsupported_type", "multipart/form-data 로 올려야 한다")
    limit = max_bytes + _ENVELOPE_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large(max_bytes)

    parser = _CompleteMultiPartParser(
        request.headers,
        _capped(request.stream(), limit, max_bytes),
        max_files=1,
        max_fields=_MAX_FIELDS,
        max_part_size=_MAX_FIELD_BYTES,
    )
    try:
        form = await parser.parse()
    except MultiPartException as e:
        # 파일 두 개·필드 과다·경계 없음 등 — 모양이 틀린 요청이다.
        raise InvalidInput(f"multipart 본문이 올바르지 않다: {e.message}") from e
    except ParseError as e:
        # python-multipart 자체 파싱 실패(파트 헤더 과대 등). 메시지에 입력 조각이 실릴 수 있어
        # 싣지 않는다.
        raise InvalidInput("multipart 본문을 해석할 수 없다") from e
    try:
        if not parser.completed:
            raise InvalidInput("multipart 본문이 닫는 경계 전에 끝났다 (잘린 업로드)")
        upload = form.get(field)
        if not isinstance(upload, UploadFile) or upload.filename is None:
            raise InvalidInput(f"`{field}` 필드에 파일이 없다")
        return UploadedFile(filename=upload.filename, data=await upload.read())
    finally:
        await form.close()


async def _capped(
    stream: AsyncIterator[bytes], limit: int, max_bytes: int
) -> AsyncGenerator[bytes, None]:
    received = 0
    async for chunk in stream:
        received += len(chunk)
        if received > limit:
            raise _too_large(max_bytes)
        yield chunk


def _too_large(max_bytes: int) -> UploadRejected:
    return UploadRejected("too_large", f"파일이 {max_bytes} 바이트 상한을 넘는다")
