"""注册、登录与当前用户接口的数据合同。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthCredentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: str  # user / admin
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserOut
