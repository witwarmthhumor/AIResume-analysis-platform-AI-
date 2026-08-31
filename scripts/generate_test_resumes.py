"""生成阶段2 验收用的 5 份固定测试简历（PROJECT-PLAN §6）。

用法：在项目根目录执行  python scripts/generate_test_resumes.py
输出：test-resumes/ 下 5 个 PDF。内容固定，作为回归基准——改提示词/解析逻辑后重跑对比。

中文字体坑：fpdf2 内置字体（Helvetica 等）不含 CJK 字形，中文必须注册系统字体。
顺序探测 simhei.ttf → msyh.ttc（Windows 系统字体，TTF 优先，TTC 用 collection_font_number 取首个）。
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "test-resumes"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",  # 黑体，TTF，fpdf2 兼容最好
    r"C:\Windows\Fonts\msyh.ttc",  # 微软雅黑，TTC 集合，用 collection_font_number=0
]

GREEN = (16, 185, 129)


def _register_cjk(pdf: FPDF) -> None:
    """注册第一个找到的中文字体；都找不到则报错（Windows 必有一款）。

    黑体没有独立的粗体/斜体文件，同一字体注册到全部样式（"" / "B" / "I" / "BI"），
    否则 set_font("cjk", "B", …) 会报 Undefined font: cjkB。
    """
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            for style in ("", "B", "I", "BI"):
                if path.endswith(".ttc"):
                    pdf.add_font("cjk", style, path, collection_font_number=0)
                else:
                    pdf.add_font("cjk", style, path)
            return
    raise SystemExit(
        f"未找到中文字体（{', '.join(FONT_CANDIDATES)}）。请确认文件存在，或补充 FONT_CANDIDATES。"
    )


def _heading(pdf: FPDF, title: str) -> None:
    """小节标题 + 绿色分隔线，风格与前端卡片一致。"""
    pdf.set_font("cjk", "B", 14)
    pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*GREEN)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(2)


def _reset_x(pdf: FPDF) -> None:
    """multi_cell 结束后 x 停在右边距，不自动回左——每个块开头先归位，否则下次宽度为 0。"""
    pdf.set_x(pdf.l_margin)


def _kv(pdf: FPDF, key: str, value: str) -> None:
    pdf.set_font("cjk", "B", 11)
    pdf.cell(0, 6, f"{key}：", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("cjk", "", 11)
    _reset_x(pdf)
    pdf.multi_cell(0, 6, value)


def _bullets(pdf: FPDF, items: list[str]) -> None:
    # 不用 •（U+2022）：simhei 是 GB2312 字符集，缺该字形，fpdf2 排版会报"不够横向空间"
    pdf.set_font("cjk", "", 11)
    for item in items:
        _reset_x(pdf)
        pdf.multi_cell(0, 6, f"- {item}")


def _name_block(pdf: FPDF, name: str, subtitle: str) -> None:
    pdf.set_font("cjk", "B", 18)
    pdf.cell(0, 10, name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("cjk", "", 11)
    pdf.multi_cell(0, 6, subtitle)
    pdf.ln(2)


# ---------- 5 份简历内容 ----------


def build_fresh_graduate() -> None:
    """1 应届生：实习 + 校园项目为主，考验 AI 挖项目潜力。"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    _register_cjk(pdf)
    _name_block(pdf, "张伟", "求职意向：Python 后端开发工程师（应届） · 电话 138-0000-0001")
    _heading(pdf, "教育背景")
    _kv(pdf, "北京理工大学 · 软件工程（本科）", "2022.09 - 2026.06 · 主修课程：数据结构、操作系统、数据库原理")
    _heading(pdf, "实习经历")
    _kv(pdf, "某某科技有限公司 · Python 开发实习生", "2025.06 - 2025.09")
    _bullets(pdf, [
        "参与公司内部工单系统的接口开发，用 FastAPI 实现工单流转与提醒功能",
        "为报表模块编写 SQL 查询并优化索引，单页加载从 3s 降到 0.8s",
    ])
    _heading(pdf, "项目经历")
    _kv(pdf, "校园二手交易平台（毕业设计）", "2025.10 - 2026.03")
    _bullets(pdf, [
        "独立完成前后端开发：FastAPI + Vue 3 + PostgreSQL，支持商品发布、搜索、私信",
        "实现基于 SHA-256 的图片去重与文件存储方案，避免重复上传",
    ])
    _heading(pdf, "技能与荣誉")
    _bullets(pdf, ["Python / SQL / Git / Linux 基础", "校级一等奖学金（2024）", "英语 CET-6"])
    return pdf


