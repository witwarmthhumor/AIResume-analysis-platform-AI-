"""配置中心：全项目唯一的配置出口。

.py 里写默认值，.env 里的同名变量覆盖它（pydantic-settings 负责读取和类型转换）。
注意：env_file 是相对"启动命令所在目录"的——统一从 backend/ 目录启动后端和 alembic。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://ai:ai@localhost:5432/ai_interview"

    # 阶段1：简历上传限制与存储（PROJECT-PLAN §1 风险1 对策：只收小体积文本型 PDF）
    upload_max_size: int = 5 * 1024 * 1024  # 5MB
    upload_max_pages: int = 5
    # 相对启动目录（和 .env 一样，统一从 backend/ 启动）；在 web 根目录之外，不对外暴露
    upload_dir: str = "uploads"

    # 阶段2：大模型（OpenAI 兼容协议；Key 只放 .env，铁律第1条）
    ai_base_url: str = ""  # 如 https://dashscope.aliyuncs.com/compatible-mode/v1
    ai_model: str = ""  # 如 qwen-plus / deepseek-chat
    ai_api_key: str = ""
    ai_max_tokens: int = 2000  # 单次调用输出上限，控成本
    ai_timeout_seconds: float = 60.0
    daily_analysis_limit: int = 20  # 每人每日分析次数上限（按匿名 cookie 统计）


settings = Settings()
