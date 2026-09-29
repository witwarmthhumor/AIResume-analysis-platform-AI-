"""简历文件解析服务：全项目唯一懂简历文件格式的地方（v4.2 起 PDF + Word docx）。

逐格式提取文字；PDF 全篇几乎提不出字 → 判定扫描件（图片型）。
觉得提取效果不好要换库，只改这个文件，路由和测试不用动。
"""

from dataclasses import dataclass
from io import BytesIO

from docx import Document as _DocxDocument
from pypdf import PdfReader

PDF_MAGIC = b"%PDF-"  # PDF 文件头 magic bytes，用于扩展名之外的二次校验
DOCX_MAGIC = b"PK\x03\x04"  # docx 本质是 zip 包，PK 头用于扩展名之外的二次校验
DOCX_MAX_PARAGRAPHS = 500  # docx 无"页"概念，段落数做等价的篇幅上限（防超长文档）

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


def parse_docx(data: bytes, max_pages: int) -> ParseResult:
    """解析 Word docx 字节流（v4.2）。docx 无分页，按段落折算页数（约 25 段/页）
    做上限校验；正文为空视为无效文档。"""
    try:
        document = _DocxDocument(BytesIO(data))
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    except Exception as exc:
        raise ParseError(
            "failed", "文件无法读取，可能已损坏，请重新导出 Word 后再试"
        ) from exc

    if not paragraphs:
        raise ParseError("unsupported", "Word 文档没有可提取的正文，请检查文件内容")

    page_count = max(1, -(-len(paragraphs) // 25))  # 向上取整：25 段折一页
    if page_count > max_pages:
        raise ParseError(
            "too_many_pages",
            f"简历约 {page_count} 页，超过 {max_pages} 页限制，请精简后上传",
        )

    return ParseResult(text="\n".join(paragraphs), page_count=page_count)


def parse_resume_file(data: bytes, filename: str, max_pages: int) -> ParseResult:
    """统一入口：按文件名扩展名分发到对应解析器。未知扩展名 → unsupported。"""
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        if not data.startswith(PDF_MAGIC):
            raise ParseError("failed", "文件内容与 PDF 格式不符，可能已损坏")
        return parse_pdf(data, max_pages)
    if lower.endswith(".docx"):
        if not data.startswith(DOCX_MAGIC):
            raise ParseError("failed", "文件内容与 Word 格式不符，可能已损坏")
        return parse_docx(data, max_pages)
    if lower.endswith(".doc"):
        raise ParseError("unsupported", "暂不支持旧版 .doc，请另存为 .docx 后上传")
    raise ParseError("unsupported", "仅支持 PDF 或 Word（.docx）格式的简历")
