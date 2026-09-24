"""worker Celery 任务体直测（v3.7 审计覆盖率缺口：worker/tasks.py 31% → 直调补齐）。

Celery task 对象直接调用即执行函数体，无需起 worker/broker。
数据库用真实 SessionLocal（本机护栏已拦截远程库），造数走 worker- / wtest- 专属标记，
清理只删自造数据（v3.7 隔离约定）。
"""

import uuid
from types import SimpleNamespace

import pytest
from fpdf import FPDF
from sqlalchemy import select, text

from app.api.resumes import UPLOAD_DIR
from app.db.session import SessionLocal, engine
from app.models.kb import KBDocument
from app.models.resume import Resume
from app.services.usage_service import count_today_usage_by_owner
from app.worker import tasks as worker_tasks
from app.worker.tasks import analyze_resume, ingest_kb, parse_resume


def _marker() -> str:
    return f"worker-{uuid.uuid4().hex[:10]}"


def _make_pdf(
    pages: int = 1, content: str = "Worker task test resume content."
) -> bytes:
    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 6, content)
    return bytes(pdf.output())


@pytest.fixture(autouse=True)
def _clean_worker_data(monkeypatch):
    """按标记清理 worker 测试造的数据：resumes（文件名前缀）/ kb 文档（标题前缀）/ analyses / usage。

    uploads/ 用快照差集清理（目录里是 {hash}.pdf，无法按前缀过滤）。
    """
    uploads_before = {f.name for f in UPLOAD_DIR.iterdir() if f.is_file()}
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'worker-%'"))
        conn.execute(
            text(
                "DELETE FROM analyses WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'worker-%')"
            )
        )
        conn.execute(text("DELETE FROM usage_logs WHERE anonymous_id LIKE 'worker-%'"))
        conn.execute(
            text(
                "DELETE FROM kb_chunks WHERE document_id IN "
                "(SELECT id FROM kb_documents WHERE title LIKE 'wtest-%')"
            )
        )
        conn.execute(text("DELETE FROM kb_documents WHERE title LIKE 'wtest-%'"))
    for f in UPLOAD_DIR.iterdir():
        if f.is_file() and f.name not in uploads_before:
            f.unlink()


