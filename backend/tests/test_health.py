"""冒烟测试：应用能起、/health 通、数据库连通。

前置：docker compose 的 db 容器在本机运行（数据库断开时此测试会失败，属预期）。
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_200_with_db_status() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "connected"
    assert body["status"] == "ok"
    assert body["version"]
