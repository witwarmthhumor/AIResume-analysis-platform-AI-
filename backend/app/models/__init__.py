"""模型登记处：import 本文件 = import 所有模型。

Alembic 的 env.py 只 import 这里一次即可；
漏登记某个模型 → autogenerate 静默漏建表（PROJECT-PLAN 点名的经典坑）。
"""

from app.models.analysis import Analysis
from app.models.base import Base
from app.models.interview import InterviewMessage, InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog

__all__ = ["Base", "Analysis", "InterviewMessage", "InterviewSession", "Resume", "UsageLog"]
