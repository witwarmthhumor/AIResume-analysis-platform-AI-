"""pytest 全局护栏。

测试 fixture 会真实清空 kb_documents / kb_chunks / usage_logs 等表，
所以测试库连接必须是本机——一旦 DATABASE_URL 指向远程或生产库，
跑一次 pytest 就等于清库。这里在会话启动前拦截，直接终止。
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