def _create_resume(
    db, marker: str, *, parse_status="success", raw_text="Worker 测试正文。", path=None
):
    storage = path or (UPLOAD_DIR / f"worker-{uuid.uuid4().hex}.pdf")
    resume = Resume(
        filename=f"{marker}.pdf",
        file_hash=uuid.uuid4().hex,
        storage_path=str(storage),
        raw_text=raw_text,
        page_count=1,
        file_size=123,
        parse_status=parse_status,
        anonymous_id=marker,  # 归属标记复用 anonymous_id，便于按它清理 usage
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


# —— parse_resume ——


def test_parse_resume_success_writes_back_fields():
    """成功路径：读文件 → 解析 → raw_text/page_count/file_size 写回，status=success。"""
    marker = _marker()
    pdf = _make_pdf(content="Hello worker parse test.")
    storage = UPLOAD_DIR / f"worker-{uuid.uuid4().hex}.pdf"
    storage.write_bytes(pdf)
    with SessionLocal() as db:
        resume = _create_resume(
            db, marker, parse_status="pending", raw_text=None, path=storage
        )

        result = parse_resume(resume.id)

    assert result["status"] == "success"
    with SessionLocal() as db:
        db.refresh(resume := db.get(Resume, resume.id))
        assert resume.parse_status == "success"
        assert "Hello worker parse test." in resume.raw_text
        assert resume.page_count == 1
        assert resume.parse_error is None


def test_parse_resume_scanned_pdf_marks_unsupported():
    """空白 PDF（无文字）→ unsupported + 友好话术，不抛异常（落库留痕设计）。"""
    marker = _marker()
    blank = FPDF()
    for _ in range(2):
        blank.add_page()
    storage = UPLOAD_DIR / f"worker-{uuid.uuid4().hex}.pdf"
    storage.write_bytes(bytes(blank.output()))
    with SessionLocal() as db:
        resume = _create_resume(
            db, marker, parse_status="pending", raw_text=None, path=storage
        )

        result = parse_resume(resume.id)

    assert result["status"] == "unsupported"
    with SessionLocal() as db:
        assert db.get(Resume, resume.id).parse_error  # 有面向用户的话术


def test_parse_resume_missing_file_raises():
    """storage_path 指向不存在的文件 → 抛 FileNotFoundError（任务级失败，可被 result backend 观测）。"""
    with SessionLocal() as db:
        resume = _create_resume(db, _marker(), path=UPLOAD_DIR / "worker-不存在.pdf")
        with pytest.raises(FileNotFoundError):
            parse_resume(resume.id)


# —— analyze_resume ——


def test_analyze_resume_success_writes_analysis_and_usage(monkeypatch):
    """成功路径：调 AI → analyses 落库 + usage 记 'analysis' 账（v3.7.1 补的 worker 记账）。"""
    marker = _marker()

    fake = SimpleNamespace(
        report={
            "target_position": "后端",
            "strengths": ["x"],
            "predicted_questions": [],
        },
        valid=True,
        model_name="fake-model",
        tokens_prompt=100,
        tokens_completion=200,
        duration_ms=5,
    )
    monkeypatch.setattr(worker_tasks, "call_ai", lambda *a, **k: fake)
    with SessionLocal() as db:
        resume = _create_resume(db, marker)
        before = count_today_usage_by_owner(db, "analysis", anonymous_id=marker)

        result = analyze_resume(resume.id)

    assert result["status"] == "success"
    with SessionLocal() as db:
        after = count_today_usage_by_owner(db, "analysis", anonymous_id=marker)
        assert after == before + 1  # worker 记账生效（v3.7.1 修复的回归锚点）
        from app.models.analysis import Analysis

        analysis = db.scalars(
            select(Analysis).where(Analysis.resume_id == resume.id)
        ).one()
        assert analysis.valid_json is True


def test_analyze_resume_requires_parsed_resume():
    """未成功解析的简历 → ValueError，不调 AI。"""
    with SessionLocal() as db:
        resume = _create_resume(db, _marker(), parse_status="failed", raw_text=None)
        with pytest.raises(ValueError):
            analyze_resume(resume.id)


# —— ingest_kb ——


def _create_kb_doc(db, title: str, status: str = "pending"):
    doc = KBDocument(
        title=title,
        doc_type="text",
        raw_text="ingest 测试内容。" * 5,
        source_type="preset",
        scope="public",  # 列级 NOT NULL，无模型默认值
        status=status,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_ingest_kb_success_marks_ready(monkeypatch):
    """成功路径：processing → ready。ingest_kb_document 打桩（真实入库走 Ollama）。"""
    with SessionLocal() as db:
        doc = _create_kb_doc(db, f"wtest-{uuid.uuid4().hex[:8]}")

        def _fake_ingest(db, d):
            # 真实 service 契约：自己把文档置 ready 再返回 ok（任务体不写状态）
            d.status = "ready"
            db.commit()
            return (True, "")

        monkeypatch.setattr(worker_tasks, "ingest_kb_document", _fake_ingest)
        result = ingest_kb(doc.id)

    assert result["status"] == "ready"
    with SessionLocal() as db:
        assert db.get(KBDocument, doc.id).status == "ready"


def test_ingest_kb_unexpected_failure_marks_failed(monkeypatch):
    """ingest 抛未预期异常 → 回滚后置 failed + 兜底话术，不留 processing 僵尸。"""
    with SessionLocal() as db:
        doc = _create_kb_doc(db, f"wtest-{uuid.uuid4().hex[:8]}")

        def _boom(db, d):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(worker_tasks, "ingest_kb_document", _boom)
        result = ingest_kb(doc.id)

    assert result["status"] == "failed"
    assert "意外错误" in result["error"]
    with SessionLocal() as db:
        assert db.get(KBDocument, doc.id).status == "failed"


def test_ingest_kb_missing_document_raises():
    with pytest.raises(ValueError):
        ingest_kb(99999999)
