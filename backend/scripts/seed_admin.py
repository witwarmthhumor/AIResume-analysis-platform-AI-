"""内置管理员播种（v4.1 企业级改造·A1，方案 §3.3）。

为什么需要：「首个注册用户自动提权 admin」已改为开关控制且**默认关**——
关闭后管理员的唯一来源就是本脚本；不加内置账号，全新库上没有任何人能进管理端。

幂等语义（方案验收要求）：
- username='admin' 已存在 → 仅确保 role='admin'，**不覆盖口令**（管理员改密后
  重复执行本脚本不会把口令重置回默认值）；
- 不存在 → 创建（Argon2id 入库），role='admin'。

用法：
    cd backend
    .venv\\Scripts\\python -m scripts.seed_admin                 # 口令用默认 123456
    .venv\\Scripts\\python -m scripts.seed_admin --password Xx   # 指定初始口令（仅创建时生效）
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User

ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@airesume.internal"  # 内置账号专用邮箱域，不与演示/真实账号重叠
DEFAULT_PASSWORD = (
    "123456"  # 演示/引导口令；登录 schema 拆分后 6 位可登录（注册仍 ≥8 位）
)


def ensure_builtin_admin(
    db: Session, password: str = DEFAULT_PASSWORD
) -> tuple[User, bool, bool]:
    """幂等确保内置 admin 存在且 role='admin'。

    返回 (用户, 是否新建, 是否补修 role)。已存在时**不覆盖口令**——只修角色，
    这样管理员改过口令后重跑脚本不会退化安全水位。
    """
    user = db.scalar(select(User).where(User.username == ADMIN_USERNAME))
    if user is not None:
        fixed_role = user.role != "admin"
        if fixed_role:
            user.role = "admin"
            db.commit()
        return user, False, fixed_role
    user = User(
        email=ADMIN_EMAIL,
        username=ADMIN_USERNAME,
        password_hash=hash_password(password),
        role="admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True, False


def main() -> int:
    args = sys.argv[1:]
    password = DEFAULT_PASSWORD
    if "--password" in args:
        idx = args.index("--password")
        if idx + 1 >= len(args):
            print("✗ --password 需要跟一个口令值")
            return 2
        password = args[idx + 1]

    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        if existing is not None and existing.username != ADMIN_USERNAME:
            print(
                f"✗ 邮箱 {ADMIN_EMAIL} 已被用户名「{existing.username}」占用——"
                "内置 admin 需要该邮箱，请先处理该账号"
            )
            return 1
        user, created, fixed_role = ensure_builtin_admin(db, password)
    finally:
        db.close()

    if created:
        print(f"✓ 已创建内置管理员：{user.username} / {ADMIN_EMAIL}（口令已入库）")
    elif fixed_role:
        print(f"✓ 账号 {user.username} 已存在，role 补修为 admin（口令未改动）")
    else:
        print(f"✓ 内置管理员已在位：{user.username}（口令未改动）")
    print("  提醒：123456 仅演示用，正式环境请用 --password 指定强口令或登录后立即改密")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
