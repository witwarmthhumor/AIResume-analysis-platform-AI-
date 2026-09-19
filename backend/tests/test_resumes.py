"""阶段1 接口测试：上传校验、解析、hash 去重、列表与详情。

前置：docker compose 的 db 容器在本机运行（与 test_health 同约定）。
PDF 样本全部用 fpdf2 现造，不依赖外部文件；用后按文件名标记清理（v3.7 测试隔离改造）。
"""

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

from app.api.resumes import UPLOAD_DIR
from app.core.config import settings
from app.db.session import engine
from app.main import app

client = TestClient(app)


def make_text_pdf(pages: list[str]) -> bytes:
    """造文本型 PDF：列表每个元素一页，元素内容为该页文字。"""
    pdf = FPDF()
    for content in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 6, content)
    return bytes(pdf.output())


def make_blank_pdf(page_count: int = 2) -> bytes:
    """造无文字 PDF：只有空白页，模拟扫描件/图片型 PDF。"""
    pdf = FPDF()
    for _ in range(page_count):
        pdf.add_page()
    return bytes(pdf.output())


def upload(data: bytes, filename: str = "rt-resume.pdf"):
    return client.post(
        "/api/resumes", files={"file": (filename, data, "application/pdf")}
    )


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """按标记清理本文件造的简历（rt- 前缀）；uploads/ 只删测试期间新增的文件。

    - uploads/ 快照式清理：目录里存的是 {hash}.pdf，按文件名前缀过滤不可行，
      快照差集既能清掉自造文件又不会误删手动上传的真实简历（P1 修复）。
    - usage_logs 用 id 快照兜底（上传现在会记 parse 账），防跨文件计数残留。
    - 抬高每日上传限额：本文件用例多，避免用例总数撞上真实限额（限额另有专测）。
    """
    monkeypatch.setattr(settings, "daily_upload_limit", 1000)
    with engine.begin() as conn:
        usage_snap = conn.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM usage_logs")
        ).scalar()
    uploads_before = {f.name for f in UPLOAD_DIR.iterdir() if f.is_file()}
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'rt-%'"))
        conn.execute(text("DELETE FROM usage_logs WHERE id > :s"), {"s": usage_snap})
    for f in UPLOAD_DIR.iterdir():
        if f.is_file() and f.name not in uploads_before:
            f.unlink()


def test_upload_daily_limit(monkeypatch) -> None:
    """P1 回归：简历上传接入每日限额——超限 429，不再对匿名敞开无限上传。"""
    monkeypatch.setattr(settings, "daily_upload_limit", 1)
    first = upload(make_text_pdf(["limit test one content, enough text here"]))
    assert first.status_code == 201
    second = upload(make_text_pdf(["limit test two, a different body entirely"]))
    assert second.status_code == 429
    assert "每日上限" in second.json()["message"]


def test_upload_text_pdf_returns_raw_text() -> None:
    resp = upload(
        make_text_pdf(
            ["Zhang San - Python Developer, 3 years experience. Built APIs and tools."]
            * 1
        )
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["duplicate"] is False
    resume = body["resume"]
    assert resume["parse_status"] == "success"
    assert "Zhang San" in resume["raw_text"]
    assert resume["page_count"] == 1
    assert resume["file_size"] > 0


def test_non_pdf_rejected_415() -> None:
    """非 PDF：扩展名不对、或扩展名对但文件头不是 %PDF-，都拦下且不落库。"""
    resp = upload(b"PK\x03\x04 zip content", "resume.zip")
    assert resp.status_code == 415

    resp2 = upload(b"plain text, not a pdf at all", "rt-resume.pdf")
    assert resp2.status_code == 415

    assert client.get("/api/resumes").json() == []


def test_oversize_rejected_413() -> None:
    big = b"%PDF-1.4" + b"0" * (5 * 1024 * 1024 + 1)  # 文件头合法、内容超 5MB
    resp = upload(big)
    assert resp.status_code == 413
    assert "5MB" in resp.json()["message"]


def test_too_many_pages_rejected_400() -> None:
    resp = upload(make_text_pdf(["page content with enough text here"] * 6))
    assert resp.status_code == 400
    assert "页" in resp.json()["message"]
    assert client.get("/api/resumes").json() == []


def test_scanned_pdf_saved_as_unsupported() -> None:
    """扫描件：落库留痕（201 + unsupported），给出友好话术。"""
    resp = upload(make_blank_pdf(page_count=2))
    assert resp.status_code == 201
    resume = resp.json()["resume"]
    assert resume["parse_status"] == "unsupported"
    assert "扫描件" in resume["parse_error"]
    assert resume["raw_text"] is None


def test_corrupt_pdf_saved_as_failed() -> None:
    resp = upload(b"%PDF-1.4 totally broken garbage, not a real pdf body")
    assert resp.status_code == 201
    resume = resp.json()["resume"]
    assert resume["parse_status"] == "failed"
    assert resume["parse_error"]


def test_duplicate_upload_returns_existing_record() -> None:
    data = make_text_pdf(["Dedup sample resume with enough text content."])
    first = upload(data)
    second = upload(data, filename="rt-renamed_copy.pdf")  # 改名不影响：去重看内容 hash
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert first.json()["resume"]["id"] == second.json()["resume"]["id"]

    records = client.get("/api/resumes").json()
    assert len(records) == 1  # 没有重复落库


def test_list_and_detail_and_404() -> None:
    upload(make_text_pdf(["List and detail test resume content here, enough."]))
    lst = client.get("/api/resumes")
    assert lst.status_code == 200
    record = lst.json()[0]
    assert "raw_text" not in record  # 列表不带正文

    detail = client.get(f"/api/resumes/{record['id']}")
    assert detail.status_code == 200
    assert "raw_text" in detail.json()

    assert client.get("/api/resumes/999999").status_code == 404


def test_soft_delete_hides_resume() -> None:
    data = make_text_pdf(["Soft delete sample content"])
    rid = upload(data).json()["resume"]["id"]
    resp = client.delete(f"/api/resumes/{rid}")
    assert resp.status_code == 204
    assert client.get(f"/api/resumes/{rid}").status_code == 404
    assert client.get("/api/resumes").json() == []
    # 同文件可重新上传（删除后不阻塞 hash 去重）
    resp2 = client.post(
        "/api/resumes",
        files={"file": ("rt-resume.pdf", data, "application/pdf")},
    )
    assert resp2.status_code == 201
    assert resp2.json()["duplicate"] is False


def test_anonymous_isolation_cannot_read_or_delete_others() -> None:
    """P0 越权回归：匿名用户 A 的简历，匿名用户 B 不可读、不可删、列表互不可见。

    历史缺陷：匿名上传不写 anonymous_id 且归属只按 user_id IS NULL 判定，
    任意匿名访客可凭自增 ID 读取/软删除他人匿名简历（含 raw_text 隐私全文）。
    """
    client_a = TestClient(app)
    resp = client_a.post(
        "/api/resumes",
        files={
            "file": (
                "rt-iso-a.pdf",
                make_text_pdf(["Isolation test A"]),
                "application/pdf",
            )
        },
    )
    assert resp.status_code == 201
    rid = resp.json()["resume"]["id"]

    client_b = TestClient(app)  # 全新匿名身份（独立 cookie）
    assert client_b.get(f"/api/resumes/{rid}").status_code == 404
    assert client_b.get("/api/resumes").json() == []
    assert client_b.delete(f"/api/resumes/{rid}").status_code == 404

    # A 自己仍可正常访问（确认修复没有误伤）
    assert client_a.get(f"/api/resumes/{rid}").status_code == 200
