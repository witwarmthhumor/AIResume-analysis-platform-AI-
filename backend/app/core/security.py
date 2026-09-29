"""认证安全工具：Argon2 密码哈希与 JWT 编解码。"""

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def create_access_token(user_id: int, token_version: int = 0) -> str:
    """签发 JWT：sub=用户 id，ver=token_version（改密 +1 使旧 token 全部失效）。"""
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_expire_minutes
    )
    return jwt.encode(
        {"sub": str(user_id), "ver": token_version, "exp": expires},
        settings.jwt_secret_key,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> tuple[int, int | None] | None:
    """解码返回 (user_id, ver)；无 ver 声明的旧 token 返回 ver=None（视为已失效）。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        return int(payload["sub"]), payload.get("ver")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None
