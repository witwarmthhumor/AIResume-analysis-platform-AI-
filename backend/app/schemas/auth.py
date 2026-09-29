"""注册、登录与当前用户接口的数据合同（v4.1 认证改造后）。

登录/注册拆成两套 schema 的原因：内置管理员口令 123456（6 位）低于注册的
8 位下限——登录必须放行它（校验交给哈希比对），注册则维持强口令门槛。
"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# 手机号（中国大陆）：登录 identifier 三态识别与注册校验共用
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

# 预设头像 key 集合（v4.2）：前端内置 a1~a8 样式，后端只存 key 防任意串
AVATAR_KEYS = {f"a{i}" for i in range(1, 9)}

# 身份证号：15 位纯数字或 18 位（末位可为 X）
_ID_CARD_RE = re.compile(r"^\d{15}$|^\d{17}[\dXx]$")


def is_valid_phone(phone: str) -> bool:
    return bool(_PHONE_RE.match(phone))


def is_valid_id_card(id_card: str) -> bool:
    return bool(_ID_CARD_RE.match(id_card))


class RegisterCredentials(BaseModel):
    """注册：username 过渡期可缺省（服务端按 email 前缀派生），前端强制填写。"""

    username: str | None = Field(default=None, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = Field(
        default=None, max_length=20
    )  # v4.2：前端必填，API 过渡期可选


class LoginCredentials(BaseModel):
    """登录：identifier 三态——username 字段可填 用户名/邮箱/手机号（旧 email 字段兼容）。"""

    username: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdate(BaseModel):
    """个人信息更新（v4.2）：None = 不改；phone 仅在账号尚无手机号时可设置一次。"""

    avatar_key: str | None = Field(default=None, max_length=30)
    id_card: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=20)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    username: str  # v4.1：登录标识（响应新增，前端展示用）
    role: str  # user / admin
    created_at: datetime
    # v4.2 个人信息（全部"本人接口"才返回；手机号/身份证不进日志与其他接口）
    phone: str | None = None
    id_card: str | None = None
    avatar_key: str | None = None


class AuthResponse(BaseModel):
    user: UserOut
