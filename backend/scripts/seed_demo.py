"""演示数据种子（v3.8·8.1）：一条命令让全站各页面都有内容可看。

为什么需要：本项目功能完整但开发库默认是「空态」——首页、我的历史、数据看板、
使用日志、在线对话、面试列表在没有数据时全是空列表，演示/截图/给新人看都很尴尬。
本脚本造一套**语义自洽**的演示数据（简历 → 分析 → 面试 → 对话 → 记账），
而不是随机塞行。

造什么：
- 两个账号（覆盖双端导航）：
  - `demo@airesume.local`（管理员，演示「管理端五项 + 右下角 AI 客服悬浮」）
  - `demo@user.local`（普通用户，演示「首页 / AI 客服 / 个人中心」）——业务数据挂在这个号名下
- 1 份简历（从 `test-resumes/` 取 PDF，走真实解析器拿正文，落真实 uploads 文件）
- 1 份已完成分析报告（结构与 AI 实际返回一致：7 个字段全给）
- 1 场已结束的模拟面试（四维评分 + 6 条对话消息）
- 1 个在线对话会话（4 条消息）
- 近 7 天的使用记账（让看板柱状图与使用日志列表都有数据）

幂等：重复执行不会重复造数；`--reset` 先清掉演示账号名下全部数据再重建。
演示数据用独立邮箱域 `@airesume.local` / `@user.local`，与真实账号、与测试残留
（`@example.com`）都区分得开。

用法：
    cd backend
    .venv\\Scripts\\python -m scripts.seed_demo            # 造数（已存在则跳过）
    .venv\\Scripts\\python -m scripts.seed_demo --reset    # 清掉演示数据重建
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.chat import SESSION_TYPE_CHAT, ChatMessage, ChatSession
from app.models.interview import InterviewMessage, InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.models.user import User
from app.services.pdf_parser import ParseError, parse_pdf
from app.services.prompts import PROMPT_VERSION

# 演示账号：两个邮箱域都不与真实账号(*) 或测试残留(@example.com) 重叠
ADMIN_EMAIL = "demo@airesume.local"
USER_EMAIL = "demo@user.local"
DEMO_PASSWORD = "demo-resume-2026"  # 满足后端 8 位下限；文档里会写明
DEMO_EMAILS = (ADMIN_EMAIL, USER_EMAIL)

UPLOAD_DIR = Path(settings.upload_dir)

# 演示用简历：社招 3~5 年版，正文最完整、适合演示分析与面试
DEMO_RESUME_FILE = "2-社招3-5年简历.pdf"
DEMO_RESUME_NAME = "演示简历-社招3-5年.pdf"

# 分析报告示例：字段与 prompts.SYSTEM_PROMPT 声明的 JSON 结构严格一致
DEMO_ANALYSIS = {
    "target_position": "后端开发工程师（Java / Python，3~5 年）",
    "position_match": (
        "候选人具备 4 年服务端开发经验，技术栈以 Java 与 Python 为主，"
        "有完整的高并发项目落地经历，与目标岗位的核心要求高度匹配。"
        "短板在于缺少大规模分布式系统的治理经验，云原生相关实践停留在使用层面。"
    ),
    "strengths": [
        "4 年服务端开发经验，主导过日均千万级请求的订单服务重构",
        "熟悉 JVM 调优与并发编程，有线上故障定位到根因的完整案例",
        "有从 0 到 1 搭建监控告警体系的经历，工程化意识较好",
        "具备跨团队协作与技术方案评审经验，能独立对接产品与测试",
    ],
    "weaknesses": [
        "缺少分布式事务与分库分表的实战经验，仅在项目中了解过方案",
        "云原生实践偏使用层，未参与过容器编排与集群治理",
        "简历中量化成果偏少，多数项目只写了职责未写结果",
    ],
    "keyword_gaps": [
        "分布式事务（Seata / TCC / 最终一致性）",
        "分库分表（ShardingSphere）",
        "Kubernetes 集群治理",
        "链路追踪（SkyWalking / OpenTelemetry）",
        "容量规划与压测（全链路压测）",
    ],
    "suggestions": [
        "把「负责订单服务」改写成「订单服务 P99 从 800ms 降到 120ms，支撑 QPS 1.2 万」这类带数字的结果",
        "补一段分布式事务或分库分表的实践（哪怕是内部项目），这是目标岗位的高频考点",
        "把 Kubernetes 从『了解』升级为『用过』：补一个自己部署并排障过的服务案例",
        "面试前准备 2 个能讲 15 分钟的深挖案例：一个性能优化、一个线上故障",
    ],
    "predicted_questions": [
        "你重构订单服务时，怎么定位到瓶颈是数据库连接池的？",
        "JVM 频繁 Full GC 你怎么排查？说说你实际处理过的那次",
        "如果订单量再涨 10 倍，你会怎么改造这套架构？",
        "你了解分布式事务吗？你们项目里一致性是怎么保证的？",
        "讲一个你和产品/测试意见不一致、最后推进下去的例子",
    ],
}

# 面试结束报告：字段与 interview_prompts 的结束评价结构一致
DEMO_INTERVIEW_REPORT = {
    "technical_depth": 7,
    "communication": 8,
    "project_authenticity": 9,
    "overall": 8,
    "summary": (
        "整体表现扎实，简历中的项目经历经得起追问，能给出具体的实现细节与取舍理由。"
        "技术深度中等偏上，对 JVM 与并发有真实经验，但在分布式与云原生方向明显偏薄。"
        "表达结构清晰，会主动分点并给结论，是本次面试的加分项。"
        "建议补齐分布式方向的知识，并把简历里的成果量化。"
    ),
    "highlights": [
        "能准确说出订单服务重构前后的 P99 与 QPS 变化，数据可信",
        "对 JVM 调优的排查路径描述完整，从现象到工具到根因",
        "回答结构清晰，先给结论再展开，听感好",
    ],
    "improvements": [
        "分布式事务与分库分表只能讲概念，缺实操，建议补一个内部项目练手",
        "面对架构扩展性问题时，回答偏保守，缺少分层递进的设计方案",
        "有两处回答绕回同一个项目，案例储备可以再丰富一些",
    ],
}

DEMO_INTERVIEW_DIALOG = [
    (
        "assistant",
        "你好，我是本次模拟面试的面试官。我们先从你最近的一个项目开始：请用两分钟介绍一下你主导过的订单服务重构，重点讲你解决的核心问题。",
    ),
    (
        "user",
        "好的。当时订单服务的 P99 在高峰期接近 800ms，主要瓶颈是每次创建订单都要查三次用户和商品库，而且加锁粒度太粗。我把这些查询改成批量预热加本地缓存，把分布式锁换成按订单维度的行锁，P99 降到 120ms 左右，能支撑 QPS 1.2 万。",
    ),
    (
        "assistant",
        "这个降幅不小。你怎么确认瓶颈确实在数据库连接池和锁，而不是下游服务？有没有做过排除？",
    ),
    (
        "user",
        "做了。我先用 Arthas 看了下线程栈，发现大量线程阻塞在获取连接上，连接池的活跃连接数一直打满。然后对比了下游服务的 RT，是稳定的，所以定位到是连接池配置偏小加上锁等待叠加。把连接池从 20 调到 80，同时把大事务拆小之后就好了。",
    ),
    (
        "assistant",
        "清楚。那如果订单量再涨 10 倍，你现在的架构还能扛住吗？你会怎么改造？",
    ),
    (
        "user",
        "10 倍的话单库肯定不行了。我会分三步：先把订单表按用户维度分库分表，写入走分片、查询走聚合；再引入消息队列削峰，把创建订单链路里的非核心步骤异步化；最后把缓存做成多级，热点商品放本地缓存。每一步都要配合容量规划和全链路压测来验证。",
    ),
]


def _demo_user_ids(db: Session) -> list[int]:
    """演示账号 id 列表（不存在则为空）。"""
    return list(db.scalars(select(User.id).where(User.email.in_(DEMO_EMAILS))))


def _wipe(db: Session) -> None:
    """按归属彻底清掉演示数据。

    顺序遵循「子表先于父表」：messages → sessions/analyses/chat → usage → resumes → users。
    漏掉任何一张都会留下指向已删主体的孤儿行。
    """
    ids = _demo_user_ids(db)
    if not ids:
        return
    db.execute(
        text(
            "DELETE FROM interview_messages WHERE session_id IN "
            "(SELECT id FROM interview_sessions WHERE user_id = ANY(:ids))"
        ),
        {"ids": ids},
    )
    db.execute(
        text("DELETE FROM interview_sessions WHERE user_id = ANY(:ids)"), {"ids": ids}
    )
    db.execute(
        text(
            "DELETE FROM chat_messages WHERE session_id IN "
            "(SELECT id FROM chat_sessions WHERE user_id = ANY(:ids))"
        ),
        {"ids": ids},
    )
    db.execute(
        text("DELETE FROM chat_sessions WHERE user_id = ANY(:ids)"), {"ids": ids}
    )
    # 演示简历的文件名是固定的，先取出文件路径以便随后删盘上文件
    paths = list(db.scalars(select(Resume.storage_path).where(Resume.user_id.in_(ids))))
    db.execute(text("DELETE FROM analyses WHERE user_id = ANY(:ids)"), {"ids": ids})
    db.execute(text("DELETE FROM resumes WHERE user_id = ANY(:ids)"), {"ids": ids})
    db.execute(text("DELETE FROM usage_logs WHERE user_id = ANY(:ids)"), {"ids": ids})
    db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": ids})
    for p in paths:
        f = Path(p)
        if f.is_file() and f.parent == UPLOAD_DIR:
            f.unlink()
    db.commit()


def _seed(db: Session) -> dict[str, object]:
    """创建演示账号与全套业务数据，返回可直接打印的摘要。"""
    now = datetime.now(timezone.utc)
    pwd = hash_password(DEMO_PASSWORD)

    admin = User(email=ADMIN_EMAIL, password_hash=pwd, is_active=True, role="admin")
    demo = User(email=USER_EMAIL, password_hash=pwd, is_active=True, role="user")
    db.add_all([admin, demo])
    db.flush()  # 拿到自增 id

    # —— 简历：真实 PDF → 真实解析 → 真实落盘，与用户手动上传的路径完全一致 ——
    pdf_candidates = [
        Path("../test-resumes") / DEMO_RESUME_FILE,
        Path("test-resumes") / DEMO_RESUME_FILE,
    ]
    pdf_path = next((p for p in pdf_candidates if p.is_file()), None)
    if pdf_path is None:
        raise FileNotFoundError(
            f"找不到演示简历源文件：{DEMO_RESUME_FILE}（应在 test-resumes/ 下）"
        )
    data = pdf_path.read_bytes()
    try:
        parsed = parse_pdf(data, settings.upload_max_pages)
    except ParseError as exc:
        raise RuntimeError(f"演示简历解析失败：{exc.message}") from exc

    file_hash = hashlib.sha256(data).hexdigest()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    storage = UPLOAD_DIR / f"{file_hash}.pdf"
    storage.write_bytes(data)

    resume = Resume(
        user_id=demo.id,
        filename=DEMO_RESUME_NAME,
        file_hash=file_hash,
        storage_path=str(storage),
        raw_text=parsed.text,
        page_count=parsed.page_count,
        file_size=len(data),
        parse_status="success",
    )
    db.add(resume)
    db.flush()

    # —— 分析报告（一次成功调用）——
    analysis = Analysis(
        resume_id=resume.id,
        user_id=demo.id,
        model_name=settings.ai_model,
        prompt_version=PROMPT_VERSION,
        result_json=DEMO_ANALYSIS,
        valid_json=True,
        tokens_prompt=3120,
        tokens_completion=860,
        duration_ms=8420,
    )
    db.add(analysis)

    # —— 一场已结束的模拟面试 + 对话原文 ——
    session = InterviewSession(
        resume_id=resume.id,
        user_id=demo.id,
        status="finished",
        stage="wrapup",
        turn_count=len([m for m in DEMO_INTERVIEW_DIALOG if m[0] == "user"]),
        position_type="senior",
        final_report_json=DEMO_INTERVIEW_REPORT,
    )
    db.add(session)
    db.flush()
    for role, content in DEMO_INTERVIEW_DIALOG:
        db.add(
            InterviewMessage(
                session_id=session.id,
                role=role,
                content=content,
                tokens=len(content) if role == "assistant" else None,
            )
        )

    # —— 在线对话（v3.1 多会话形态）——
    chat = ChatSession(
        user_id=demo.id, session_type=SESSION_TYPE_CHAT, title="RAG 混合检索怎么调的"
    )
    db.add(chat)
    db.flush()
    for role, content in [
        ("user", "我们这个项目的 RAG 检索是怎么做到 hit@1 87.8% 的？"),
        (
            "assistant",
            (
                "核心是向量 + 词法的双路召回再用 RRF 融合。向量路走 pgvector 的 HNSW 余弦索引，"
                "词法路用 jieba 分词后自己实现的 Okapi BM25；两路各取 50 个候选，"
                "按名次做 RRF 融合（k=5），最后截到 top-k 交给大模型生成。"
            ),
        ),
        ("user", "为什么不直接把两路分数加权求和？"),
        (
            "assistant",
            (
                "因为两路分数不在同一量纲上——余弦相似度在 [0,1] 且分布很挤，BM25 是无上界的绝对分。"
                "要加权就得先归一化，而归一化方式本身又是一个要调的超参。RRF 只比名次不比分数，"
                "天然绕开这道题，实测在 49 题评测集上也是 k=5 时最好。"
            ),
        ),
    ]:
        db.add(ChatMessage(session_id=chat.id, role=role, content=content))

    # —— 近 7 天使用记账：让看板柱状图与使用日志列表都有内容 ——
    # 数量与 token 特意做出起伏，柱状图才看得出趋势
    daily_plan = [
        (6, "parse", 1, 0),
        (5, "analysis", 1, 3980),
        (4, "interview_message", 4, 2680),
        (3, "agent", 2, 1520),
        (2, "parse", 2, 0),
        (1, "analysis", 1, 4120),
        (0, "agent", 3, 2240),
    ]
    for days_ago, action, times, tokens in daily_plan:
        for i in range(times):
            db.add(
                UsageLog(
                    user_id=demo.id,
                    action_type=action,
                    model_name=None if action == "parse" else settings.ai_model,
                    tokens_total=tokens // times if tokens else None,
                    ip_address="127.0.0.1",
                    created_at=now - timedelta(days=days_ago, minutes=i * 7),
                )
            )
    # 管理端账号也给两笔，保证两个账号在日志页都看得到东西
    db.add(
        UsageLog(
            user_id=admin.id,
            action_type="agent",
            model_name=settings.ai_model,
            tokens_total=640,
            ip_address="127.0.0.1",
            created_at=now - timedelta(hours=3),
        )
    )
    db.commit()

    return {
        "admin_id": admin.id,
        "user_id": demo.id,
        "resume_id": resume.id,
        "analysis_id": analysis.id,
        "session_id": session.id,
        "chat_id": chat.id,
        "messages": len(DEMO_INTERVIEW_DIALOG),
        "usage_rows": sum(t for _, _, t, _ in daily_plan) + 1,
    }


def main() -> int:
    reset = "--reset" in sys.argv
    with SessionLocal() as db:
        existing = _demo_user_ids(db)
        if existing and not reset:
            print("演示数据已存在，跳过（要重建请加 --reset）：")
            for u in db.scalars(select(User).where(User.id.in_(existing))).all():
                print(f"  - {u.email}（角色 {u.role}）")
            print(f"\n登录口令：{DEMO_PASSWORD}")
            return 0

        if existing:
            print("=== 清理既有演示数据 ===")
            _wipe(db)
            print(f"  已清掉 {len(existing)} 个演示账号名下的数据")

        print("=== 造演示数据 ===")
        info = _seed(db)
        print(f"  账号      {ADMIN_EMAIL}（管理员）/ {USER_EMAIL}（普通用户）")
        print(f"  简历      id={info['resume_id']}（{DEMO_RESUME_NAME}）")
        print(
            f"  分析报告  id={info['analysis_id']}（prompt_version={PROMPT_VERSION}）"
        )
        print(
            f"  面试场次  id={info['session_id']}（finished，{info['messages']} 条对话）"
        )
        print(f"  在线对话  id={info['chat_id']}")
        print(f"  记账行    {info['usage_rows']} 条（覆盖近 7 天）")

        print("\n=== 全站可见性核对 ===")
        for label, sql in [
            ("users", "SELECT count(*) FROM users"),
            ("resumes", "SELECT count(*) FROM resumes"),
            ("analyses", "SELECT count(*) FROM analyses"),
            ("interview_sessions", "SELECT count(*) FROM interview_sessions"),
            ("chat_sessions", "SELECT count(*) FROM chat_sessions"),
            ("usage_logs", "SELECT count(*) FROM usage_logs"),
        ]:
            print(f"  {label:20s} {db.execute(text(sql)).scalar_one()}")
        print("\n孤儿复查（应为 0）：")
        for label, sql in [
            (
                "analyses→resumes",
                "SELECT count(*) FROM analyses WHERE resume_id NOT IN (SELECT id FROM resumes)",
            ),
            (
                "resumes→users",
                "SELECT count(*) FROM resumes WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users)",
            ),
        ]:
            print(f"  {label:20s} {db.execute(text(sql)).scalar_one()}")

    print(f"\n完成。演示账号口令：{DEMO_PASSWORD}")
    print("打开 http://localhost:5173 用它登录即可看到各页面内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
