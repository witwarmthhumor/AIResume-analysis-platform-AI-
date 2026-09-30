"""用户认证接口：注册（用户名）、登录（用户名/邮箱兼容）、改密、登出、当前用户。

v4.1 企业级改造·A2（方案 §3）：
- 注册支持自选 username（过渡期不传则按 email 前缀派生，老客户端零感知）；
- 登录入参改为 username，兼容邮箱值与旧 email 字段（含 @ 即按邮箱查）；
- 新增 change-password：改密 +1 token_version 使所有旧 JWT 立即失效（全端下线）；
- 登录失败锁定从进程内字典迁 Redis（多 worker 正确，fail-open，见 login_throttle）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth_deps import ACCESS_COOKIE, get_current_user
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginCredentials,
    RegisterCredentials,
    UserOut,
    is_valid_phone,
)
from app.services.login_throttle import failure_count, record_failure, reset_failures
from app.services.username_service import derive_username_db, is_valid_username

router = APIRouter(prefix="/api/auth", tags=["auth"])

# secure/max_age 走配置：本地 HTTP 为 False，生产 HTTPS 在 .env 开 JWT_SECURE_COOKIE；
# 有效期与 JWT 过期时间保持同一来源，避免"cookie 还在但 token 已过期"
_COOKIE_KWARGS = {
    "httponly": True,
    "samesite": "lax",
    "secure": settings.jwt_secure_cookie,
    "max_age": settings.jwt_expire_minutes * 60,
}

# 防用户名枚举的时序均等化：账号不存在也跑一次真哈希校验，让"查无此人"与
# "密码错误"的响应时间不可区分。哈希在 import 时算一次（argon2 百毫秒级），运行期只 verify
_DUMMY_PASSWORD_HASH = hash_password("timing-equalization-dummy")


def _issue_session(response: Response, user: User) -> None:
    """注册/登录成功后统一签发会话 cookie；JWT 带 ver 声明（token_version）。"""
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(user.id, user.token_version),
        **_COOKIE_KWARGS,
    )


@router.post(
    "/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
def register(
    credentials: RegisterCredentials,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> AuthResponse:
    # v4.2.1：email 可选（新账号不再收集邮箱）；username 缺省时若有 email 则按前缀派生，
    # 两者都没有 → 422（username 是登录标识，必须有）
    email = str(credentials.email).lower() if credentials.email else None
    if credentials.username is not None:
        username = credentials.username.strip().lower()
        if not is_valid_username(username):
            raise HTTPException(
                422, "用户名需为 3~64 位小写字母、数字或下划线，且不能用保留字"
            )
    elif email:
        # 按候选逐个探测存在性（S-4：不把全表 username 拉进内存）
        username = derive_username_db(db, email)
    else:
        raise HTTPException(422, "请填写用户名")
    # v4.2：手机号（前端必填，API 过渡期可选）——校验格式；唯一冲突走 IntegrityError 409
    phone = (credentials.phone or "").strip() or None
    if phone is not None:
        if not is_valid_phone(phone):
            raise HTTPException(422, "手机号格式不正确（11 位，1 开头）")
        if db.scalar(select(User).where(User.phone == phone)) is not None:
            raise HTTPException(409, "该手机号已注册")
    # 首个注册用户自动提权受开关控制且默认关（A1）；管理员唯一来源是 seed_admin.py。
    # 事务级咨询锁串行化"查计数 → 插入"窗口，防并发首注产生双 admin
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('first-user')::bigint)"))
    is_first_user = db.scalar(select(func.count()).select_from(User)) == 0
    promote = settings.auto_promote_first_user and is_first_user
    user = User(
        email=email,
        username=username,
        password_hash=hash_password(credentials.password),
        phone=phone,
        role="admin" if promote else "user",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # 409 明确话术区分撞了哪个唯一键（注册页不是防枚举重点，明确比含糊有用）
        # S-3：email 可空后，email IS NULL 查询永远不命中——只有 email 非空才查邮箱分支
        if (
            email is not None
            and db.scalar(select(User).where(User.email == email)) is not None
        ):
            raise HTTPException(409, "该邮箱已注册") from None
        if (
            phone is not None
            and db.scalar(select(User).where(User.phone == phone)) is not None
        ):
            raise HTTPException(409, "该手机号已注册") from None
        raise HTTPException(409, "该用户名已被占用") from None
    db.refresh(user)
    _issue_session(response, user)
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthResponse)
def login(
    credentials: LoginCredentials,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> AuthResponse:
    # identifier 三态（v4.2）：含 @ 按邮箱查；1 开头 11 位数字按手机号查；否则按用户名。
    # 旧 email 字段兼容（测试/老前端零改动）
    identifier = (credentials.username or credentials.email or "").strip().lower()
    if not identifier:
        raise HTTPException(422, "请输入用户名、邮箱或手机号")
    client_ip = request.client.host if request.client else "unknown"
    throttle_key = f"{client_ip}:{identifier}"
    if failure_count(throttle_key) >= settings.login_max_failures:
        raise HTTPException(
            429,
            f"登录失败次数过多，请 {settings.login_lockout_minutes} 分钟后再试",
        )
    if "@" in identifier:
        lookup = select(User).where(User.email == identifier)
    elif is_valid_phone(identifier):
        lookup = select(User).where(User.phone == identifier)
    else:
        lookup = select(User).where(User.username == identifier)
    user = db.scalar(lookup)
    # 时序均等化：无论账号是否存在都跑一次真哈希校验
    password_ok = verify_password(
        credentials.password, user.password_hash if user else _DUMMY_PASSWORD_HASH
    )
    if user is None or not password_ok or not user.is_active:
        # 失败不区分"账号不存在/密码错误/已停用"——不给爆破者枚举线索
        record_failure(throttle_key, settings.login_lockout_minutes * 60)
        raise HTTPException(401, "账号或密码错误")
    reset_failures(throttle_key)  # 登录成功清空该组合的失败记录
    _issue_session(response, user)
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    """修改密码：原密码校验通过后 token_version +1——所有已签发 JWT 的 ver 立刻
    对不上，等价全端下线；当前端清除 cookie 要求用新密码重登。"""
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(400, "原密码不正确")
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    db.commit()
    response.delete_cookie(ACCESS_COOKIE)
    return {"ok": True, "message": "密码已更新，请使用新密码重新登录"}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:  # noqa: B008
    return user
