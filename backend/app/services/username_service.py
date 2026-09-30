"""用户名派生与校验（v4.1 认证改造 A1）。

一套规则服务两个场景：
1. **注册时服务端派生**（A1 过渡期：入参还只有 email，用户名先按前缀自动分配，
   A2 切换入参后改为用户自选 + 本模块校验）；
2. **存量账号回填**（Alembic 数据迁移按 email 前缀派生用户名）。

规则（评审裁定，方案附页 A·2）：
- 前缀小写化，剔除 `[^a-z0-9_]` 之外字符（现实邮箱普遍含 `.` `+` `-`）；
- 清洗后为空或命中保留字 → 加 `user_` 前缀（保留字防冒充内置 admin 等身份）；
- 长度钳 3~64：不足 3 用 `user_` 前缀补足，超 64 截断；
- 重名追加 `_2/_3…`（used 集合由调用方给定，迁移传"已分配集合"，注册传 DB 查询结果）。
"""

import re

# 保留字：防冒充内置管理员与系统身份；username 列有唯一约束，但"能注册"和"该注册"是两回事
RESERVED_USERNAMES = {"admin", "administrator", "root", "system"}

_USERNAME_RE = re.compile(r"^[a-z0-9_]{3,64}$")
_SANITIZE_RE = re.compile(r"[^a-z0-9_]")


def is_valid_username(name: str) -> bool:
    """注册入参校验口径：3~64 位小写字母/数字/下划线，且不在保留字内。"""
    return bool(_USERNAME_RE.match(name)) and name not in RESERVED_USERNAMES


def _sanitize_base(email: str) -> str:
    """email 前缀 → 清洗后的候选基名（可能为空串）。"""
    base = email.split("@", 1)[0].strip().lower()
    base = _SANITIZE_RE.sub("", base)
    if len(base) > 64:
        base = base[:64]
    if not base or base in RESERVED_USERNAMES or len(base) < 3:
        # user_ 前缀保证 ≥3 位且避开保留字；user_xxx 本身不在保留字内
        base = f"user_{base}" if base else "user"
    return base


def derive_username(email: str, used: set[str]) -> str:
    """按 email 派生用户名并避开 used 集合（重名追加 _2/_3…）。

    注意：返回值**会加入 used**——同一批回填循环里连续调用天然去重。
    """
    base = _sanitize_base(email)
    candidate, n = base, 1
    while candidate in used:
        n += 1
        candidate = f"{base}_{n}"
    used.add(candidate)
    return candidate


def derive_username_db(db, email: str) -> str:
    """注册路由用的 DB 版派生（S-4）：按候选逐个探测存在性，
    避免把全表 username 拉进内存；派生候选数通常 ≤2，探测次数有界。"""
    from sqlalchemy import select

    from app.models.user import User

    base = _sanitize_base(email)
    candidate, n = base, 1
    while db.scalar(select(User).where(User.username == candidate)) is not None:
        n += 1
        candidate = f"{base}_{n}"
    return candidate
