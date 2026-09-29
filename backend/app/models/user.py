"""users 表：阶段4 用户账户，密码只存 Argon2 哈希。"""

from sqlalchemy import BigInteger, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # v4.1 企业级改造：登录标识 email → username（历史账号由迁移按 email 前缀回填，
    # 规则见 app/services/username_service.py；迁移侧自含同规则快照，不 import 应用代码）
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 会话版本号：改密 +1 使所有旧 JWT 立即失效（等价全端下线），JWT 带 ver 声明比对
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 「继续上次会话」快捷入口：最近活跃的面试/对话会话 id（nullable，无历史时为空）
    last_active_session_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    role: Mapped[str] = mapped_column(
        String(20), default="user", server_default="user"
    )  # user / admin
