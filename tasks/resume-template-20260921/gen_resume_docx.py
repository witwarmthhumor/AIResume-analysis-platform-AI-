# -*- coding: utf-8 -*-
"""生成 AIResume 项目简历板块 Word 文档（无模板创建）。"""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn

TASK_DIR = r"E:\AIDevelop\AIProject\AIResume\tasks\resume-template-20260921"
OUT = TASK_DIR + r"\AIResume项目经历-简历模板.docx"

doc = Document()

# ---------- 页面设置：A4，上下左右 2.5cm ----------
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Cm(2.5)

# ---------- Normal 样式：宋体(中文) / Arial(西文)，12pt，1.5 倍行距 ----------
normal = doc.styles["Normal"]
normal.font.name = "Arial"
normal.font.size = Pt(12)
normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
npf = normal.paragraph_format
npf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
npf.space_before = Pt(0)
npf.space_after = Pt(0)


def set_run_font(run, cn="宋体", en="Arial", size=12, bold=False, color=None):
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


def add_para(text_runs, align=WD_ALIGN_PARAGRAPH.JUSTIFY, indent_chars=2,
             space_before=0, space_after=0, line15=True):
    """text_runs: list of (text, dict(font args))"""
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = align
    if line15:
        pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if indent_chars:
        # 用 Word 字符单位实现首行缩进 2 字符（不受字号影响）
        ind = p._p.get_or_add_pPr().makeelement(qn("w:ind"), {})
        ind.set(qn("w:firstLineChars"), str(indent_chars * 100))
        p._p.get_or_add_pPr().append(ind)
    for text, fmt in text_runs:
        run = p.add_run(text)
        set_run_font(run, **fmt)
    return p


# ---------- 1. 板块标题：项目经历 ----------
p = doc.add_paragraph()
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
p.paragraph_format.space_after = Pt(10)
p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
r = p.add_run("项目经历")
set_run_font(r, cn="黑体", size=16, bold=True, color=(0, 0, 0))

# ---------- 2. 项目名 ----------
p = doc.add_paragraph()
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
p.paragraph_format.space_before = Pt(6)
p.paragraph_format.space_after = Pt(6)
p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
r = p.add_run("AIResume——AI 简历分析与模拟面试平台（个人全栈项目）")
set_run_font(r, cn="黑体", size=13, bold=True, color=(0, 0, 0))

# ---------- 3. 技术栈 ----------
add_para(
    [("技术栈：", dict(cn="宋体", size=12, bold=True)),
     ("FastAPI、PostgreSQL 16（pgvector）、Redis、SQLAlchemy 2、Celery、Vue 3、Vite、LangChain 0.3、Ollama（nomic-embed-text）",
      dict(cn="宋体", size=12))],
    indent_chars=0, space_after=6)

# ---------- 4. 项目简介 ----------
add_para(
    [("项目简介：", dict(cn="宋体", size=12, bold=True)),
     ("一个集成用户注册登录、PDF 简历上传解析、AI 智能分析报告与多轮文字模拟面试的全链路 AI 应用平台。针对 AI 输出不可控、简历解析易翻车与长耗时任务阻塞三大痛点，采用“统一 AI 封装层 + JSON 结构化校验 + 异步任务化 + RAG 混合检索”的整体架构，实现从简历上传到智能评估、模拟面试与知识库问答的一体化服务。",
      dict(cn="宋体", size=12))],
    indent_chars=0, space_after=6)

# ---------- 5. 分点 x7（Word 内置 List Bullet 自动项目符号） ----------
bullets = [
    "搭建基于 pgvector 的知识库问答系统，实现“向量检索 + jieba 分词 BM25 词法检索 + RRF 融合”的混合检索，在 49 题黄金问答集上 top-1 命中率由 71.4% 提升至 87.8%（top-5 达 100%），且无一题回退。",
    "基于 LangChain 0.3 构建 ReAct Agent，落地 11 个业务工具（知识检索、简历查询、面试历史、岗位匹配、题目生成、答案点评等），统一归属过滤防止个人数据越权，工具路由评测 33/33 达 100% 准确率。",
    "基于 SSE 流式协议实现 AI 逐字输出与 Agent 工具调用过程可视化，配套会话快照守卫与 abort 句柄，消息全量落库，实测单次问答输出 500+ 流式分片，中断后刷新页面即可恢复会话。",
    "引入 Celery + Redis 将简历解析、知识库入库等长耗时任务全链路异步化（幂等重试），前端实时展示任务进度；基于 Redis 令牌桶实现每日限额与限流，遏制恶意请求刷爆 AI API 调用成本。",
    "封装 OpenAI 兼容协议层，内置 JSON 输出结构化校验与失败自动重试（最多 2 次），提示词带版本号留档，切换模型仅需修改 3 行配置，保证 AI 输出结构化可控、分析报告可对比追溯。",
    "完成 77 项安全审计修复（含 2 项 P0 横向越权漏洞），统一匿名/登录双条件归属校验杜绝越权访问；264 个 pytest 用例全绿、服务层覆盖率 89%，CI 流水线（代码检查 → 数据库迁移 → 测试 → 前端构建）全绿。",
    "设计数据库快速失败机制，连接异常统一转为 503 友好提示且不泄露内部细节，实测停库 5.3 秒返回（修复前挂起 60 秒以上），故障恢复后 0.36 秒自动回归正常。",
]

for text in bullets:
    p = doc.add_paragraph(style="List Bullet")
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    pf.space_before = Pt(0)
    pf.space_after = Pt(4)
    # 清除继承自 Normal 的首行缩进，保证项目符号悬挂
    pPr = p._p.get_or_add_pPr()
    for ind in pPr.findall(qn("w:ind")):
        pPr.remove(ind)
    run = p.add_run(text)
    set_run_font(run, cn="宋体", size=12)

doc.save(OUT)
print("saved:", OUT)
