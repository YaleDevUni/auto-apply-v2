"""API 에러 응답 — 모든 실패를 한 JSON 모양으로 (T1.2).

`{"error": {"code": "...", "message": "...", "details": [{"loc": [...], "msg": "..."}]}}`

검증 실패 상세에는 **입력값을 싣지 않는다**. FastAPI 기본 422 는 `input` 을 그대로 돌려주는데,
거부한 주민등록번호가 응답·브라우저 콘솔로 되돌아가면 절대 규칙 5 를 API 가 스스로 어기게 된다.
"""

from collections.abc import Sequence
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from auto_apply.contracts._base import Frozen
from auto_apply.domain.errors import (
    AnswerKeyConflict,
    InvalidInput,
    LLMAuthRequired,
    LLMExecutionError,
    LLMQuotaExceeded,
    LLMSchemaViolation,
    NotFound,
    ProfileNotFound,
    TextExtractionFailed,
    UniqueIdentifierRejected,
    UploadRejected,
)

log = structlog.get_logger(__name__)


class ErrorDetail(Frozen):
    loc: list[str | int]
    msg: str


class ErrorInfo(Frozen):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(Frozen):
    error: ErrorInfo


def error_response(
    status: int, code: str, message: str, details: Sequence[ErrorDetail] = ()
) -> JSONResponse:
    body = ErrorResponse(error=ErrorInfo(code=code, message=message, details=list(details)))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def _validation(errors: Sequence[Any]) -> JSONResponse:
    details = [ErrorDetail(loc=list(e.get("loc", ())), msg=str(e.get("msg", ""))) for e in errors]
    rejected = any(
        isinstance((e.get("ctx") or {}).get("error"), UniqueIdentifierRejected) for e in errors
    )
    if rejected:
        return error_response(
            422, "unique_identifier_rejected", "주민등록번호 형식의 값은 저장할 수 없다", details
        )
    return error_response(422, "validation_error", "입력 값이 올바르지 않다", details)


_UPLOAD_STATUS = {"too_large": 413, "unsupported_type": 415, "empty": 422}
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}


def _llm_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, LLMSchemaViolation):
        return "llm_invalid_output", "LLM 응답이 형식에 맞지 않았다 — 다시 시도해 달라"
    if isinstance(exc, LLMAuthRequired):
        return "llm_auth_required", "Claude 로그인이 필요하다 (`claude login`)"
    if isinstance(exc, LLMQuotaExceeded):
        return "llm_quota_exceeded", "LLM 사용량 한도에 걸렸다 — 잠시 뒤 다시 시도해 달라"
    return "llm_error", "LLM 호출이 실패했다 — 잠시 뒤 다시 시도해 달라"


def install_error_handlers(app: FastAPI) -> None:
    async def request_validation(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError | ValidationError)
        return _validation(exc.errors())

    async def not_found(_: Request, exc: Exception) -> JSONResponse:
        return error_response(404, "not_found", str(exc))

    async def invalid(_: Request, exc: Exception) -> JSONResponse:
        return error_response(422, "validation_error", str(exc))

    async def identifier(_: Request, exc: Exception) -> JSONResponse:
        return error_response(422, "unique_identifier_rejected", str(exc))

    async def conflict(_: Request, exc: Exception) -> JSONResponse:
        return error_response(409, "conflict", str(exc))

    async def upload(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, UploadRejected)
        return error_response(_UPLOAD_STATUS.get(exc.reason, 422), exc.reason, str(exc))

    async def extraction(_: Request, exc: Exception) -> JSONResponse:
        return error_response(422, "extraction_failed", str(exc))

    async def llm(_: Request, exc: Exception) -> JSONResponse:
        # LLM 에러 문자열은 모델 출력·CLI 응답 원문(이력서 내용)을 담을 수 있다 — 종류만 알린다.
        code, message = _llm_error(exc)
        log.warning("llm call failed", application_id=None, run_id=None, code=code)
        return error_response(502, code, message)

    async def http(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        res = error_response(exc.status_code, code, str(exc.detail))
        if exc.headers:
            res.headers.update(exc.headers)
        return res

    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        # 원인은 서버 로그에만. 응답에 예외 문자열을 실으면 내부 경로·입력값이 샐 수 있다.
        log.exception("unhandled api error", application_id=None, run_id=None)
        return error_response(500, "internal_error", "서버 내부 오류")

    app.add_exception_handler(RequestValidationError, request_validation)
    app.add_exception_handler(ValidationError, request_validation)
    app.add_exception_handler(NotFound, not_found)
    app.add_exception_handler(ProfileNotFound, not_found)
    app.add_exception_handler(InvalidInput, invalid)
    app.add_exception_handler(UniqueIdentifierRejected, identifier)
    app.add_exception_handler(AnswerKeyConflict, conflict)
    app.add_exception_handler(UploadRejected, upload)
    app.add_exception_handler(TextExtractionFailed, extraction)
    app.add_exception_handler(LLMSchemaViolation, llm)
    app.add_exception_handler(LLMExecutionError, llm)
    app.add_exception_handler(StarletteHTTPException, http)
    app.add_exception_handler(Exception, unexpected)
