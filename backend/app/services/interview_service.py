"""面试会话服务（v4.0 M3 下沉 D4）：创建逻辑从 api/interviews.py 迁出。

v1 路由与 v2 HITL 风险动作执行器调同一份函数——风险动作的持久化副作用
只允许走这里，保证审计口径与归属校验单点。
"""

from sqlalchemy.orm import Session

from app.models.interview import InterviewMessage, InterviewSession
from app.models.resume import Resume
from app.services.interview_prompts import (
    OPENING_MESSAGE,
)


def create_session(
    db: Session,
    resume_id: int,
    *,
    user_id: int | None,
    anonymous_id: str | None,
    position_type: str | None = None,
) -> InterviewSession:
    """为归属者创建 in_progress 面试会话并写入开场白（不耗 AI 调用）。

    调用方负责：简历归属与解析状态的预先校验（v1 路由已有 404/400 语义）。
    已有同简历进行中会话时直接复用（沿用 v1 路由的去重行为；超时废弃逻辑留在
    API 层——它依赖请求时刻的判断，任务侧创建总是新建，由面试超时机制兜底）。
    """
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise ValueError("简历记录不存在或已删除")
    if resume.parse_status != "success" or not resume.raw_text:
        raise ValueError("该简历未成功解析出文本，无法开始面试")

    session = InterviewSession(
        resume_id=resume_id,
        user_id=user_id,
        anonymous_id=anonymous_id,
        position_type=position_type,
    )
    db.add(session)
    db.flush()  # 拿到 session.id 给开场白用
    db.add(
        InterviewMessage(
            session_id=session.id, role="interviewer", content=OPENING_MESSAGE
        )
    )
    db.commit()
    db.refresh(session)
    return session
