"""业务异常与全局异常处理器。

服务层只管 raise BusinessException("中文提示")，路由层不写 try/except，
由全局处理器统一转成 { code, data, msg } 信封。
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import error

logger = logging.getLogger("traffic")


class BusinessException(Exception):
    """可预期的业务失败：参数不合法、文件类型不支持、任务正在运行等。"""

    def __init__(self, msg: str, code: int = 4000, http_status: int = status.HTTP_200_OK):
        self.msg = msg
        self.code = code
        self.http_status = http_status
        super().__init__(msg)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessException)
    async def _business(_request: Request, exc: BusinessException):
        logger.warning("业务异常：%s (code=%s)", exc.msg, exc.code)
        return error(exc.code, exc.msg, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "请求参数"
        return error(4220, f"参数校验失败：{field} {first.get('msg', '')}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException):
        return error(exc.status_code * 10, f"请求出错：{exc.detail}", exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception):
        logger.exception("未捕获异常")
        return error(5000, f"服务器内部错误：{type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)
