"""langgraph checkpoint 表预建（langgraph schema）——防 setup() 的 CREATE INDEX CONCURRENTLY 与流式响应的 idle-in-transaction 死锁

Revision ID: 08c3d31e023c
Revises: b7e4a1c90d52
Create Date: 2026-09-25 23:57:43.170669

背景（2026-09-25 CI 实测踩坑）：PostgresSaver.setup() 每次发起 run 都会执行，
其中建索引语句是 CREATE INDEX CONCURRENTLY——它必须等所有在途事务结束。
而 POST /runs 的请求会话在 SSE 流式响应期间保持 idle in transaction
（FastAPI 依赖收尾要等响应结束），于是「建索引等事务结束 ← 事务等流结束 ←
流等图跑完 ← 图卡在建索引」四方互等，冷库（索引不存在才真正执行 DDL）上
图线程静默挂死，SSE 90s 防御性断流，首个图用例必挂。
本迁移把 4 张 checkpoint 表预建进 langgraph schema 并把 langgraph 自身的
10 个版本号全部标记为已应用，setup() 从此是纯读空转，不再执行任何 DDL。
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '08c3d31e023c'
down_revision: str | None = 'b7e4a1c90d52'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# langgraph 0.2.76 checkpoint-postgres 2.0.21 的 MIGRATIONS 共 10 条（下标 0..9），
# 全部标记为已应用后 setup() 计算出的 version=9 即为末位，逐条跳过。
LANGGRAPH_MIGRATION_COUNT = 10


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    # 表结构与 langgraph/checkpoint/postgres/base.py 的 MIGRATIONS 一致，
    # 索引去掉 CONCURRENTLY（迁移事务内不允许也不需要）
    op.execute("""
        CREATE TABLE IF NOT EXISTS langgraph.checkpoint_migrations (
            v INTEGER PRIMARY KEY
        )
    """)
    op.execute(f"""
        INSERT INTO langgraph.checkpoint_migrations (v)
        SELECT generate_series(0, {LANGGRAPH_MIGRATION_COUNT - 1})
        ON CONFLICT (v) DO NOTHING
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS langgraph.checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint JSONB NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS langgraph.checkpoint_blobs (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            version TEXT NOT NULL,
            type TEXT NOT NULL,
            blob BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS langgraph.checkpoint_writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT,
            blob BYTEA NOT NULL,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
    """)
    op.execute("ALTER TABLE langgraph.checkpoint_blobs ALTER COLUMN blob DROP NOT NULL")
    op.execute("ALTER TABLE langgraph.checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''")
    op.execute("CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON langgraph.checkpoints(thread_id)")
    op.execute("CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON langgraph.checkpoint_blobs(thread_id)")
    op.execute("CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON langgraph.checkpoint_writes(thread_id)")
    # 旧版本 _dsn() 的 search_path 回退把 checkpoint 表误建到了 public（langgraph schema
    # 不存在时），这些表不受本迁移管理且只含测试期数据，清掉防两套并存造成歧义
    op.execute("""
        DROP TABLE IF EXISTS public.checkpoints, public.checkpoint_blobs,
        public.checkpoint_writes, public.checkpoint_migrations
    """)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
