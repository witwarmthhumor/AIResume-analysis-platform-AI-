"""注册、登录与当前用户接口的数据合同（v4.1 认证改造后）。

登录/注册拆成两套 schema 的原因：内置管理员口令 123456（6 位）低于注册的
8 位下限——登录必须放行它（校验交给哈希比对），注册则维持强口令门槛。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterCredentials(BaseModel):
    """注册：username 过渡期可缺省（服务端按 email 前缀派生），A4 前端起强制。"""

    username: str | None = Field(default=None, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginCredentials(BaseModel):
    """登录：username 与旧 email 字段二选一（后者为老客户端兼容，值含 @ 即按邮箱查）。"""

    username: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    username: str  # v4.1：登录标识（响应新增，前端展示用）
    role: str  # user / admin
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserOut
