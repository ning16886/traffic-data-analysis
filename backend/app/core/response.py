"""统一响应信封：{ code, data, msg }。

约定（与前端 src/api/client.js 完全一致）：
  code = 0        成功，data 里是业务数据
  code != 0       失败，msg 是可直接展示给用户的中文提示，data 为 null
业务错误不抛 HTTP 500——HTTP 状态码一律 200，靠 code 区分，前端只认这一套。
"""

from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse


def success(data: Any = None, msg: str = "") -> dict:
    return {"code": 0, "data": data, "msg": msg}


def error(code: int, msg: str, http_status: int = status.HTTP_200_OK) -> JSONResponse:
    """业务失败。code 用 4xx/5xx 语义值，但 HTTP 状态默认仍返回 200。"""
    return JSONResponse(
        status_code=http_status,
        content={"code": code, "data": None, "msg": msg},
    )


def page(items: list, total: int, page_no: int, page_size: int) -> dict:
    """分页载荷。列表接口一律分页，绝不让前端拉全表。"""
    return {
        "items": items,
        "total": total,
        "page": page_no,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
    }
