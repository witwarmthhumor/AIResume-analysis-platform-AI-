# AI 简历分析 + AI 模拟面试

[![CI](https://github.com/witwarmthhumor/AIResume-analysis-platform-AI-/actions/workflows/ci.yml/badge.svg)](https://github.com/witwarmthhumor/AIResume-analysis-platform-AI-/actions/workflows/ci.yml)

面向求职者的简历智能分析与文字模拟面试工具，定位为个人学习与求职作品集项目。

## 快速体验（约 5 分钟）

不熟悉本项目时，按这个顺序看最快（详细环境要求见下方「本地跑起来」）。

**1. 准备环境 + 造演示数据**（一条命令）

```bash
bash scripts/demo-up.sh
```

它会依次做：启动 Docker Desktop（若未运行）→ 拉起 db/redis/ollama 三容器并等健康 → 执行 Alembic 迁移 → 跑环境自检 → 造演示数据。脚本**不会**替你常驻启动前后端（长驻进程放在脚本里容易被回收，反而更难排查），跑完会打印下面两条命令。

**2. 起前后端**（两个终端）

```bash
cd backend && .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

**3. 登录看数据**

打开 http://localhost:5173 ，用**用户名或邮箱**登录（v4.1 起登录标识为用户名，邮箱兼容）：

| 账号 | 角色 | 能看到什么 |
|---|---|---|
| `admin` / `123456` | 内置管理员 | 管理端五项（在线对话 / 使用日志 / 数据看板 / 语料库管理）+ 右下角 AI 客服悬浮窗（`seed_admin.py` 播种，登录后请立即改密） |
| `demo_admin`（`admin@airesume-demo.com`） | 演示管理员 | 同上 + 名下一整套演示业务数据 |
| `demo_user`（`user@airesume-demo.com`） | 演示普通用户 | 简历评估 / 模拟面试 / 个人中心，以及名下的简历、分析报告、面试复盘 |

演示数据是**语义自洽**的一套（简历 → 分析 → 面试 → 对话 → 记账），不是随机塞行：分析报告七字段齐全、面试有四维评分与 6 条对话原文、记账覆盖近 7 天（看板柱状图有起伏）。

推荐体验路径：**首页选入口 → 简历评估打开分析报告（试「版本对比」）→ 模拟面试看复盘雷达图与「继续上次会话」→ 个人中心改密体验全端下线 → 管理端数据看板 + 右下角 AI 客服悬浮窗（可试「🚀 一键求职准备」贴 JD 走 LangGraph 状态图 + 人工审批）**。

**只想重置/只看一遍命令**：

```bash
cd backend && .venv\Scripts\python -m scripts.seed_demo --reset   # 清掉演示数据重建
cd backend && .venv\Scripts\python -m scripts.check_env           # 10 项环境自检（含数据一致性）
bash scripts/demo-up.sh --no-seed                                 # 只准备环境，不造演示数据
```

## 当前功能

- 简历上传解析：**PDF / Word（docx）≤10MB**（v4.2）与 hash 去重；AI 简历分析：岗位匹配、优势、短板、关键词缺口、改进建议、预测面试题
- 多轮文字模拟面试：SSE 流式回复、消息恢复、结束评价，支持智能出题（实习/校招/社招）；**LangGraph 图编排 + checkpoint 断点续跑**（v4.1 S1），**可加载面试题库按序出题**（v4.2，题库模式零 LLM 出题调用）
- 🎧 **录音分析**（v4.2）：上传面试录音 → **faster-whisper 本地转写**（Celery 异步）→ 可编辑文本 → **角色审核**（LLM 按面试官/候选人分段标注）→ **面试审核**（四维评分报告，与模拟面试报告同口径）；历史记录随时回看
- 📝 **面试题生成**（v4.2）：基于简历一键生成 10~20 道定制化面试题（基础/项目/深挖分维度 + 难度星级），保存为题库
- 用户注册/登录（**用户名标识**，手机号兼容；v4.2.1 起注册不再收集邮箱，存量账号保留）、修改密码（改密即全端下线）、JWT + token_version、**/api/\*\* 默认拒绝闸门**、用户数据隔离、个人信息（预设头像 + 身份证号 + 手机号）、个人中心（本人五卡统计 + 近 7 日用量 + 改密）
- 🧪 **知识库问答 / 在线对话**（v3.0 → v3.1 → v3.5）：基于预置语料库（面试题/八股文/岗位 JD）+ 用户上传文档，**混合检索（向量 + BM25，RRF 融合）** + RAG 流式回答，带引用来源；**多会话管理**（新建/切换/删除，标题自动生成）
- 🤖 **AI 客服 Agent**（v3.4 → v3.10）：基于 LangChain 1.x ReAct Agent（`create_agent` 图编排 + `astream_events` 流式桥），**SSE 流式输出 + 工具调用过程可见**，会话历史持久化；**13 个工具**——检索/查询类 9 个（知识库检索 / 我的简历 / 我的面试记录 / 面试问答原文 / 评分趋势 / 历史对话检索 / 我的用量 / 读分析报告 / 语料清单）+ 平台功能说明 1 个 + 工具内 LLM 类 3 个（岗位匹配 job_match / 出题 question_gen / 回答点评 answer_review，独立限额）；工具路由实测 40/40 = 100%；用户端整页形态 + 管理端右下角悬浮形态
- 🚀 **一键求职准备**（v4.0 LangGraph 试点）：AI 客服页新页签，贴入 JD 后自动串起「读简历 →（补）AI 分析 → 岗位匹配 → 定制出题 → 整理交付」，步骤条实时流转；**创建面试场次前先弹审批卡（TTL 倒计时，批准才建场次）**；失败自动回环重试（≤2 次）并可从失败节点续跑；结果分区展示分析/匹配（命中词、缺口词）/出题（含出题依据）；LangGraph 1.2 + PostgresSaver 断点续跑，全程 trace span 落库可回看
- 📊 **数据看板**（v3.2）与 📋 **使用日志**（v3.3）：五卡统计 + 用户列表分页 + 近 7 日用量柱状图；使用明细支持动作/模型/IP/日期范围筛选与后端分页
- 📈 **面试复盘雷达图**（v3.5）：结束评价的四维度评分可视化，可叠加本人历史场次对比
- 🔀 **分析版本对比**（v3.5）：同一份简历历次分析（不同提示词版本）并排对比，标出各自独有条目
- 🗂 **语料库管理**（v3.1）：管理端可查看全部文档（含预置与所有用户上传）并删除，上传可直接入预置语料
- 双端导航分离：管理端五项（首页/在线对话/使用日志/数据看板/语料库管理），普通用户三项（首页/AI 客服/个人中心）
- Redis + Celery 异步任务基础

## 技术栈

- 后端：Python + FastAPI + PostgreSQL（pgvector）+ SQLAlchemy / Alembic
- 前端：Vue 3 + Vite（零 UI 框架，手写 CSS 设计系统），生产环境由 Nginx 提供静态文件并反代 `/api`
- 异步：Redis 7 + Celery
- AI：国产大模型 OpenAI 兼容协议（当前 DeepSeek，可切换通义）
- Agent（v3.4）：LangChain 1.x（`langchain` 1.4 / `langchain-openai` 1.6）+ `create_agent` 图编排，`astream_events` 桥接 SSE 流式（v4.4 依赖链解冻）
- Agent v2（v4.0 试点）：**LangGraph 1.2** + PostgresSaver 断点持久化 + interrupt/Command 人机协同；与 v1 双轨并存（`agent_v2_enabled` 开关，v2 异常不影响 v1）
- Embedding（v3.0）：Ollama 本地 nomic-embed-text（768 维，OpenAI 兼容 API），pgvector HNSW 向量检索；可切云端 embedding

## 生产 Docker 一键启动

前置：安装 Docker Desktop，并准备一个真实的部署环境文件：

```bash
cp backend/.env.docker.example backend/.env.docker
# 编辑 backend/.env.docker，至少填写 POSTGRES_PASSWORD、JWT_SECRET_KEY、AI_API_KEY

docker compose -f docker-compose.prod.yml up -d --build
```

访问 `http://localhost`（或设置 `APP_PORT=8080` 后访问 `http://localhost:8080`）。

> **v4.1 起登录防爆破已迁 Redis 集中计数**（`login:fail:*` 键，INCR+EXPIRE 续窗），多 worker 部署语义正确；Redis 故障时 fail-open 放行登录并记告警日志，不把缓存变成登录单点。
>
> **单 worker 限定（v4.4.1 注记）**：Agent v2 / 求职准备的 SSE 事件总线是进程内实现（`services/agent_v2/event_bus.py`），uvicorn 必须单 worker 启动（现状即如此）；未来多副本 / 多 worker 部署前需先把事件总线换成 Redis Pub/Sub（订阅/补发接口已抽象，替换面可控）。

查看状态和日志：

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend worker frontend
```

停止服务但保留数据：

```bash
docker compose -f docker-compose.prod.yml stop
```

数据卷：`pgdata`（Postgres）、`redisdata`（Redis）、`uploadsdata`（简历文件）、`ollama_data`（embedding 模型，重建容器不重复下载 ~275MB）。生产环境应定期备份 Postgres，并限制卷和 `.env.docker` 的文件权限。

## 架构

```mermaid
flowchart LR
  Browser[浏览器] --> Nginx[Nginx :80]
  Nginx --> API[FastAPI :8000]
  API --> DB[(Postgres 16 + pgvector)]
  API --> Redis[(Redis 7)]
  API --> Ollama[Ollama 本地 embedding]
  API --> Agent[LangChain ReAct Agent]
  Agent --> Ollama
  Agent --> AI[OpenAI 兼容大模型]
  Redis --> Worker[Celery Worker]
  Worker --> DB
  Worker --> AI
  Worker --> Ollama
```

## 本地开发启动

按开发模式分别启动（先启动 Docker 中的 db / redis / ollama）：

```bash
docker compose up -d db redis ollama
cd backend
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
# 新窗口
cd frontend
npm install
npm run dev
```

本地页面：[http://localhost:5173](http://localhost:5173)

## 启动前自检

一条命令看清所有前置条件（Docker / 三容器 / 数据库与迁移 / Redis / 端口 / 探针 / 语料 / AI 通道），任一失败不中断，全部跑完后给汇总，失败项直接附可复制的修复命令：

```bash
cd backend
.venv\Scripts\python -m scripts.check_env             # 全量检查（AI 项消耗一次 1 token 级请求）
.venv\Scripts\python -m scripts.check_env --skip-ai   # 跳过 AI 通道检查，不消耗额度
```

输出示例（节选）：

```
-- Docker 与容器 --
  [OK] Docker daemon：可用（Server 29.7.2）
  [FAIL] 三容器健康（db/redis/ollama）：未达 healthy：ai-interview-db（exited）
        → 修复: docker start ai-interview-db ai-interview-redis ai-interview-ollama

== 汇总：通过 8 项 / 失败 1 项 ==
```

退出码：全部通过为 0，存在失败为 1（可被 CI 或外层脚本判定）。容器闲置会自行退出（假死），建议每次启动项目前先跑一遍自检。

> AI 客服与知识库问答依赖 Ollama 提供 embedding，首次启动需拉模型：`docker exec ai-interview-ollama ollama pull nomic-embed-text`。预置语料入库与 RAG 评测见下节。

## 隐私与安全

- 简历只在用户主动上传/分析/面试时处理；AI 分析会把简历内容发送给第三方大模型服务。
- 简历原文件存储在应用数据卷，不由 Nginx 直接暴露；应用提供删除入口。
- 日志不打印简历正文、面试内容或 API Key。
- 真实 `.env`、`.env.docker`、API Key、JWT 密钥和演示账号密码不入 Git。
- 云服务器只开放 80/443；Postgres 5432 和 Redis 6379 不开放公网。启用 HTTPS 后设置 `JWT_SECURE_COOKIE=true`。

## 测试与检查

```bash
cd backend
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
cd ..\frontend
npm run build
```

## 项目文档

- `docs/database-schema.md`：数据库表结构（public 16 张表 + `langgraph` 4 张编排表）说明与查看方式
- `docs/tech-stack-and-features.md`：技术栈与功能点清单
- `docs/后续开发规划.md`：v3.4 之后的方向、优先级与推进顺序


## 技术亮点（真实代码支撑 · v4.4.1）

### 架构与工程
- **Router → Service → Model 三层**：API 路由不堆业务逻辑，限流/记账/分析落库全下沉到 `services/`（`usage_service.py`、`analysis_service.py`），`RouterRegistry` 自动注册路由，`main.py` 仅保留两行注册调用
- **统一错误体系**：自定义异常类（`ValidationError` / `AuthenticationError` / `NotFoundError` / `RateLimitError`） + 全局 `exception_handler`，所有 4xx/5xx 输出 `{"code":"...","message":"...","details":null}`，成功响应保持原结构
- **Alembic 版本化迁移**：**17 次**迁移全线可用（含 pgvector 向量表与 HNSW 索引、`langgraph` checkpoint schema 预建、v4.0 四张编排表、v4.1 用户名/`token_version` 回填、v4.2 题库与录音分析表、v4.2.1 注册放开邮箱非空），禁止手改表；`alembic check` 无漂移；`created_at` 全表索引，`user_id` / `session_id` / `anonymous_id` 等关键查询字段均索引
- **Docker 六容器生产编排**：Nginx + FastAPI + Celery Worker + PostgreSQL（pgvector）+ Redis + Ollama，健康探针 + 依赖编排 + 具名卷持久化，`docker compose -f docker-compose.prod.yml up -d --build` 一键启动

### AI 与异步
- **AIService 统一封装**（`ai_client.py`）：OpenAI 兼容协议，换模型 = 改 `.env` 三行；Pydantic 强校验 + 失败自动重试（最多 2 次），失败 ERROR 日志含模型/异常原文/重试次数
- **SSE 流式面试**：面试官回复逐字推送，三点跳动打字动画 + 头像气泡 UI
- **Celery + Redis 异步**：解析/AI 分析迁移到 Celery 任务，任务状态可查询；面试 SSE 保持同步流式
- **智能出题**：`position_type`（intern/fresh/senior/通用）按方向调整提示词难度与提问方向，PROMPT_VERSION 递增
- **RAG 检索（v3.5 混合检索）**：`kb_chunker.py` 段落合并切块（~600字/块 + 60字重叠，无内容丢失）；检索为**向量 + BM25 双路召回 + RRF 融合**——向量路走 pgvector HNSW（数据库排序），词法路用 jieba 分词 + 手写 BM25；`kb_service.py` 幂等入库；问答 SSE 流式回答带引用来源（来源可折叠展开）
- **可量化的检索质量**：`scripts/eval_rag.py` 同一份 49 题黄金问答集并排跑「纯向量 vs 混合」——hit@1 **71.4% → 87.8%**，hit@5 **91.8% → 100%**，报告落 `data/kb_eval/report.md`
- **异步入库**（v3.0）：`ingest_kb` Celery 任务切块+向量化，用户上传文档提交任务后状态 pending→processing→ready
- **LangChain ReAct Agent**（v3.4 / v3.5 扩工具）：`services/agent/` 四件套——`llm_factory`（ChatOpenAI streaming，未配 Key 抛 ValueError → API 503）、`tools`（**每请求闭包工厂**，绑定本次请求的 db 会话与归属者，杜绝多请求串数据）、`executor`（1.x `create_agent` 图 + 后台线程内 `astream_events` 泵，把 `on_tool_start` / `on_tool_end` / `on_chat_model_stream` 转成 action / observation / delta 事件队列，SSE 协议零变化）
- **13 个 Agent 工具**（v3.10）：检索/查询类 9 个——`kb_search`（知识库，可按 `document` 限定单篇）、`resume_lookup`（本人简历 + 关键词定位）、`interview_history`（本人面试场次与四维评分）、`interview_transcript`（本人面试问答原文）、`score_trend`（评分趋势）、`conversation_search`（历史对话检索）、`usage_stats`（本人用量，可按 `action` 过滤）、`analysis_read`（读本人分析报告）、`kb_list`（语料清单）；静态类 1 个 `platform_help`；工具内 LLM 类 3 个 `job_match` / `question_gen` / `answer_review`（走 `_run_tool_llm` 独立限额）。全部按归属者过滤，无身份时明确拒绝而非返回空结果
- **路由准确率有量化兜底**：`scripts/eval_agent_routing.py` 40 题 × 13 工具，top-1 **40/40 = 100%**（`data/agent_eval/report.md`）；工具描述按「什么时候用 / 什么时候**不**用」三段式写，易撞的工具互相点名排他（如 `resume_lookup` 原文 ↔ `analysis_read` 结论）
- **Agent SSE 事件协议**（v3.4）：`meta → (action → observation)* → delta* → done{content, iterations, tokens, citations, message_id}`，异常走 `error` 事件；工具调用过程落库 `chat_messages.tool_steps`（JSONB），前端可折叠回看
- **Agent 工具容错**：工具内部吞掉检索类异常并返回自然语言说明，让 Agent 换通用知识兜底而非整轮崩掉；未命中时清空上一轮残留引用，避免它编造「知识库说」
- **LangGraph 长流程编排**（v4.0）：`services/agent_v2/` 用 `StateGraph` 串起 `planner → load → analyzer? → matcher → questioner → verifier(回环≤2) → hitl_gate → deliver`，条件边跳过已有分析；`PostgresSaver` 断点持久化（Alembic 预建 schema，不在请求路径跑 `setup()`，规避 `CREATE INDEX CONCURRENTLY` 与流式事务互等死锁）；`interrupt` 人工审批（TTL 30min，到点终止并可 `retry-node` 续跑）；approve/abort 走条件更新乐观锁；事件总线进程内订阅 + 重连回放；lifespan 回收停机孤儿的 run——与 v1 ReAct 轨双轨并存，`agent_v2_enabled` 一键回退

### 安全与隐私
- **JWT HttpOnly Cookie + Argon2 密码哈希**：密码不落明文，JWT 不可读
- **用户数据隔离**：简历/分析/面试全量过滤 `user_id`，跨用户 404
- **简历软删除**（`deleted_at`）：删除后列表/详情 404，数据可审计，同文件可重新上传
- **上传三道防线**：扩展名 + magic bytes 双校验、文件名消毒、5MB/5页限制
- **日志不打印正文/Key**：日志系统按模块分级，AI 失败日志不含简历正文与 API Key
- **每日限流**：双轨（anonymous_id / user_id），基于 `usage_logs` 按日聚合，超限 429 含恢复时间

### 前端
- **手写 CSS 设计系统**（`main.css`）：零 UI 框架依赖，全局变量 + 通用类 + 动效，卡件入场动画、呼吸脉冲、打字动画
- **统一 fetch 封装**（`api.js`）：全组件收敛，自动 credentials + JSON 序列化 + 错误统一解析
- **双端导航分离**（v4.2.1）：**未登录只见独立登录首屏**（登录成功才进系统）；普通用户侧边栏四项业务模块（简历评估 / 录音分析 / 面试题生成 / 模拟面试），个人信息 / 改密 / 退出收进顶栏头像下拉；管理端四项（在线对话 / 使用日志 / 数据看板 / 语料库管理）并挂 Agent 悬浮气泡，按 `role` 动态渲染导航集合
- **AI 客服两形态复用**（v3.4）：`AgentChatCore`（核心：SSE 打字机 + 工具过程折叠 + 引用来源）被整页视图 `AgentChatView` 与右下角 400px 浮层 `AgentWidget` 共用，`props.compact` 切换密度
- **纯手写数据可视化**（v3.2 / v3.3 / v3.5）：零图表库——CSS 渐变柱状图（近 7 日用量）+ 手写日期范围选择器 `DateRangePicker.vue`（6×7 日历网格 + 时间输入 + 点击外部关闭）+ **面试评分雷达图**（四维度、支持叠加历史场次对比）
- **数据看板**（v3.2）：五卡悬浮（用户/简历/分析/面试/今日 Token）+ 用户列表前端分页 + 双卡片等比布局，窄屏自动堆叠
- **安全与流程收尾批次**（v4.4.1）：音频上传补 **magic bytes 文件头校验**（防改扩展名伪装，whisper 解码前 415）；prod 启动断言 `validate_prod_settings`（Secure Cookie / 登录闸门 / 首用户提权三项自相矛盾配置直接拒启）；管理端删除语料落 `audit_logs`（谁/何时/IP/删了哪篇）；录音转写新增**快速档**（每请求 `fast=true` 用 base 小模型换速度，前端勾选透传）；pre-commit 提交钩子**真启用**（装依赖 + git hook，仓库级 ruff 双道）；CI pytest 步骤加**覆盖率门槛 85%**（实测 90.22%）；run-app 技能修复数据库容器名冲突（已存在改走 `docker start`）
- **依赖链解冻**（v4.4.0）：LangChain 0.3/LangGraph 0.2 整体切 1.x（langchain 1.4.3 / langgraph 1.2.12 / checkpoint-postgres 3.1.2）——v1 executor 重写为 `create_agent` + `astream_events` 桥（SSE 协议零变化），v2/interview 图仅修一处节点 `config: RunnableConfig` 注解即兼容；pip-audit 漏洞 **14 → 0**，CI 的 12 个版本锁死豁免全部删除；升级窗口清场在途 run（旧 checkpoint 能读不能续跑，见 docs/依赖链解冻调研报告.md）
- **工程质量门禁批次**（v4.3.0）：列表接口分页收口（admin/users 服务端 envelope 分页，其余列表 limit/offset 安全上限）；Agent 工具层拆包 `services/agent/tools/`（六域模块 + registry 单一数据源，`GET /api/agent/tools` 下发工具中文名，前端不再手写映射）；前端接入 **vue-router（hash）+ vitest（SSE 状态机 19 例）+ eslint**；CI 新增 **pip-audit / npm audit 依赖审计**（12 个被 langchain/langgraph 版本锁死的漏洞 ID 显式豁免并注明解冻条件）与 Dependabot 周检；`scripts/release_check.py` 收口闸门机器核对文档口径（pytest 数/工具数/迁移数/版本号/git tag）——文档口径失真 #23/#26/#27 三次复发的根治
- **报告版本对比**（v3.5）：简历分析报告可取历次 `prompt_version` 两版并排，逐条标出「仅本版有」——改提示词后能直观看出结论变化

### 测试
- **397 个 pytest 全量覆盖**：上传校验（含 docx）、分析、面试（SSE/状态机/图编排/题库驱动）、认证（用户名/手机号注册登录/改密全端下线/锁定 fail-open/**登录闸门真身/跨用户越权 sweep**）、个人信息（头像/身份证/手机号一次绑定）、**面试题库（生成/归属隔离/题库驱动面试）**、**录音分析（转写 stub/角色审核/面试审核/记账）**、任务、隔离、软删除、知识库（切块器/检索/owner 隔离/Playground SSE）、**混合检索（BM25 分词/排序/阈值回退）**、在线对话（多会话 CRUD/隔离/标题自动生成）、管理端语料库、使用日志筛选分页、Agent（工具命中/未命中兜底/回调事件/SSE 编排/归属校验/限额/503）、**Agent 个人数据工具（归属隔离/无身份拒绝/聚合口径）**、**面试评分列表与分析版本列表**、**LangGraph v2 编排（图节点/审批超时与并发乐观锁/事件总线多订阅者/断点重连快照/孤儿 run 回收/SQL 分页聚合）**；全部 mock AI/embedding/whisper 不烧额度
- ruff check + ruff format --check 全绿（CI 两道都跑）、npm build 通过
- **RAG 有量化基线**：`scripts/eval_rag.py` 跑 49 题黄金问答集并排对比「纯向量 / 混合」，改检索逻辑必须对比 `data/kb_eval/report.md`
- **工具路由有量化基线**：`scripts/eval_agent_routing.py` 40 题 40/40 = 100%（`data/agent_eval/report.md`），改工具描述/增删工具必须对比
- **Agent v2 有端到端基线**：`scripts/eval_agent_e2e.py` 11 个黄金任务离线确定性全过（`data/agent_eval/e2e_report.md`），改图结构/节点逻辑必须对比
