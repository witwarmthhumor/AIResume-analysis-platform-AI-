"""认证依赖：从 HttpOnly cookie 解析当前用户，并统一处理未登录请求。

v4.1 A2：JWT 增加 ver（token_version）声明——与库中用户当前版本比对，
不一致（含旧版无 ver 的 token）一律 401，实现"改密即全端下线"。
"""

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

ACCESS_COOKIE = "access_token"


def _load_user(access_token: str | None, db: Session) -> tuple[User | None, bool]:
    """共享解析：返回 (用户, ver 是否有效)。token 缺失/解码失败返回 (None, False)。"""
    if not access_token:
        return None, False
    decoded = decode_access_token(access_token.removeprefix("Bearer "))
    if decoded is None:
        return None, False
    user_id, ver = decoded
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None, False
    return user, ver is not None and ver == user.token_version


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    db: Session = Depends(get_db),  # noqa: B008
) -> User:
    user, ver_ok = _load_user(access_token, db)
    if user is None:
        raise HTTPException(401, "请先登录")
    if not ver_ok:
        # token 版本过期：改密/强制下线后的旧凭证到此为止（与未登录同一 401 语义）
        raise HTTPException(401, "登录已失效，请重新登录")
    return user


def get_optional_current_user(
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    db: Session = Depends(get_db),  # noqa: B008
) -> User | None:
    """可选认证：匿名阶段保持兼容，带合法 JWT（含 ver 匹配）时返回用户。"""
    user, ver_ok = _load_user(access_token, db)
    if user is None or not ver_ok:
        return None
    return user
