"""用户认证接口：注册、登录、登出、当前用户。"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
    db: Session = Depends(get_db),  # noqa: B008
) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == str(credentials.email).lower()))
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
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
