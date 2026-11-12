"""FastAPI 应用入口。

启动（在 backend/ 目录下）：
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
接口文档：http://127.0.0.1:8000/docs

--host 写 127.0.0.1 而不是 0.0.0.0 也行，但前端 vite 的代理 target 必须同用
127.0.0.1：Windows 上 localhost 优先解析成 IPv6 ::1，uvicorn 默认只监听 IPv4，
代理会连不上。两边都写死 127.0.0.1 最稳。
"""

from collections.abc import AsyncIterator

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import init_database, register_exception_handlers, settings, success
from app.routers import dashboard, match


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 本地 SQLite 首次启动自动建表；生产由 init_db.sql 先建好，create_all 幂等跳过
    init_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(dashboard.router, prefix="/api")
app.include_router(match.router, prefix="/api")


@app.get("/api/health", tags=["系统"])
def health():
    """给前端和容器探活用，顺带确认后端确实起来了。"""
    return success({"app": settings.app_name, "database": settings.database_url.split("://")[0]})
