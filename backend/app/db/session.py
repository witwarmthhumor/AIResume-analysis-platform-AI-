"""数据库连接层：engine + 会话工厂 + 健康探测。

引擎全程只有一个（连接池复用，开销小）；每次请求用 with SessionLocal 造一个短会话。
"""

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # 取连接前先 ping，避免复用已断开的连接
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle_seconds,
    # 池耗尽时最多等 db_pool_timeout_seconds，不无限排队（配合全局 503 处理器快速失败）
    pool_timeout=settings.db_pool_timeout_seconds,
    # connect_timeout 是 psycopg 驱动参数：数据库不可用时建连最多等 5 秒（默认会挂到系统级超时）
    connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def graph_dsn() -> str:
    """LangGraph checkpoint 专用连接串（v6 审计 S6-2 收口：两图模块共用一处）。

    强制 search_path=langgraph,public（checkpoint 表隔离在专属 schema）；
    DATABASE_URL 已带 query（如 ?sslmode=）时用 & 追加——直接 f-string 拼 `?`
    会产出双问号的非法 DSN（v5 S-5 遗留缺陷在此一并防御）。
    """
    from urllib.parse import urlsplit, urlunsplit

    base = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    scheme, netloc, path, query, _frag = urlsplit(base)
    extra = "options=-csearch_path%3Dlanggraph%2Cpublic"
    query = f"{query.rstrip('&')}&{extra}" if query else extra  # 尾 & 剥掉，防 "&&"
    return urlunsplit((scheme, netloc, path, query, _frag))


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
    except Exception:  # noqa: BLE001  健康检查必须永不抛异常：任何失败都降级为 disconnected
        return "disconnected"
