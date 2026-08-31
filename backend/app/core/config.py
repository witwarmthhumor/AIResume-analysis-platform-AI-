"""配置中心：全项目唯一的配置出口。

.py 里写默认值，.env 里的同名变量覆盖它（pydantic-settings 负责读取和类型转换）。
注意：env_file 是相对"启动命令所在目录"的——统一从 backend/ 目录启动后端和 alembic。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://ai:ai@localhost:5432/ai_interview"


settings = Settings()
