# 项目：AI 简历分析 + AI 模拟面试

> **本文件是干嘛的**：项目"户口本"——技术栈、启动命令、代码约定、铁律都记在这里。AI 每次会话开工必读；只在阶段切换或约定变更时更新。

## 当前状态
- 当前版本：v3.4（双端分离 + LangChain AI 客服；分支 main；v3.4 代码尚未 git commit）
- 项目根目录：E:\AIDevelop\AIProject
- 权威计划：PROJECT-PLAN.md（改需求先改它）
- 协作约定：AI-COLLABORATION.md（每次会话先读本文件和 PROGRESS.md）

## 技术栈
- 后端：FastAPI + Postgres 16（pgvector）+ SQLAlchemy 2 + Alembic + Celery/Redis
- 前端：Vue 3 + Vite（JavaScript 起步，配置文件用 vite.config.ts）
- AI：OpenAI 兼容协议（当前：DeepSeek deepseek-chat；换通义 = 改 backend/.env 三行），Key 只放 backend/.env
- Agent：LangChain 0.3 稳定线（langchain / langchain-openai，锁 <0.4；1.x 已移除 AgentExecutor 故不升、不引 langgraph），ReAct Agent + 单工具 kb_search，SSE 流式
- Embedding：Ollama 本地（nomic-embed-text 768 维，OpenAI 兼容 API）；切云端改 settings 三行
- 向量检索：pgvector HNSW 余弦索引
- 后台任务：Celery + Redis
- 测试：pytest（阶段0 起，冒烟测试）

## 启动命令（2026-08-31 阶段0 全部实测通过）
> 统一从 backend/ 目录启动后端和 alembic——`.env` 读取相对当前目录，离开 backend/ 会找不到配置。

```bash
# 1. 数据库（仅 Postgres 容器，具名卷 pgdata）
docker compose up -d db          # 项目根目录执行

# 2. 后端（FastAPI，8000 端口；--reload 改代码自动重启）
cd backend
.venv\Scripts\python -m uvicorn app.main:app --reload

# 3. 前端（Vite，5173 端口；/api/health 代理到后端 /health）
cd frontend
npm run dev

# 数据库迁移（改模型后）
cd backend
.venv\Scripts\python -m alembic upgrade head          # 应用迁移
.venv\Scripts\python -m alembic revision --autogenerate -m "说明"  # 生成迁移

# v3.0 Playground：预置语料入库 + RAG 评测（backend/ 目录下执行）
./.venv/Scripts/python -m scripts.seed_kb_preset             # 入库 data/preset_kb 语料
./.venv/Scripts/python -m scripts.seed_kb_preset --reset    # 清空预置语料重新入库
./.venv/Scripts/python -m scripts.eval_rag --report         # 跑黄金问答集评测（写 data/kb_eval/report.md）

# Ollama embedding（本地向量化，首次需拉模型，已入 ollama_data 卷）
docker compose up -d ollama          # 起 Ollama 服务（端口 11434）
docker exec ai-interview-ollama ollama pull nomic-embed-text   # 若模型缺失则拉取

# 冒烟测试
cd backend
.venv\Scripts\python -m pytest

# 代码检查
cd backend
.venv\Scripts\python -m ruff check . && .venv\Scripts\python -m ruff format .
```

验证：浏览器打开 http://localhost:5173 ，首页三个徽标应为「运行中 / 已连接 / 0.1.0」；`/health` 是基础设施检查，不带 `/api` 前缀。

## 代码约定
- commit message：conventional commits（feat: / fix: / docs: / test: / chore:）
- 数据库改动必须走 Alembic 迁移，禁止手动改表
- Python 代码带类型注解，格式化用 ruff
- 目录：后端代码在 backend/app；业务路由挂 /api 前缀（/health 例外，供基础设施检查）
- 环境变量只放 backend/.env；前端本地开发走 Vite 代理调 /api，天然同源，不加 CORS
- 测试简历集：test-resumes/ 5 份 PDF（生成脚本 scripts/generate_test_resumes.py）；改解析/提示词后必须重跑对比（回归基准）
- AI 提示词带版本号 PROMPT_VERSION（backend/app/services/prompts.py）：改提示词必须递增，旧版本报告自动失效不复用
- v3.0 约定：切块/embedding 改动后必须跑 scripts/eval_rag.py 对比基线（data/kb_eval/report.md）；embedding 模型维度变更需新迁移 + 重新入库（Vector(768) 写死在迁移里）
- v3.0 目录：预置语料 data/preset_kb/（新增语料放这里跑 seed 脚本）；黄金问答集 data/kb_eval/qa.json（改检索逻辑先加题再验证）
- v3.4 约定：AI 客服会话与在线对话共用 chat_sessions/chat_messages，靠 session_type('agent'/'chat') 隔离；改 Agent 提示词/工具后用真机冒烟确认 action/observation 事件；LangChain 不升 1.x

## Git / GitHub 策略（用户 2026-08-31 指示，覆盖原计划的逐阶段推送）
- 现在：本地 git commit + 每阶段验收后打 tag，**不推送远端**
- 全部阶段（v1.0）完成后：一次性建 GitHub 仓库并推送全部提交与 tag

## 铁律
1. API Key 只放 backend/.env，.env 永不入库
2. 只做当前阶段的事，不顺手重构、不提前引入后续技术
3. 改动超过 3 个文件先出方案
4. 测试简历集（PROJECT-PLAN.md §6）是回归基准，改解析/提示词必须重跑
5. 执行任何命令前，先用一句大白话解释它做什么、动什么、有没有风险，解释完再执行
6. 写每个文件前，先说明这个文件是干嘛的、为什么这样命名；约定俗成的固定文件名（如 .gitignore、README.md）要指出"这是约定名，不能改"

## 环境现状（2026-09-04 复核）
- git 2.52.0 ✓（身份已配置：十四 / 2578415251@qq.com）
- Python 3.13.9 ✓ / Node.js 24.12.0 ✓ / Docker Desktop 29.7.2 + Compose v5.4.0 ✓
- Postgres 容器 ai-interview-db 运行中（具名卷 pgdata）；开发端口 8000（后端）/ 5173（前端）
- AI：DeepSeek deepseek-chat，Key 已填 backend/.env ✓；LangChain 0.3.30 已装
- 容器：db / ollama(nomic-embed-text 768) / redis 三个；跑全量 pytest 前三者都需在（Celery 用例依赖 redis）
- 已知：Docker Desktop 手动启动（不随开机自启）；Vite 只绑 IPv6 [::1]，curl 用 localhost 不用 127.0.0.1
