"""v4.3 分页收口的回归锚点：列表接口不再无上限全量返回。

口径：
- /api/admin/users 改 envelope 分页（items/total/limit/offset），服务端裁决翻页；
- /api/resumes、/api/chat/sessions 等列表加 limit/offset 安全上限，响应形状不变；
- /api/history（兼容层）三组列表统一 limit 上限。
测试造数用专属前缀 pgpage-（邮箱/文件名/会话标题），清理只按前缀删，禁止全表 DELETE。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

client = TestClient(app)

_PREFIX = "pgpage-"


def _email(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}@example.com"


def _register(prefix: str, role: str | None = None) -> tuple[TestClient, int]:
    c = TestClient(app)
    addr = _email(prefix)
    c.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    with engine.begin() as conn:
        if role:
            conn.execute(
                text("UPDATE users SET role = :r WHERE email = :e"),
                {"r": role, "e": addr},
            )
        uid = conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": addr}
        ).scalar()
    return c, uid


def _insert_resume(conn, user_id: int, idx: int) -> None:
    conn.execute(
        text(
            "INSERT INTO resumes "
            "(user_id, anonymous_id, filename, file_hash, storage_path, parse_status) "
            "VALUES (:u, NULL, :f, :h, :p, 'success')"
        ),
        {
            "u": user_id,
            "f": f"{_PREFIX}简历{idx}.pdf",
            "h": f"{_PREFIX}{uuid.uuid4().hex}",
            "p": f"pgpage-{idx}.pdf",
        },
    )


def _insert_chat_session(conn, user_id: int, idx: int) -> None:
    conn.execute(
        text(
            "INSERT INTO chat_sessions "
            "(user_id, anonymous_id, session_type, title) "
            "VALUES (:u, NULL, 'chat', :t)"
        ),
        {"u": user_id, "t": f"{_PREFIX}会话{idx}"},
    )


@pytest.fixture(autouse=True)
def _clean_pgpage_data():
    yield
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM resumes WHERE filename LIKE :p OR storage_path LIKE :p"),
            {"p": f"{_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM chat_sessions WHERE title LIKE :p"),
            {"p": f"{_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM users WHERE email LIKE :p"),
            {"p": f"{_PREFIX}%"},
        )


# —— /api/admin/users：envelope 分页 ——


def test_admin_users_envelope_and_pagination():
    """envelope 四键齐全；limit=10 翻页不重不漏；首条是最新注册的用户。"""
    admin, _ = _register(_PREFIX, role="admin")
    created_ids = []
    for _ in range(12):
        _, uid = _register(_PREFIX)
        created_ids.append(uid)
    newest_id = created_ids[-1]

    page1 = admin.get("/api/admin/users", params={"limit": 10, "offset": 0}).json()
    assert set(page1) == {"items", "total", "limit", "offset"}
    assert page1["limit"] == 10 and page1["offset"] == 0
    assert page1["total"] >= 13  # 12 个造数用户 + admin 本身（全局口径，容残留）
    assert len(page1["items"]) == 10
    assert page1["items"][0]["id"] == newest_id  # created_at desc, id desc

    page2 = admin.get("/api/admin/users", params={"limit": 10, "offset": 10}).json()
    ids1 = {u["id"] for u in page1["items"]}
    ids2 = {u["id"] for u in page2["items"]}
    assert len(ids1) == 10 and len(ids2) >= 1
    assert ids1 & ids2 == set()  # 翻页不重叠
    assert newest_id in ids1 and newest_id not in ids2

    # 每页条目都带齐投影字段
    assert set(page1["items"][0]) == {
        "id",
        "username",
        "email",
        "role",
        "is_active",
        "created_at",
        "resume_count",
    }


def test_admin_users_param_bounds():
    """limit/offset 越界一律 422（服务端裁决，不信前端）。"""
    admin, _ = _register(_PREFIX, role="admin")
    assert admin.get("/api/admin/users", params={"limit": 0}).status_code == 422
    assert admin.get("/api/admin/users", params={"limit": 101}).status_code == 422
    assert admin.get("/api/admin/users", params={"offset": -1}).status_code == 422


def test_admin_users_requires_admin():
    """普通用户 403、匿名 401（沿用 _admin_only 守卫口径）。"""
    user, _ = _register(_PREFIX)
    assert user.get("/api/admin/users").status_code == 403
    assert TestClient(app).get("/api/admin/users").status_code == 401


# —— /api/resumes：默认行为不变 + limit 生效 ——


def test_resumes_limit_keeps_default_shape():
    """不带参数仍是 list 且全量可见（默认 100 内）；limit=2 只回最新 2 条。"""
    user, uid = _register(_PREFIX)
    with engine.begin() as conn:
        for i in range(3):
            _insert_resume(conn, uid, i)

    rows = user.get("/api/resumes").json()
    assert isinstance(rows, list) and len(rows) == 3

    limited = user.get("/api/resumes", params={"limit": 2}).json()
    assert len(limited) == 2
    assert limited == rows[:2]  # 同一排序下的前 2 条（最新优先）


# —— /api/chat/sessions：limit 生效 ——


def test_chat_sessions_limit():
    user, uid = _register(_PREFIX)
    with engine.begin() as conn:
        for i in range(3):
            _insert_chat_session(conn, uid, i)

    rows = user.get("/api/chat/sessions").json()
    assert len(rows) == 3
    limited = user.get("/api/chat/sessions", params={"limit": 2}).json()
    assert len(limited) == 2


# —— /api/history：兼容层上限 ——


def test_history_limit_cap():
    """兼容层默认 50 上限：limit=2 时 resumes 只回 2 条，其余组同理封顶。"""
    user, uid = _register(_PREFIX)
    with engine.begin() as conn:
        for i in range(3):
            _insert_resume(conn, uid, i)

    limited = user.get("/api/history", params={"limit": 2}).json()
    assert len(limited["resumes"]) == 2
    assert len(limited["analyses"]) == 0  # 没有分析记录，仅验证键仍在
    assert "interviews" in limited
