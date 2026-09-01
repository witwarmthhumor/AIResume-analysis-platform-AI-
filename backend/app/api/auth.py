"""用户认证接口：注册、登录、登出、当前用户。"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth_deps import ACCESS_COOKIE, get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import AuthCredentials, AuthResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_KWARGS = {"httponly": True, "samesite": "lax", "secure": False}


@router.post(
    "/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
def register(
    credentials: AuthCredentials, response: Response, db: Session = Depends(get_db)  # noqa: B008
) -> AuthResponse:
    email = str(credentials.email).lower()
    user = User(email=email, password_hash=hash_password(credentials.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该邮箱已注册") from None
    db.refresh(user)
    response.set_cookie(
        ACCESS_COOKIE, create_access_token(user.id), max_age=86400, **_COOKIE_KWARGS
    )
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthResponse)
def login(
    credentials: AuthCredentials, response: Response, db: Session = Depends(get_db)  # noqa: B008
) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == str(credentials.email).lower()))
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
    response.set_cookie(
        ACCESS_COOKIE, create_access_token(user.id), max_age=86400, **_COOKIE_KWARGS
    )
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:  # noqa: B008
    return user
