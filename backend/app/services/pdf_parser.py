"""PDF 解析服务：全项目唯一懂 PDF 的地方。

阶段1 只支持文本型 PDF（PROJECT-PLAN §1 风险1 对策）：逐页提取文字；
全篇几乎提不出字 → 判定扫描件（图片型）。觉得双栏提取效果不好要换库，
只改这个文件，路由和测试不用动。
"""

from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader

PDF_MAGIC = b"%PDF-"  # PDF 文件头 magic bytes，用于扩展名之外的二次校验

# 平均每页有效字符低于该值视为"无文字"；全篇都无文字 → 大概率扫描件
_MIN_CHARS_PER_PAGE = 10


class ParseError(Exception):
    """解析失败。kind 决定落库状态与用户话术，不让 4xx 与库状态混着用。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # too_many_pages / unsupported / failed
        self.message = message  # 面向用户的话术


@dataclass
class ParseResult:
    text: str
    page_count: int


def parse_pdf(data: bytes, max_pages: int) -> ParseResult:
    """解析 PDF 字节流。页数超限/扫描件/损坏时抛 ParseError，由路由层决定处置。"""
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:  # pypdf 异常种类繁多，统一按损坏文件话术处理
        raise ParseError(
            "failed", "文件无法读取，可能已损坏，请重新导出 PDF 后再试"
        ) from exc

    page_count = len(reader.pages)
    if page_count > max_pages:
        raise ParseError(
            "too_many_pages",
            f"简历共 {page_count} 页，超过 {max_pages} 页限制，请精简后上传",
        )

    texts: list[str] = []
    for page in reader.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001  单页提取失败按空页处理，交给下面的扫描件判定兜底
            texts.append("")

    text = "\n".join(texts).strip()
    effective_chars = len(text.replace(" ", "").replace("\n", ""))
    if effective_chars < _MIN_CHARS_PER_PAGE * max(page_count, 1):
        raise ParseError("unsupported", "暂不支持扫描件（图片型）PDF，请上传文本型 PDF")

    return ParseResult(text=text, page_count=page_count)
