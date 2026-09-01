"""认证依赖：从 HttpOnly cookie 解析当前用户，并统一处理未登录请求。"""

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

ACCESS_COOKIE = "access_token"


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    db: Session = Depends(get_db),  # noqa: B008
) -> User:
    if not access_token:
        raise HTTPException(401, "请先登录")
    user_id = decode_access_token(access_token.removeprefix("Bearer "))
    user = db.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise HTTPException(401, "登录已失效，请重新登录")
    return user