def build_experienced() -> None:
    """2 社招 3-5 年：技术深度，考验分析质量。"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    _register_cjk(pdf)
    _name_block(pdf, "李强", "求职意向：高级后端开发工程师 · 5 年 Python 后端经验 · 电话 139-0000-0002")
    _heading(pdf, "工作经历")
    _kv(pdf, "某电商公司 · 后端开发工程师", "2023.04 - 至今")
    _bullets(pdf, [
        "负责订单系统核心链路，日订单量峰值 80 万，将下单接口 P99 延迟从 900ms 优化到 220ms",
        "用 Celery + Redis 重构异步任务，订单超时关单任务失败率下降 95%",
        "主导 PostgreSQL 分库分表方案设计，单表超千万行后读写性能保持稳定",
    ])
    _kv(pdf, "某金融科技公司 · Python 开发工程师", "2020.07 - 2023.03")
    _bullets(pdf, [
        "开发风控规则引擎，日处理请求 3000 万，支持规则热更新",
        "搭建 Prometheus + Grafana 监控体系，服务可用性从 99.5% 提升到 99.95%",
    ])
    _heading(pdf, "技术栈")
    _bullets(pdf, [
        "Python / FastAPI / Django / Celery",
        "PostgreSQL / Redis / Kafka",
        "Docker / Kubernetes / AWS",
    ])
    _heading(pdf, "项目亮点")
    _kv(pdf, "支付网关", "2022.01 - 2022.08")
    _bullets(pdf, ["对接 12 家支付渠道，统一回调协议，资金对账差错率降至十万分之一"])
    return pdf


def build_career_changer() -> None:
    """3 转行/经历混杂：销售 → 数据分析，考验解析与归纳。"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    _register_cjk(pdf)
    _name_block(pdf, "王芳", "求职意向：数据分析师 · 4 年销售管理 + 转行培训 · 电话 137-0000-0003")
    _heading(pdf, "工作经历")
    _kv(pdf, "某快消公司 · 区域销售主管", "2019.07 - 2023.06")
    _bullets(pdf, [
        "负责华东区域 20 家门店销售管理，年度销售额从 800 万做到 1400 万",
        "用 Excel 搭建门店经营看板，辅助选品与促销决策，库存周转提升 20%",
    ])
    _heading(pdf, "转行学习")
    _kv(pdf, "数据分析师培训班", "2023.09 - 2024.04")
    _bullets(pdf, ["系统学习 SQL、Python（pandas）、数据可视化（Tableau）", "完成 3 个实战项目：用户流失分析、销售预测、A/B 测试评估"])
    _heading(pdf, "项目经历")
    _kv(pdf, "用户流失预警分析", "2024.02 - 2024.04")
    _bullets(pdf, [
        "基于 pandas 清洗 50 万条用户行为数据，用逻辑回归构建流失预警模型",
        "输出分析报告，提出会员召回策略，试点后 30 日复购率提升 8%",
    ])
    _heading(pdf, "技能")
    _bullets(pdf, ["SQL / Python(pandas) / Excel / Tableau", "业务理解：销售管理、门店运营", "沟通与汇报：多次向管理层做数据汇报"])
    return pdf


