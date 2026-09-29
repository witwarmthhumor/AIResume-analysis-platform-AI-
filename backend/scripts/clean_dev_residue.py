"""开发库残留与孤儿数据清理（一次性运维脚本）。

背景：历次测试运行 + 进程被强杀，导致开发库里累积了两类脏数据：
1. **孤儿行** —— 测试只删主表（users / resumes）没删关联表，留下指向不存在主体的行；
   实测：139 条 analyses/resumes 指向已不存在的 user、16 条 analyses 指向已不存在的
   resume、34 条 interview_sessions 指向已不存在的 user、6 条 interview_messages
   指向已不存在的 session。
2. **测试造的用户** —— `@example.com` 域，前缀为 chatview- / tasks- / agent- /
   test-interview-，全部由测试夹具创建且未被清理。

本脚本只删「可证明是测试产物或孤儿」的行：
- 孤儿行：按「左连接右表为空」判定，与具体标记无关，定义精确；
- 测试用户：仅 `email LIKE '%@example.com'`，以及其名下数据。

**保留**：真实账号（非 example.com 域）、匿名用户上传的简历与其分析、
预置知识库语料（7 文档 / 111 块）。

用法：
    python -m scripts.clean_dev_residue --dry-run   # 只报告，不删
    python -m scripts.clean_dev_residue             # 执行清理

注意：执行前务必先备份（本仓库约定备份到 `.backups/`，已 gitignore）：
    docker exec ai-interview-db pg_dump -U ai -d ai_interview > .backups/xxx.sql
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.resume import Resume

# 测试用户的判定：
# ① example.com 域（本项目所有真实账号均为外部邮箱域）；
# ② v4.2.1 起注册**不再收集邮箱**（email 可为 NULL）——`email LIKE` 对 NULL 求值为 NULL
#    （不成立），只按 ① 判会让 `noemail*` 这类测试账号永远清不掉，故必须再按 username 前缀兜一层。
TEST_USER_PREDICATE = "email LIKE '%@example.com' OR username LIKE 'noemail%'"

# 孤儿行判定：(表, where 条件)。条件一律用「左连接后右表为空」这种可证明的写法。
ORPHAN_PREDICATES: list[tuple[str, str]] = [
    ("analyses", "resume_id NOT IN (SELECT id FROM resumes)"),
    ("analyses", "user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)"),
    ("resumes", "user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)"),
    (
        "interview_sessions",
        (
            "resume_id NOT IN (SELECT id FROM resumes) OR "
            "(user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users))"
        ),
    ),
    ("interview_messages", "session_id NOT IN (SELECT id FROM interview_sessions)"),
    ("chat_messages", "session_id NOT IN (SELECT id FROM chat_sessions)"),
    ("chat_sessions", "user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)"),
    ("usage_logs", "user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)"),
    ("kb_chunks", "document_id NOT IN (SELECT id FROM kb_documents)"),
    # v4.2 新增的两张表此前漏判 —— 测试夹具不删子表时，行会原地变孤儿且无人发现
    ("question_banks", "user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)"),
    (
        "question_banks",
        "resume_id IS NOT NULL AND resume_id NOT IN (SELECT id FROM resumes)",
    ),
    ("audio_analyses", "user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)"),
]

# 受测试用户牵连的关联表：删用户前先把这些表里属于测试用户的行清掉。
# 判据统一写成子查询而不是 id 列表 —— psycopg3 下 `IN %(uid)s` 传元组会报语法错误，
# 且子查询天然跟随「删用户」这一步，不会因 ids 过期而漏删。
_TEST_USERS = f"(SELECT id FROM users WHERE {TEST_USER_PREDICATE})"
CASCADE_TABLES: list[tuple[str, str]] = [
    (
        "interview_messages",
        f"session_id IN (SELECT id FROM interview_sessions WHERE user_id IN {_TEST_USERS})",
    ),
    ("analyses", f"user_id IN {_TEST_USERS}"),
    ("interview_sessions", f"user_id IN {_TEST_USERS}"),
    (
        "chat_messages",
        f"session_id IN (SELECT id FROM chat_sessions WHERE user_id IN {_TEST_USERS})",
    ),
    ("chat_sessions", f"user_id IN {_TEST_USERS}"),
    ("usage_logs", f"user_id IN {_TEST_USERS}"),
    # v4.2 新增表：必须在删 users / resumes 之前清，否则行原地变孤儿
    ("question_banks", f"user_id IN {_TEST_USERS}"),
    ("audio_analyses", f"user_id IN {_TEST_USERS}"),
    ("resumes", f"user_id IN {_TEST_USERS}"),
]

COUNTED_TABLES = [
    "users",
    "resumes",
    "analyses",
    "interview_sessions",
    "interview_messages",
    "chat_sessions",
    "chat_messages",
    "usage_logs",
    "kb_documents",
    "kb_chunks",
]


def _snapshot(db: Session) -> dict[str, int]:
    """各表当前行数，用于清理前后对比。"""
    return {
        t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
        for t in COUNTED_TABLES
    }


def _orphan_counts(db: Session) -> dict[str, int]:
    """逐条孤儿判定命中的行数（键 = 判定表达式）。"""
    out: dict[str, int] = {}
    for table, where in ORPHAN_PREDICATES:
        out[f"{table}: {where}"] = db.execute(
            text(f"SELECT count(*) FROM {table} WHERE {where}")
        ).scalar_one()
    return out


def _orphan_uploads(db: Session) -> list[Path]:
    """盘上存在、但没有任何 resumes 行引用的上传文件。

    成因：测试夹具在 teardown 里删盘上文件，一旦进程被强杀或 teardown 报错，
    文件就留在 `uploads/` 里没有主。DB 清干净了但磁盘还在涨，属于同一类残留。
    """
    referenced = {Path(p).name for p in db.scalars(select(Resume.storage_path)) if p}
    upload_dir = Path(settings.upload_dir)
    if not upload_dir.is_dir():
        return []
    return sorted(
        f for f in upload_dir.iterdir() if f.is_file() and f.name not in referenced
    )


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    with SessionLocal() as db:
        before = _snapshot(db)
        test_user_ids = [
            r[0]
            for r in db.execute(
                text(f"SELECT id FROM users WHERE {TEST_USER_PREDICATE}")
            )
        ]

        print("=== 清理前 ===")
        for t, n in before.items():
            print(f"  {t:22s} {n}")

        print(f"\n=== 测试用户（{TEST_USER_PREDICATE}）===")
        print(f"  命中 {len(test_user_ids)} 个账号")

        print("\n=== 孤儿行判定 ===")
        orphans = _orphan_counts(db)
        for k, n in orphans.items():
            print(f"  {n:5d}  {k}")
        orphan_total = sum(orphans.values())

        print("\n=== 离线孤儿文件（uploads/）===")
        orphan_files = _orphan_uploads(db)
        print(f"  {len(orphan_files)} 个文件无 resumes 行引用")
        for f in orphan_files[:5]:
            print(f"    - {f.name}")
        if len(orphan_files) > 5:
            print(f"    … 其余 {len(orphan_files) - 5} 个")

        if dry_run:
            print(
                f"\n[dry-run] 将删除 {len(test_user_ids)} 个测试用户、{orphan_total} 条孤儿行、"
                f"{len(orphan_files)} 个离线孤儿文件，未执行。"
            )
            return 0

        print("\n=== 执行清理 ===")
        # 1) 孤儿行：按精确判定删除，与标记无关
        for table, where in ORPHAN_PREDICATES:
            res = db.execute(text(f"DELETE FROM {table} WHERE {where}"))
            if res.rowcount:
                print(f"  孤儿 {table:20s} -{res.rowcount}")
        # 2) 测试用户名下的关联行（此时可能已有孤儿被清掉，剩余的是仍属于测试用户的）
        if test_user_ids:
            for table, where in CASCADE_TABLES:
                res = db.execute(text(f"DELETE FROM {table} WHERE {where}"))
                if res.rowcount:
                    print(f"  测试 {table:20s} -{res.rowcount}")
            res = db.execute(text(f"DELETE FROM users WHERE {TEST_USER_PREDICATE}"))
            print(f"  测试 users               -{res.rowcount}")
        # 3) 离线孤儿文件：DB 里没有主的上传文件，一并清掉（否则磁盘只增不减）
        if orphan_files:
            removed = 0
            for f in orphan_files:
                try:
                    f.unlink()
                    removed += 1
                except OSError as exc:
                    print(f"  ⚠️ 删除失败 {f.name}: {exc}")
            print(f"  离线文件                -{removed}")
        db.commit()

        after = _snapshot(db)
        print("\n=== 清理后 ===")
        for t in COUNTED_TABLES:
            delta = after[t] - before[t]
            flag = f"  ({delta:+d})" if delta else ""
            print(f"  {t:22s} {after[t]}{flag}")

        print("\n=== 遗留孤儿复查（应全为 0）===")
        remaining = _orphan_counts(db)
        bad = {k: n for k, n in remaining.items() if n}
        if bad:
            for k, n in bad.items():
                print(f"  ⚠️ {n:5d}  {k}")
        else:
            print("  全部为 0 ✓")
        left_files = _orphan_uploads(db)
        print(
            f"  离线孤儿文件 {len(left_files)} 个" + ("✓" if not left_files else " ⚠️")
        )

        print("\n=== 保留项核对 ===")
        kept_users = db.execute(
            text(
                "SELECT id, email FROM users WHERE email NOT LIKE '%@example.com' ORDER BY id"
            )
        ).all()
        print(
            f"  真实账号 {len(kept_users)} 个：{', '.join(u[1] for u in kept_users) or '（无）'}"
        )
        kept_anon = db.execute(
            text("SELECT count(*) FROM resumes WHERE user_id IS NULL")
        ).scalar_one()
        print(f"  匿名简历（用户自己上传）保留 {kept_anon} 份")
        kb = db.execute(
            text("SELECT count(*) FROM kb_documents WHERE deleted_at IS NULL")
        ).scalar_one()
        chunks = db.execute(text("SELECT count(*) FROM kb_chunks")).scalar_one()
        print(f"  预置语料 {kb} 文档 / {chunks} 块")

        return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
