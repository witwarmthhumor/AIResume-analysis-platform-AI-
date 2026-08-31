"""数据库连接层：engine + 会话工厂 + 健康探测。

引擎全程只有一个（连接池复用，开销小）；每次请求用 with SessionLocal 造一个短会话。
"""

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：给每个请求发一个会话，用完自动归还。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping_database() -> str:
    """执行 SELECT 1 探测数据库连通性，给 /health 用。只报状态，不抛异常。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "connected"
    except Exception:
        return "disconnected"
