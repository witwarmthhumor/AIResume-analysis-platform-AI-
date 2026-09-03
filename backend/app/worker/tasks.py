"""Celery 业务任务：把耗时解析和 AI 分析放到 worker 执行。"""

from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.kb import KBDocument
from app.models.resume import Resume
from app.services.ai_client import analyze_resume as call_ai
from app.services.kb_service import ingest_kb_document
from app.services.pdf_parser import ParseError, parse_pdf
from app.services.prompts import PROMPT_VERSION
from app.worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.worker.tasks.health_check")
def health_check() -> dict[str, str]:
    """用于联调 broker/result backend 的最小任务。"""
    return {"status": "ok"}


@celery_app.task(name="app.worker.tasks.parse_resume")
def parse_resume(resume_id: int) -> dict[str, int | str]:
    """读取已有上传文件，解析正文并写回 resumes。"""
    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        if resume is None:
            raise ValueError("简历记录不存在")
        data = Path(resume.storage_path).read_bytes()
        try:
            result = parse_pdf(data, settings.upload_max_pages)
        except ParseError as exc:
            if exc.kind == "too_many_pages":
                resume.parse_status = "failed"
            else:
                resume.parse_status = exc.kind
            resume.parse_error = exc.message
            db.commit()
            return {"resume_id": resume_id, "status": resume.parse_status}
        resume.raw_text = result.text
        resume.page_count = result.page_count
        resume.file_size = len(data)
        resume.parse_status = "success"
        resume.parse_error = None
        db.commit()
        return {
            "resume_id": resume_id,
            "status": "success",
            "page_count": result.page_count,
        }


@celery_app.task(name="app.worker.tasks.analyze_resume")
def analyze_resume(resume_id: int) -> dict[str, int | str]:
    """读取已解析简历，调用 AI 并写入 analyses。"""
    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        if resume is None or resume.parse_status != "success" or not resume.raw_text:
            raise ValueError("简历尚未成功解析，无法分析")
        result = call_ai(resume.raw_text, settings)
        analysis = Analysis(
            resume_id=resume_id,
            user_id=resume.user_id,
            anonymous_id=resume.anonymous_id,
            model_name=result.model_name,
            prompt_version=PROMPT_VERSION,
            result_json=result.report,
            valid_json=result.valid,
            tokens_prompt=result.tokens_prompt,
            tokens_completion=result.tokens_completion,
            duration_ms=result.duration_ms,
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)
        return {"resume_id": resume_id, "analysis_id": analysis.id, "status": "success"}


@celery_app.task(name="app.worker.tasks.ingest_kb")
def ingest_kb(document_id: int) -> dict[str, int | str]:
    """对知识库文档切块向量化并置 ready（v3.0：用户上传走异步入库）。

    失败置 failed（parse_error 留话术），可对同一 document 重试——ingest 幂等。
    """
    with SessionLocal() as db:
        document = db.get(KBDocument, document_id)
        if document is None or document.deleted_at is not None:
            raise ValueError("知识库文档不存在或已删除")
        document.status = "processing"
        db.commit()
        try:
            ok, message = ingest_kb_document(db, document)
        except Exception:
            # 兜底：任何未预期异常都不能把文档留在 processing（用户会一直看到"入库中"）
            db.rollback()
            logger.exception("ingest_kb 意外失败 document_id=%s", document_id)
            document.status = "failed"
            document.parse_error = "入库过程发生意外错误，请稍后重试"
            db.commit()
            return {
                "document_id": document_id,
                "status": "failed",
                "error": document.parse_error,
            }
        return {
            "document_id": document_id,
            "status": "ready" if ok else "failed",
            "error": None if ok else message,
        }
