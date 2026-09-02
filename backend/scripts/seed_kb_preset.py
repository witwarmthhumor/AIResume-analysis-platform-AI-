"""预置语料入库脚本：把 data/preset_kb/*.txt 切块向量化写入知识库。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe -m scripts.seed_kb_preset
    ./.venv/Scripts/python.exe -m scripts.seed_kb_preset --reset   # 先清空旧预置语料再入

幂等：同一 title + source_type=preset 的记录已 ready 则跳过（重复执行不重复入库）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.kb import KBDocument
from app.services.kb_service import create_document, ingest_kb_document

# 预置语料统一放项目根 data/preset_kb（与前端构建产物同层，仓库级数据）
PRESET_DIR = Path(__file__).resolve().parents[1].parent / "data" / "preset_kb"


def run(reset: bool = False) -> None:
    files = sorted(PRESET_DIR.glob("*.txt"))
    if not files:
        print(f"未找到预置语料文件：{PRESET_DIR}")
        sys.exit(1)

    with SessionLocal() as db:
        if reset:
            deleted = db.execute(
                delete(KBDocument).where(KBDocument.source_type == "preset")
            )
            print(f"已清空旧预置语料 {deleted.rowcount} 条（含其 chunks，级联删除）")

        done = skipped = failed = 0
        for f in files:
            title = f.stem
            exists = db.scalar(
                select(KBDocument).where(
                    KBDocument.source_type == "preset",
                    KBDocument.title == title,
                    KBDocument.status == "ready",
                    KBDocument.deleted_at.is_(None),
                )
            )
            if exists is not None:
                print(f"  [跳过] {title}（已 ready）")
                skipped += 1
                continue

            doc = create_document(
                db,
                title=title,
                doc_type="text",
                raw_text=f.read_text(encoding="utf-8"),
                user_id=None,
                anonymous_id=None,
                source_type="preset",
            )
            doc.status = "processing"
            db.commit()
            ok, message = ingest_kb_document(db, doc)
            if ok:
                done += 1
                print(f"  [入库] {title} → ready")
            else:
                failed += 1
                print(f"  [失败] {title} → {message}")

        print(
            f"\n完成：新入库 {done} 条，跳过 {skipped} 条，失败 {failed} 条。"
            f"（embedding: {settings.embedding_model}）"
        )
        if failed:
            sys.exit(2)


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)
