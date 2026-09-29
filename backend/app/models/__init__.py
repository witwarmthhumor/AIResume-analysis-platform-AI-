"""模型登记处：import 本文件 = import 所有模型。

Alembic 的 env.py 只 import 这里一次即可；
漏登记某个模型 → autogenerate 静默漏建表（PROJECT-PLAN 点名的经典坑）。
"""

from app.models.agent_v2 import AgentApproval, AgentRun, AgentSpan, AuditLog
from app.models.analysis import Analysis
from app.models.audio_analysis import AudioAnalysis
from app.models.base import Base
from app.models.chat import ChatMessage, ChatSession
from app.models.interview import InterviewMessage, InterviewSession
from app.models.kb import KBChunk, KBDocument
from app.models.question_bank import QuestionBank
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.models.user import User

__all__ = [
    "AgentApproval",
    "AgentRun",
    "AgentSpan",
    "Analysis",
    "AudioAnalysis",
    "AuditLog",
    "Base",
    "ChatMessage",
    "ChatSession",
    "InterviewMessage",
    "InterviewSession",
    "KBChunk",
    "KBDocument",
    "QuestionBank",
    "Resume",
    "UsageLog",
    "User",
]
