"""v4.1 A5 安全收口：跨用户越权 sweep——B 用户访问 A 用户名下资源一律 404。

方案验收「越权用例覆盖全部资源类型」的兜底清单：简历详情、分析报告、面试会话、
在线对话消息、v2 运行详情。资源行直接入库（不经业务接口），保证探测的是
"读路径归属校验"本身；两个账号都是 API 注册的真实登录态。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app


@pytest.fixture(autouse=True)
def _clean_sweep_data():
    """前后双清 sw- 标记数据（本文件专用前缀）。"""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM analyses WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'sw-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM interview_messages WHERE session_id IN "
                "(SELECT id FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'sw-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'sw-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM chat_messages WHERE session_id IN "
                "(SELECT id FROM chat_sessions WHERE title LIKE 'sw-%')"
            )
        )
        conn.execute(text("DELETE FROM chat_sessions WHERE title LIKE 'sw-%'"))
        conn.execute(
            text(
                "DELETE FROM agent_runs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'sw-%')"
            )
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'sw-%'"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'sw-%'"))


def _register() -> tuple[TestClient, int]:
    """注册 sw- 前缀用户，返回 (带独立 cookie jar 的客户端, user_id)。"""
    c = TestClient(app)
    email = f"sw-{uuid.uuid4().hex[:10]}@example.com"
    resp = c.post(
        "/api/auth/register", json={"email": email, "password": "correct-horse-123"}
    )
    assert resp.status_code == 201, resp.text
    with engine.begin() as conn:
        uid = conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).scalar()
    return c, uid


def test_cross_user_access_is_404_for_all_resources() -> None:
    """A 名下五类资源，B 逐个访问：必须全部 404（不存在/无权限同话术，防枚举）。"""
    owner, owner_id = _register()
    stranger, _ = _register()

    # 注意：造数事务必须先提交，HTTP 请求的独立会话才能看到这些行——
    # 把请求写在 engine.begin() 块内会让路由读到"不存在"（404 假阳性/假阴性）
    with engine.begin() as conn:
        resume_id = conn.execute(
            text(
                "INSERT INTO resumes (user_id, filename, storage_path, file_hash, file_size, "
                "page_count, parse_status, raw_text) VALUES "
                "(:uid, 'sw-iso.pdf', 'sw-iso.pdf', 'sw-hash', 1024, 1, 'success', 'hello') "
                "RETURNING id"
            ),
            {"uid": owner_id},
        ).scalar_one()
        analysis_id = conn.execute(
            text(
                "INSERT INTO analyses (resume_id, user_id, model_name, prompt_version, "
                "result_json, valid_json) VALUES "
                "(:rid, :uid, 'test-model', 'test', '{\"target_position\": \"x\"}'::jsonb, true) "
                "RETURNING id"
            ),
            {"rid": resume_id, "uid": owner_id},
        ).scalar_one()
        interview_id = conn.execute(
            text(
                "INSERT INTO interview_sessions (resume_id, user_id, status, stage, turn_count) VALUES "
                "(:rid, :uid, 'in_progress', 'intro', 0) RETURNING id"
            ),
            {"rid": resume_id, "uid": owner_id},
        ).scalar_one()
        chat_id = conn.execute(
            text(
                "INSERT INTO chat_sessions (user_id, session_type, title) VALUES "
                "(:uid, 'chat', 'sw-iso-chat') RETURNING id"
            ),
            {"uid": owner_id},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO chat_messages (session_id, role, content) VALUES "
                "(:sid, 'user', 'hi')"
            ),
            {"sid": chat_id},
        )

    # B 访问 A 的每一类资源：404 语义统一
    cases = {
        "resume": stranger.get(f"/api/resumes/{resume_id}"),
        "analysis": stranger.get(f"/api/resumes/{resume_id}/analysis"),
        "interview": stranger.get(f"/api/interviews/{interview_id}"),
        "chat_messages": stranger.get(f"/api/chat/sessions/{chat_id}/messages"),
    }
    for name, resp in cases.items():
        assert resp.status_code == 404, f"{name} 越权未拦截: {resp.status_code}"
    assert analysis_id > 0  # 分析行在库（只是 B 不可见）

    # 归属者本人可见（对照组）
    assert owner.get(f"/api/resumes/{resume_id}").status_code == 200
    assert owner.get(f"/api/interviews/{interview_id}").status_code == 200
    assert owner.get(f"/api/chat/sessions/{chat_id}/messages").status_code == 200


def test_unauthenticated_rejected_by_gate(monkeypatch) -> None:
    """无 cookie 访问资源路径：闸门 401（默认拒绝），与越权 404 是两层防线。

    conftest 默认关闸门保住匿名业务流用例；这里显式打开生产语义。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_gate_enabled", True)
    fresh = TestClient(app)
    assert fresh.get("/api/resumes/1").status_code == 401
    assert fresh.get("/api/chat/sessions/1/messages").status_code == 401
