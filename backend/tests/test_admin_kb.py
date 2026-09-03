"""v3.1 管理员语料库管理测试：权限、列所有文档、删任意文档（含预置）、上传预置 public。"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app


class _FakeTask:
    """替身 Celery 任务：只记录被投递的 document_id，不触发真实入库。"""

    def __init__(self):
        self.delayed = []

    def delay(self, document_id):
        self.delayed.append(document_id)

        class _Result:
            id = "fake-task-id"

        return _Result()


def _email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


def _make_admin(client: TestClient) -> str:
    """注册用户并提权为 admin，返回邮箱。"""
    addr = _email()
    client.post(
        "/api/auth/register", json={"email": addr, "password": "correct-horse-123"}
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = 'admin' WHERE email = :email"),
            {"email": addr},
        )
    return addr


@pytest.fixture(autouse=True)
def _fake_ingest(monkeypatch):
    monkeypatch.setattr("app.api.admin_kb.ingest_kb", _FakeTask())


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_chunks"))
        conn.execute(text("DELETE FROM kb_documents"))
        conn.execute(text("DELETE FROM usage_logs WHERE action_type = 'kb_upload'"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'test-%@example.com'"))


# —— 权限 ——


def test_anonymous_cannot_access_admin_kb() -> None:
    client = TestClient(app)
    assert client.get("/api/admin/kb/documents").status_code == 401
    assert client.delete("/api/admin/kb/documents/1").status_code == 401
    assert (
        client.post(
            "/api/admin/kb/documents",
            files={"file": ("note.txt", b"content", "text/plain")},
        ).status_code
        == 401
    )


def test_regular_user_cannot_access_admin_kb() -> None:
    client = TestClient(app)
    client.post(
        "/api/auth/register", json={"email": _email(), "password": "correct-horse-123"}
    )
    assert client.get("/api/admin/kb/documents").status_code == 403
    assert client.delete("/api/admin/kb/documents/1").status_code == 403


# —— 列所有文档 ——


def test_admin_lists_all_documents_with_owner() -> None:
    admin = TestClient(app)
    _make_admin(admin)

    # admin 上传一篇预置
    resp = admin.post(
        "/api/admin/kb/documents",
        files={"file": ("preset.txt", "预置文档内容".encode() * 5, "text/plain")},
    )
    assert resp.status_code == 201
    preset_id = resp.json()["id"]

    # 普通用户上传一篇 private
    user = TestClient(app)
    user.post(
        "/api/auth/register", json={"email": _email(), "password": "correct-horse-123"}
    )
    user_resp = user.post(
        "/api/kb/documents",
        files={"file": ("private.txt", "私人文档内容".encode() * 5, "text/plain")},
    )
    assert user_resp.status_code == 201

    # admin 列表能看到两篇
    listing = admin.get("/api/admin/kb/documents").json()
    ids = [d["id"] for d in listing]
    assert preset_id in ids
    assert any(
        d["source_type"] == "preset" and d["owner_email"] == "系统预置" for d in listing
    )
    assert any(d["source_type"] == "uploaded" for d in listing)


# —— 删除任意文档 ——


def test_admin_can_delete_preset_document() -> None:
    """admin 可删除预置文档（普通接口不允许）。"""
    admin = TestClient(app)
    _make_admin(admin)

    resp = admin.post(
        "/api/admin/kb/documents",
        files={"file": ("to-delete.txt", "待删除预置内容".encode() * 5, "text/plain")},
    )
    doc_id = resp.json()["id"]

    # 普通接口删预置 → 400
    anon = TestClient(app)
    assert anon.delete(f"/api/kb/documents/{doc_id}").status_code == 400

    # admin 删 → 204
    assert admin.delete(f"/api/admin/kb/documents/{doc_id}").status_code == 204

    # 列表不再出现
    listing = admin.get("/api/admin/kb/documents").json()
    assert not any(d["id"] == doc_id for d in listing)


def test_admin_delete_nonexistent_404() -> None:
    admin = TestClient(app)
    _make_admin(admin)
    assert admin.delete("/api/admin/kb/documents/999999").status_code == 404


# —— 上传为预置 public ——


def test_admin_upload_becomes_preset_public() -> None:
    admin = TestClient(app)
    _make_admin(admin)

    resp = admin.post(
        "/api/admin/kb/documents",
        files={
            "file": (
                "admin-preset.txt",
                "管理员上传的预置内容".encode() * 5,
                "text/plain",
            )
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_type"] == "preset"
    assert data["scope"] == "public"
    assert data["owner_email"] == "系统预置"
    assert data["status"] == "pending"
    assert "task_id" in data

    # 匿名用户能在普通列表里看到这篇预置
    anon = TestClient(app)
    anon_listing = anon.get("/api/kb/documents").json()
    assert any(d["id"] == data["id"] for d in anon_listing)


def test_admin_upload_dedup_globally() -> None:
    """同内容预置文档全局去重，不重复创建。"""
    admin = TestClient(app)
    _make_admin(admin)
    content = "全局去重测试内容".encode() * 5

    first = admin.post(
        "/api/admin/kb/documents", files={"file": ("a.txt", content, "text/plain")}
    )
    second = admin.post(
        "/api/admin/kb/documents", files={"file": ("b.txt", content, "text/plain")}
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


def test_admin_upload_unsupported_format_415() -> None:
    admin = TestClient(app)
    _make_admin(admin)
    resp = admin.post(
        "/api/admin/kb/documents",
        files={"file": ("doc.docx", b"not supported", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_admin_upload_empty_content_400() -> None:
    admin = TestClient(app)
    _make_admin(admin)
    resp = admin.post(
        "/api/admin/kb/documents",
        files={"file": ("empty.txt", b"   \n  ", "text/plain")},
    )
    assert resp.status_code == 400
