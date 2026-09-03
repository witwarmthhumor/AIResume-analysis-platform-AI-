"""配置中心：全项目唯一的配置出口。

.py 里写默认值，.env 里的同名变量覆盖它（pydantic-settings 负责读取和类型转换）。
注意：env_file 是相对"启动命令所在目录"的——统一从 backend/ 目录启动后端和 alembic。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://ai:ai@localhost:5432/ai_interview"

    # 日志（P0）：级别 INFO/WARNING/ERROR；log_file 为空时只输出到控制台
    log_level: str = "INFO"
    log_file: str = ""

    # 数据库连接池（P1）：SQLAlchemy 连接池参数
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800  # 定期回收，避免连接被服务端断开后仍被复用

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

    # 阶段3：文字模拟面试
    max_interview_turns: int = 10  # 单场面试最大轮次，防无限聊（PROJECT-PLAN §2）
    daily_interview_message_limit: int = 100  # 每人每日 AI 回复条数上限（按匿名 cookie 统计）
    interview_abandon_minutes: int = 30  # 超时无活动自动置 abandoned（P1）

    # 阶段6（v3.0）：Playground 本地 embedding。切云端只改这三行（base_url/api_key/model）
    embedding_base_url: str = "http://localhost:11434/v1"  # 本地 Ollama；docker 内为 http://ollama:11434/v1
    embedding_api_key: str = "ollama"  # Ollama 本地不校验，占位；云端填真实 key
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768  # 与 nomic-embed-text 对齐；换模型（如 bge-m3 1024 维）需新迁移+重向量化

    # 切块与检索
    kb_chunk_size: int = 600  # 块目标字数
    kb_chunk_overlap: int = 60  # 相邻块重叠字数，保上下文连续
    kb_search_top_k: int = 5  # RAG 召回块数
    kb_min_similarity: float = 0.65  # 余弦相似度低于此值视为无关，不作引用来源（实测相关 0.70+，无关 0.60-）
    daily_playground_limit: int = 50  # 每人每日 Playground 提问上限

    # 知识库上传配额：上传会触发切块+批量向量化（消耗 Ollama 资源），需与提问同等级别的限额
    daily_kb_upload_limit: int = 10  # 每人每日上传文档次数上限（按归属者统计）
    kb_max_documents_per_owner: int = 20  # 每个归属者名下（未删除）文档数上限

    # 运行环境：prod 时 JWT 弱默认密钥直接拒绝启动（dev 只告警，方便本地起服务）
    app_env: str = "dev"

    # 登录防爆破：按 IP+邮箱计失败次数，超限锁定。内存实现（进程重启即解锁），
    # 多 worker 部署需换 Redis 集中计数——当前单 worker 部署够用
    login_max_failures: int = 10
    login_lockout_minutes: int = 15

    # 阶段4：JWT 与 Celery/Redis
    jwt_secret_key: str = "change-me-in-backend-env"
    jwt_expire_minutes: int = 60 * 24
    jwt_secure_cookie: bool = False  # 本地 HTTP 开发为 False，生产 HTTPS 再改 True
    redis_url: str = "redis://localhost:6379/0"
    celery_result_url: str = "redis://localhost:6379/1"


settings = Settings()
