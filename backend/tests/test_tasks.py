"""阶段4 Celery 任务接口测试：提交、状态查询与未登录保护。"""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_task_submission_requires_login() -> None:
    assert client.post("/api/tasks/parse-resume/1").status_code == 401
    assert client.post("/api/tasks/analyze-resume/1").status_code == 401


def test_health_task_submission(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.tasks.health_check",
        SimpleNamespace(delay=lambda: SimpleNamespace(id="test-task-id")),
    )
    # health-check 现在要求登录（v3.7 审计修复：调试入口不对外开放）
    client.post(
        "/api/auth/register",
        json={
            "email": f"tasks-{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct-horse-123",
        },
    )
    response = client.post("/api/tasks/health-check")
    assert response.status_code == 200
    assert response.json() == {"task_id": "test-task-id", "status": "pending"}


def test_health_task_requires_login(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.tasks.health_check",
        SimpleNamespace(delay=lambda: SimpleNamespace(id="test-task-id")),
    )
    anon = TestClient(app)
    assert anon.post("/api/tasks/health-check").status_code == 401
