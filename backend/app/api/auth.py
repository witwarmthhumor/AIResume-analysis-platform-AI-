"""用户认证接口：注册、登录、登出、当前用户。"""

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth_deps import ACCESS_COOKIE, get_current_user
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import AuthCredentials, AuthResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

# secure/max_age 走配置：本地 HTTP 为 False，生产 HTTPS 在 .env 开 JWT_SECURE_COOKIE；
# 有效期与 JWT 过期时间保持同一来源，避免"cookie 还在但 token 已过期"
_COOKIE_KWARGS = {
    "httponly": True,
    "samesite": "lax",
    "secure": settings.jwt_secure_cookie,
    "max_age": settings.jwt_expire_minutes * 60,
}

# 登录失败锁定（防爆破）：按 IP+邮箱记最近失败时间戳，窗口内超限即 429。
# 内存实现——单 worker 部署够用，进程重启即解锁；多 worker 部署需换 Redis 集中计数
_login_failures: dict[str, list[float]] = {}
_FAILURE_WINDOW_SECONDS = settings.login_lockout_minutes * 60


def _recent_failure_count(key: str, now: float) -> int:
    """取窗口内的失败次数（顺手清掉过期记录，避免字典无限增长）。"""
    recent = [
        t for t in _login_failures.get(key, []) if now - t < _FAILURE_WINDOW_SECONDS
    ]
    _login_failures[key] = recent
    return len(recent)


@router.post(
    "/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
def register(
    credentials: AuthCredentials,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> AuthResponse:
    email = str(credentials.email).lower()
    # 首个注册用户自动成为 admin（P6 只读管理面板入口）
    is_first_user = db.scalar(select(func.count()).select_from(User)) == 0
    user = User(
        email=email,
        password_hash=hash_password(credentials.password),
        role="admin" if is_first_user else "user",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该邮箱已注册") from None
    db.refresh(user)
    response.set_cookie(
        ACCESS_COOKIE, create_access_token(user.id), **_COOKIE_KWARGS
    )
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthResponse)
def login(
    credentials: AuthCredentials,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> AuthResponse:
    email = str(credentials.email).lower()
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{email}"
    now = time.monotonic()
    if _recent_failure_count(key, now) >= settings.login_max_failures:
        raise HTTPException(
            429,
            f"登录失败次数过多，请 {settings.login_lockout_minutes} 分钟后再试",
        )
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(credentials.password, user.password_hash):
        # 失败不区分"邮箱不存在/密码错误"——不给爆破者枚举线索
        _login_failures.setdefault(key, []).append(now)
        raise HTTPException(401, "邮箱或密码错误")
    _login_failures.pop(key, None)  # 登录成功清空该组合的失败记录
    response.set_cookie(
        ACCESS_COOKIE, create_access_token(user.id), **_COOKIE_KWARGS
    )
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:  # noqa: B008
    return user
