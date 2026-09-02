"""阶段1 接口测试：上传校验、解析、hash 去重、列表与详情。

前置：docker compose 的 db 容器在本机运行（与 test_health 同约定）。
PDF 样本全部用 fpdf2 现造，不依赖外部文件；每个用例结束后清库清 uploads/。
"""

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

from app.api.resumes import UPLOAD_DIR
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


def upload(data: bytes, filename: str = "resume.pdf"):
    return client.post(
        "/api/resumes", files={"file": (filename, data, "application/pdf")}
    )


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例跑完后清掉 resumes 表和 uploads/ 里的文件，用例互不干扰。"""
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM resumes"))
    for f in UPLOAD_DIR.iterdir():
        if f.is_file():
            f.unlink()


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

    resp2 = upload(b"plain text, not a pdf at all", "resume.pdf")
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
    second = upload(data, filename="renamed_copy.pdf")  # 改名不影响：去重看内容 hash
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
        files={"file": ("resume.pdf", data, "application/pdf")},
    )
    assert resp2.status_code == 201
    assert resp2.json()["duplicate"] is False
