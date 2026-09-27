# -*- coding: utf-8 -*-
"""生成按 Action List 改造后的完整简历 Word 文档（总稿的同构 docx 版）。"""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn

TASK_DIR = r"E:\AIDevelop\AIProject\AIResume\tasks\resume-rework-20260927"
OUT = TASK_DIR + r"\简历-AI应用开发工程师-总稿.docx"

doc = Document()

# ---------- 页面：A4，上下左右 2.2cm ----------
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.top_margin = sec.bottom_margin = Cm(2.0)
sec.left_margin = sec.right_margin = Cm(2.2)

normal = doc.styles["Normal"]
normal.font.name = "Arial"
normal.font.size = Pt(11)
normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
npf = normal.paragraph_format
npf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
npf.space_before = Pt(0)
npf.space_after = Pt(0)


def set_run_font(run, cn="宋体", en="Arial", size=11, bold=False, color=None):
    run.font.name = en
    run.font.size = Pt(size)
    run.font.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = rPr.makeelement(qn("w:rFonts"), {})
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), cn)
    if color is not None:
        run.font.color.rgb = RGBColor(*color)


def section_title(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    set_run_font(r, cn="黑体", size=14, bold=True)


def para(runs, space_after=2, indent=0):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    if indent:
        ind = p._p.get_or_add_pPr().makeelement(qn("w:ind"), {})
        ind.set(qn("w:firstLineChars"), str(indent * 100))
        p._p.get_or_add_pPr().append(ind)
    for text, fmt in runs:
        run = p.add_run(text)
        set_run_font(run, **fmt)
    return p


def bullet(text, bold_prefix=None):
    p = doc.add_paragraph(style="List Bullet")
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    pf.space_after = Pt(3)
    pPr = p._p.get_or_add_pPr()
    for ind in pPr.findall(qn("w:ind")):
        pPr.remove(ind)
    if bold_prefix:
        r = p.add_run(bold_prefix)
        set_run_font(r, cn="宋体", size=11, bold=True)
    r = p.add_run(text)
    set_run_font(r, cn="宋体", size=11)
    return p


def project_head(name):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(name)
    set_run_font(r, cn="黑体", size=12, bold=True)


def tech_line(text):
    para([("技术栈：", dict(cn="宋体", size=11, bold=True)),
          (text, dict(cn="宋体", size=11))], space_after=2)


# ===================== 求职意向 =====================
section_title("求职意向")
para([("AI 应用开发工程师", dict(cn="宋体", size=12, bold=True)),
      ("｜意向城市：【城市】｜工作经验：3.5 年", dict(cn="宋体", size=11))])

# ===================== 专业技能 =====================
section_title("专业技能")
bullet("Python 3.13；LangChain 0.3（ReAct Agent、多工具路由）；LangGraph 0.2（StateGraph 多 Agent 编排、interrupt 人机协同、PostgresSaver 断点续跑）——由 AIResume 项目全程落地佐证", "AI 应用开发：")
bullet("pgvector + HNSW 向量检索、jieba/BM25 词法检索、RRF 融合、检索效果评测（hit@k / MRR / RAGAS），两个项目分别以 49 题、285 题标注集驱动迭代", "RAG 检索：")
bullet("FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Redis、Celery、SSE 流式协议", "后端工程：")
bullet("Vue 3、Vite（零 UI 框架手写组件）", "前端：")
bullet("pytest（287 用例全绿、覆盖率 89%）、GitHub Actions CI、ruff、Docker Compose；77 项安全审计修复（含 2 项横向越权 P0）", "工程化：")

# ===================== 工作经历 =====================
section_title("工作经历")
para([("【公司一】｜AI 应用开发工程师｜【2024.X – 至今】（金卡 APP）", dict(cn="宋体", size=11, bold=True))], space_after=1)
bullet("负责【金卡 APP 的 AI 功能，如智能客服/内容生成/推荐】的设计与落地：用 Python + LangChain【落地方式一句话】，【量化结果】。")
bullet("【RAG 知识库 / 提示词工程与评测 / 成本治理，任选真实做过的一项 + 数字】。")
para([("【公司二】｜AI 应用开发工程师｜【2023.X – 2024.X】（凌云 TMS）", dict(cn="宋体", size=11, bold=True))], space_after=1)
bullet("负责【凌云 TMS 的 AI 模块，如运单智能录入/路线问答/单据解析】：用 Python + LangChain【落地方式一句话】，【量化结果】。")
bullet("【第二条：同上结构，选真实做过的一项】。")
para([("【公司三】｜【传统岗位】｜【起止】——【一句话职责】", dict(cn="宋体", size=11))], space_after=1)
para([("【公司四】｜【传统岗位】｜【起止】——【一句话职责】", dict(cn="宋体", size=11))])

# ===================== 项目经历 =====================
section_title("项目经历")

project_head("金卡 APP——【AI 子系统名】（工作项目）【待补】")
tech_line("Python、【LangChain / LangGraph】、【向量库】、【其余】")
bullet("【分点 1：Python + LangChain 关键词必须出现，写清做了什么 + 数字】")
bullet("【分点 2-6：检索 / 提示词 / 评测 / 稳定性 / 成本，每条一个技术点 + 量化】")

project_head("凌云 TMS——【AI 模块名】（工作项目）【待补】")
tech_line("Python、【LangChain】、【其余】")
bullet("【分点 1：Python + LangChain 关键词落实 + 数字】")
bullet("【分点 2-6：同上】")

project_head("AIResume——AI 简历分析与模拟面试平台（个人全栈项目）")
tech_line("Python、FastAPI、PostgreSQL 16（pgvector）、Redis、SQLAlchemy 2、Celery、Vue 3、LangChain 0.3、LangGraph 0.2、Ollama")
para([("项目简介：", dict(cn="宋体", size=11, bold=True)),
      ("集成简历上传解析、AI 分析报告、多轮模拟面试与知识库问答的全链路 AI 应用平台。针对 AI 输出不可控、长任务阻塞与个人数据越权三大痛点，以「统一 AI 封装层 + RAG 混合检索 + 多 Agent 编排 + 人机协同审批」为架构主线，全程评测驱动迭代。",
       dict(cn="宋体", size=11))], space_after=3)
bullet("基于 LangGraph 构建「一键求职准备」多 Agent 工作流（规划 → 简历分析 → 岗位匹配 → 定制出题 → 交付，含坏输出回环重试），高风险动作经 interrupt 人机协同审批后才执行，PostgresSaver 支持断点续跑；11 个端到端黄金任务评测基线（正常/无简历/坏输出/审批通过/拒绝/超时/检索降级/限额/越权）全部通过。")
bullet("基于 LangChain 0.3 构建 ReAct Agent，落地 11 个业务工具（知识检索、简历查询、面试历史、岗位匹配、出题、答案点评等），统一归属过滤防止个人数据越权，工具路由评测 33/33 达 100% 准确率。")
bullet("搭建 pgvector 混合检索（向量 + jieba/BM25 词法 + RRF 融合），49 题黄金问答集 top-1 命中率 71.4% → 87.8%（top-5 达 100%）且无一题回退。")
bullet("封装 OpenAI 兼容协议层：JSON 结构化校验 + 失败自动重试，提示词带版本号留档，切换模型仅改 3 行配置；Celery + Redis 长任务异步化（幂等重试），SSE 流式输出逐字结果与工具调用过程（单次问答 500+ 分片，中断可恢复）。")
bullet("工程质量：77 项安全审计修复（含 2 项横向越权 P0）、287 个 pytest 用例全绿、服务层覆盖率 89%、CI 全绿；数据库快速失败机制实测停库 5.3s 返回 503（修复前挂起 60s+）。")

project_head("二手智析——基于 RAG 的二手交易平台智能分析系统（个人项目）")
tech_line("Python、FastAPI、bge-m3、BM25 + RRF、RAGAS、Chroma、PostgreSQL、Redis、Vue3、Docker")
bullet("bge-m3 向量 + BM25 + RRF(k=60) 混合检索，向量相似度二次精排 + 文档级保底重排，285 问评测集 Hit@5 由 0.9825 → 1.0000、MRR@10 由 0.9186 → 0.9506、nDCG@10 → 0.8280。")
bullet("三档防幻觉分流（≥0.65 直出 / 0.4~0.65 片段注入 / <0.4 拒答不调 LLM），答案强制来源标注校验，无谓拒答率 20% → 0。")
bullet("L1 进程内 LRU + L2 Redis 两级检索缓存（热点命中 <0.05ms）+ SingleFlight 防击穿，Redis 故障 30s 熔断静默降级；Token 级成本预检/记账/累计熔断，ASGI 滑动窗口三档限流。")

project_head("【传统项目一】（压缩 2 行）")
bullet("【项目名 + 技术栈 + 一句话定位】")
bullet("【核心职责 + 唯一一个量化结果】")
project_head("【传统项目二】（压缩 2 行）")
bullet("【项目名 + 技术栈 + 一句话定位】")
bullet("【核心职责 + 唯一一个量化结果】")

doc.save(OUT)
print("saved:", OUT)
