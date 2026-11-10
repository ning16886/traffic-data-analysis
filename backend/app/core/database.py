"""数据库连接层。SQLAlchemy 2.0 风格，SQLite 与 MySQL 共用一套模型。"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# SQLite 需要 check_same_thread=False 才能在 FastAPI 的多线程下复用连接；
# MySQL 连接池参数由 SQLAlchemy 默认值接管。
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True, echo=False)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。init_db.sql 与本文件生成的 DDL 字段保持一致。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用：每个请求一个会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_database() -> None:
    """建表。本地开发直接按模型建；生产环境表结构由 init_db.sql 先行创建，此处幂等跳过。"""
    from app import models  # noqa: F401  触发模型注册

    Base.metadata.create_all(bind=engine)
