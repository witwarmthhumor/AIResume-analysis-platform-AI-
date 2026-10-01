"""GET /api/agent/tools 元数据端点（v4.3 单一数据源）与 registry 一致性。

前端工具中文名从该端点派生（AgentChatCore 不再手写 TOOL_LABELS），
本文件是"端点返回 = registry = make_tools 实际产物"三处一致的回归锚点。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app
from app.services.agent import ToolContext, make_tools
from app.services.agent.tools import TOOL_NAMES, TOOLS_META

client = TestClient(app)


def _register() -> TestClient:
    c = TestClient(app)
    addr = f"toolmeta-{uuid.uuid4().hex[:10]}@example.com"
    c.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    return c


@pytest.fixture(autouse=True)
def _clean_toolmeta_users():
    yield
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM users WHERE email LIKE 'toolmeta-%'"),
        )


def test_agent_tools_endpoint_shape():
    """登录后返回 registry 全量：字段四键、顺序一致、kb_search 始终第一。"""
    c = _register()
    r = c.get("/api/agent/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert [t["name"] for t in tools] == list(TOOL_NAMES)
    for t in tools:
        assert set(t) == {"name", "label", "summary", "llm_backed"}
        assert t["label"] and t["summary"]
    assert tools[0]["name"] == "kb_search"
    llm_backed = {t["name"] for t in tools if t["llm_backed"]}
    assert llm_backed == {"job_match", "question_gen", "answer_review"}


def test_agent_tools_endpoint_requires_login():
    """匿名 401（端点在 /api/** 登录闸门与 get_current_user 双重口径内）。"""
    assert TestClient(app).get("/api/agent/tools").status_code == 401


def test_registry_matches_make_tools():
    """registry 与 make_tools 实际产物强一致（防两处口径漂移）。"""
    tools = make_tools(None, None, None, ToolContext())
    assert [t.name for t in tools] == list(TOOL_NAMES)
    assert [m["name"] for m in TOOLS_META] == list(TOOL_NAMES)
