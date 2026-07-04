"""统一错误 envelope 与错误码（技术设计文档 6.1）。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, http_status: int, code: str, message: str):
        self.http_status = http_status
        self.code = code
        self.message = message


# 便捷构造器
def invalid_argument(message: str) -> ApiError:
    return ApiError(400, "invalid_argument", message)


def unauthorized(message: str = "invalid or missing API key") -> ApiError:
    return ApiError(401, "unauthorized", message)


def not_found() -> ApiError:
    # kid 不存在 / domain 越权 / 非 published / 已过期——统一 404，不暴露存在性
    return ApiError(404, "not_found", "knowledge not found")


def rate_limited(limit: int) -> ApiError:
    return ApiError(429, "rate_limited", f"QPS limit exceeded (limit={limit})")


def upstream_unavailable() -> ApiError:
    return ApiError(503, "upstream_unavailable", "knowledge index temporarily unavailable")


def not_implemented(feature: str) -> ApiError:
    return ApiError(501, "not_implemented", f"{feature} is not implemented yet")


def _envelope(request: Request, code: str, message: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", ""),
        }
    }


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        headers = {"Retry-After": "1"} if exc.code == "rate_limited" else None
        return JSONResponse(
            status_code=exc.http_status,
            content=_envelope(request, exc.code, exc.message),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # 契约规定参数校验失败返回 400 invalid_argument（而非 FastAPI 默认 422）
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = f"{loc}: {first.get('msg', 'invalid')}" if loc else "invalid request"
        return JSONResponse(status_code=400, content=_envelope(request, "invalid_argument", message))
