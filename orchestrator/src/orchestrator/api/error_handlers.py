"""Turn every error into the one error shape (docs/api.md §3)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from orchestrator.errors import ApiError, from_illegal_transition
from shared.models import ErrorBody, ErrorCode, ErrorResponse
from shared.state_machine import IllegalTransitionError

logger = logging.getLogger(__name__)

# Plain HTTP errors raised by FastAPI/Starlette itself (unknown route, wrong method).
CODE_BY_HTTP_STATUS = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.VALIDATION_ERROR,
}


def error_response(error: ApiError) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=error.code, message=error.message, details=error.details)
    )
    return JSONResponse(
        status_code=error.status_code,
        content=body.model_dump(mode="json"),
        headers=error.headers or None,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(error)

    @app.exception_handler(IllegalTransitionError)
    async def handle_illegal_transition(
        request: Request, error: IllegalTransitionError
    ) -> JSONResponse:
        return error_response(from_illegal_transition(error))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        # errors() can hold non-JSON values (bytes, exceptions); keep the readable parts.
        problems = [
            {"loc": list(problem.get("loc", [])), "msg": problem.get("msg", "")}
            for problem in error.errors()
        ]
        return error_response(
            ApiError(400, ErrorCode.VALIDATION_ERROR, "request is not valid", {"errors": problems})
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        code = CODE_BY_HTTP_STATUS.get(error.status_code, ErrorCode.VALIDATION_ERROR)
        return error_response(ApiError(error.status_code, code, str(error.detail)))
