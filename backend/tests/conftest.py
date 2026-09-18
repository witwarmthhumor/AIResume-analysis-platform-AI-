"""pytest 全局护栏。

v3.7 测试隔离改造后，fixture 只按标记清理各文件自造的数据（预置语料与真实数据
不受影响）。本护栏作为最后一道防线保留：一旦 DATABASE_URL 指向远程或生产库，
任何清理语句都可能变成清库事故，在会话启动前直接拦截终止。
"""

import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}


def pytest_configure(config) -> None:
    host = (make_url(settings.database_url).host or "").lower()
    if host not in _ALLOWED_HOSTS:
        pytest.exit(
            f"测试会清空数据库表，拒绝连接非本机数据库（host={host}）。"
            "如确属本机，请把 DATABASE_URL 的 host 写成 localhost/127.0.0.1。",
            returncode=1,
        )
