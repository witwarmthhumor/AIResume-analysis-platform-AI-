"""简历接口的响应结构：pydantic 模型 = 出参合同，字段名即前端拿到的 JSON 键名。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ResumeOut(BaseModel):
    """列表项：不含正文（列表页用不着 raw_text，省流量）。"""

    model_config = ConfigDict(from_attributes=True)  # 允许直接从 ORM 对象转换

    id: int
    filename: str
    parse_status: str  # success / failed / unsupported / pending
    parse_error: str | None
    page_count: int | None
    file_size: int | None
    created_at: datetime


class ResumeDetail(ResumeOut):
    """详情：加上解析出的纯文本与文件指纹。"""

    raw_text: str | None
    file_hash: str


class UploadResult(BaseModel):
    """上传响应。duplicate=true 表示 hash 命中历史记录，直接复用，未重复解析。"""

    duplicate: bool
    resume: ResumeDetail