def build_english() -> None:
    """4 英文简历：全英文（内置 Helvetica），验证中英文兼容。"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "John Smith", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, "Senior Software Engineer · Seattle, WA · john.smith@example.com")

    def h(title: str) -> None:
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*GREEN)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)

    def kv(key: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, key, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        _reset_x(pdf)
        pdf.multi_cell(0, 6, value)

    def bullets(items: list[str]) -> None:
        # Helvetica 内置字体是 Latin-1，•（U+2022）超出范围；用 - 兼容
        pdf.set_font("Helvetica", "", 11)
        for item in items:
            _reset_x(pdf)
            pdf.multi_cell(0, 6, f"- {item}")

    h("Summary")
    pdf.multi_cell(0, 6, "Backend engineer with 4 years building distributed systems at scale. Passionate about clean architecture and reliability.")
    h("Experience")
    kv("Senior Software Engineer, CloudRise Inc.", "2022.02 - Present")
    bullets([
        "Led migration of 12 microservices from Go to Python (FastAPI), cutting deployment time by 60%",
        "Designed event pipeline processing 1M messages/day on Kafka and Redis",
    ])
    kv("Software Engineer, DataHarbor LLC", "2020.06 - 2022.01")
    bullets([
        "Built ETL jobs in Python reducing batch window from 6h to 1.5h",
        "Owned PostgreSQL sharding strategy for multi-tenant data",
    ])
    h("Education")
    kv("B.S. Computer Science, University of Washington", "2016 - 2020")
    h("Skills")
    bullets(["Python / Go / FastAPI", "PostgreSQL / Redis / Kafka", "AWS / Docker / Terraform"])
    return pdf


def build_two_column() -> None:
    """5 双栏排版：左栏个人信息/技能，右栏经历/项目，考验解析鲁棒性。"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    _register_cjk(pdf)
    pdf.set_font("cjk", "B", 18)
    pdf.cell(0, 10, "赵敏", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("cjk", "", 11)
    pdf.multi_cell(0, 6, "全栈工程师 · 3 年经验 · 136-0000-0004 · zhaomin@example.com")
    pdf.ln(3)

    left_x, left_w = 15, 82
    right_x, right_w = 105, 90
    top_y = pdf.get_y()

    def col_title(x: float, title: str) -> None:
        pdf.set_xy(x, pdf.get_y())
        pdf.set_font("cjk", "B", 13)
        pdf.multi_cell(left_w if x < right_x else right_w, 6, title)
        pdf.set_draw_color(*GREEN)
        pdf.set_xy(x, pdf.get_y())
        pdf.line(x, pdf.get_y(), x + (left_w if x < right_x else right_w), pdf.get_y())
        pdf.ln(1)

    def col_text(x: float, text: str) -> None:
        pdf.set_xy(x, pdf.get_y())
        pdf.set_font("cjk", "", 10)
        pdf.multi_cell(left_w if x < right_x else right_w, 5, text)
        pdf.ln(1)

    # 左栏
    pdf.set_y(top_y)
    col_title(left_x, "个人信息")
    col_text(left_x, "邮箱：zhaomin@example.com\n电话：136-0000-0004\n现居：杭州")
    col_title(left_x, "教育背景")
    col_text(left_x, "浙江大学 · 计算机科学（本科）\n2019 - 2023")
    col_title(left_x, "专业技能")
    col_text(left_x, "Python · Vue · SQL\nDocker · Git · 敏捷开发")

    # 右栏
    pdf.set_xy(right_x, top_y)
    col_title(right_x, "工作经历")
    col_text(right_x, "某某互联网公司 · 全栈工程师\n2021.07 - 至今\n负责在线教育平台前后端，DAU 20 万；\n用 FastAPI 重构后端接口，响应时间下降 45%；\n前端 Vue 组件库沉淀 30+ 公共组件。")
    col_title(right_x, "项目经历")
    col_text(right_x, "实时协作白板（个人项目）\n基于 WebSocket 实现多人实时协作，\n支持图形与文字同步，支撑 500 人并发。")

    # 双栏在右侧内容更高，右侧写入后可能超出左栏底；底部补充一行底部说明
    pdf.set_font("cjk", "", 9)
    pdf.set_text_color(130, 130, 130)
    pdf.multi_cell(0, 5, "（本简历采用双栏排版，用于验证 PDF 文本提取鲁棒性）")
    pdf.set_text_color(0, 0, 0)
    return pdf


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    builders = [
        ("1-应届生简历.pdf", build_fresh_graduate),
        ("2-社招3-5年简历.pdf", build_experienced),
        ("3-转行经历混杂.pdf", build_career_changer),
        ("4-english-resume.pdf", build_english),
        ("5-双栏排版.pdf", build_two_column),
    ]
    for fname, fn in builders:
        pdf = fn()
        pdf.output(str(OUT_DIR / fname))
    print(f"已生成 {len(builders)} 份测试简历到 {OUT_DIR}")


if __name__ == "__main__":
    main()
