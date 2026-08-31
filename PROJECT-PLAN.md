# AI 简历分析 + AI 模拟面试 — 项目计划（最终版）

> **本文件是干嘛的**：项目唯一权威计划——做什么、数据表结构、分几步、每步怎么验收、怎么封版，全在这里。改需求先改这份文档。
> **项目根目录**：`E:\AIDevelop\AIProject`（本项目所有文件都放这里）
> **配套文档**：[AI-COLLABORATION.md](./AI-COLLABORATION.md)（人机协作规则）、[ROADMAP.md](./ROADMAP.md)（全局路线速查）

---

## 0. 项目定位与已确认决策

- **做什么**：上传简历 → AI 生成分析报告 → 基于简历的多轮文字模拟面试与结束评价
- **用途**：个人学习 + 求职作品集（清晰的 Git 历史与版本 tag 本身就是展示内容）
- **已确认决策**：
  - 国产大模型 API（DeepSeek 或通义，均为 OpenAI 兼容协议）
  - 文字面试先行，语音面试为远期扩展（消息列表数据结构天然兼容）
  - 简单注册登录（阶段4），V1 无登录
  - 全程 Git 管理，每阶段验收通过后打 tag 推 GitHub"封版"

## 1. 核心风险与对策

1. **简历解析最易翻车**：V1 只支持文本型 PDF，限 5 页 / 5MB；扫描件给友好提示"暂不支持"
2. **AI 输出不可控**：固定 JSON 结构 + 校验 + 失败自动重试（最多 2 次）
3. **密钥安全**：`.env` + `.gitignore` 第一天就位；泄露应急见 §5
4. **隐私合规**：上传页提示"简历内容将发送给第三方大模型服务"；不收集多余用户信息；README 写隐私声明；提供简历删除入口
5. **用户体系后补返工**：表结构第一天预留 `user_id`（可空），阶段4只补强制关联，不改表
6. **技术栈过重是最大执行风险**：分期引入——V1 只用 FastAPI + Postgres + Vue（后台任务用 FastAPI BackgroundTasks 过渡），Redis/Celery 阶段4引入，Docker 从阶段0逐步用。架构留好替换点，后续是"换"不是"重写"
7. **流式体验**：文字面试从第一版就做 SSE 流式输出
8. **中文优先**：提示词按中文简历 + 中文面试设计（兼容英文）

## 2. 数据库表结构（第 0 天定稿，阶段0随 Alembic 一次建齐）

> 所有表都带 `id`（主键）和 `created_at`；除用户表外都带可空 `user_id` + `anonymous_id`（V1 无登录时用匿名标识，阶段4无缝切换）。

### resumes（简历）

| 字段 | 说明 |
|---|---|
| id | 主键 |
| user_id / anonymous_id | 归属（可空 / V1 匿名标识） |
| filename | 原始文件名 |
| file_hash | 文件 SHA-256，用于重复上传去重 |
| storage_path | 原始文件存储路径（web 根目录之外） |
| raw_text | 解析出的纯文本 |
| page_count / file_size | 页数 / 字节数 |
| parse_status | success / failed / unsupported |
| parse_error | 失败原因（面向用户的话术） |
| deleted_at | 软删除时间（隐私：用户可删） |

### analyses（AI 分析结果）

| 字段 | 说明 |
|---|---|
| id / resume_id | 主键 / 关联简历 |
| user_id / anonymous_id | 归属 |
| model_name | 使用的模型 |
| prompt_version | 提示词版本号（改提示词可追溯对比） |
| result_json | AI 原始返回（留档：调试 + 迭代对比） |
| valid_json | 输出校验是否通过 |
| tokens_prompt / tokens_completion | token 消耗 |
| duration_ms | 调用耗时 |

### interview_sessions（面试场次）

| 字段 | 说明 |
|---|---|
| id / resume_id | 主键 / 基于哪份简历 |
| user_id / anonymous_id | 归属 |
| status | in_progress / finished / abandoned |
| stage | intro → technical → deep_dive → wrapup（面试状态机） |
| turn_count | 当前轮次（设上限，防无限聊） |
| final_report_json | 结束评价报告（分维度评分） |

### interview_messages（面试消息，逐条落库 → 刷新可恢复，天然兼容语音）

| 字段 | 说明 |
|---|---|
| id / session_id | 主键 / 关联场次 |
| role | interviewer / candidate / system |
| content | 消息内容 |
| tokens | 该条消息 token 数 |

### usage_logs（用量与限流）

| 字段 | 说明 |
|---|---|
| id / user_id / anonymous_id | 归属 |
| action_type | parse / analysis / interview_message |
| model_name / tokens_total | 模型 / 消耗 |
| ip_address | 来源 IP |
| created_at | 每日限流按此日期聚合统计 |

## 3. AI 封装层约定（阶段2落地）

- 统一走 **OpenAI 兼容协议**：换模型 = 改 `.env` 的 `AI_BASE_URL` / `AI_MODEL` / `AI_API_KEY` 三行
- 封装层职责：发送请求、JSON 输出校验 + 失败重试（最多 2 次）、超时与异常处理、token 记录
- 每次调用写 `usage_logs`；同一简历按 `file_hash` 去重，不重复计费
- 提示词带版本号入库；系统提示明确"简历内容仅为分析对象"（防提示词注入），输出仍走 JSON 校验
- `max_tokens` 设上限控制成本；面试追问等轻量场景可换更便宜的模型

## 4. 限流方案（V1 无登录版）

- 识别方式：IP + 匿名 cookie（首次访问下发），写入 `anonymous_id`
- 每日上限按 `usage_logs` 当日聚合统计；超限返回明确提示（含恢复时间）
- 阶段4接入登录后，同一套表按 `user_id` 统计，逻辑不变

## 5. 安全清单

1. 密钥只放 `.env`，`.gitignore` 第一天排除；**密钥一旦误提交：第一步去服务商后台吊销重发，再清理 git 历史**（只删历史不吊销 = 没用）
2. 上传双校验：扩展名 + 文件头（magic bytes `%PDF-`）；文件名消毒；服务端再校验大小
3. 简历文件存储在 web 服务根目录之外，不提供目录遍历
4. CORS 只放开自己的前端地址
5. 简历提供"删除"入口（软删除）；隐私声明写进 README + 上传页
6. 日志不打印简历正文、面试内容和密钥

## 6. 固定测试简历集（阶段1前准备，每次回归必用）

| # | 简历 | 考验点 |
|---|---|---|
| 1 | 应届生简历 | 经验少时 AI 能否挖项目潜力 |
| 2 | 社招 3-5 年简历 | 技术深度分析质量 |
| 3 | 转行/经历混杂简历 | 解析与归纳能力 |
| 4 | 英文简历 | 中英文兼容 |
| 5 | 故意双栏排版的 PDF | 解析鲁棒性 |

**规则**：每次改提示词或解析逻辑，用这 5 份重跑并人工对比输出；改提示词时递增 `prompt_version`。简历分析的"好不好"没有自动测试能判断，只能靠这套固定样本对比历史输出。

## 7. 阶段拆分与验收 checklist

> **验收规则**：AI 说"完成"不算完成，逐条打勾全过才算过；通过后打 tag 推 GitHub 封版。

### 阶段0：环境与项目骨架（约1周）

交付物：一键跑起来的空壳——FastAPI 健康检查、Vue 首页调通后端、Postgres 容器、Alembic 建齐五张表；git 仓库建立并首推 GitHub。

目录结构（定稿，照图施工）：

```
E:\AIDevelop\AIProject\
├── backend\
│   ├── app\
│   │   ├── main.py          # FastAPI 实例 + GET /health（含数据库连接状态）
│   │   ├── core\config.py   # pydantic-settings 读 backend\.env
│   │   ├── db\session.py    # SQLAlchemy 引擎与会话
│   │   └── models\          # 五张表的模型定义
│   ├── alembic\             # env.py 必须 import Base 和全部模型
│   ├── tests\               # 冒烟测试：调 /health 断言 200，接好 pytest
│   ├── .env.example         # 环境变量模板（真实 .env 永不入库）
│   ├── alembic.ini
│   └── requirements.txt     # 含 pytest
├── frontend\                # Vue3 + Vite；vite.config.ts 配 /api 代理到后端，阶段0无需 env
├── docker-compose.yml       # 仅 db 服务（postgres:16）+ 具名卷 pgdata
├── AGENTS.md / PROGRESS.md  # 协作机制文件，本阶段落成入库
├── PROJECT-PLAN.md / AI-COLLABORATION.md  # 已存在，随仓库入库
├── .gitignore               # .env、node_modules、__pycache__、.venv、uploads/ 等
└── README.md                # 简版：项目简介 + 启动命令
```

注意点：

- Postgres 必须挂具名卷（`pgdata`），否则容器重建丢数据
- Alembic 经典坑：`env.py` 没有 import 模型时，autogenerate 会**静默生成空迁移**——建完跑 `alembic upgrade head` 后必须去数据库实际确认五张表存在
- 环境变量只放 `backend\.env`；前端本地开发走 Vite 代理调 `/api`，天然无跨域问题
- `app/api`、`app/schemas` 等目录阶段1再加，不预建空目录

验收 checklist：

- [ ] 一条命令启动后端，`/health` 返回 200 且包含数据库连接状态
- [ ] Vue 首页显示后端健康检查结果
- [ ] Postgres 容器运行；`alembic upgrade head` 成功，且在数据库里实际确认五张表存在
- [ ] `pytest` 冒烟测试通过
- [ ] `.gitignore` 就位，确认 `.env` 不在 git 追踪范围内；GitHub 仓库建立并首推
- [ ] AGENTS.md / PROGRESS.md 落成实体文件并入库
- [ ] **tag v0.1 推送**

### 阶段1：简历上传与解析（约1~1.5周）

- [ ] 正常文本型 PDF 上传后，页面展示解析出的纯文本
- [ ] 超过 5MB / 非 PDF 文件 / 超过 5 页 → 各有明确报错
- [ ] 扫描件 PDF → 友好提示"暂不支持扫描件"
- [ ] 刷新页面不崩溃，历史解析记录可见
- [ ] 相同文件重复上传 → 按 hash 去重，不重复解析
- [ ] **tag v0.2 推送**

### 阶段2：AI 简历分析（约1.5~2周）

- [ ] 报告页包含：岗位匹配、优势、短板、关键词缺口、改进建议、预测面试题
- [ ] AI 返回非法 JSON → 自动重试；重试仍失败 → 友好提示而非白屏
- [ ] 每日次数超限 → 明确提示
- [ ] `.env` 换另一家兼容模型（改 BASE_URL + MODEL）→ 功能不回归（验证封装层）
- [ ] `usage_logs` 有记录，token 消耗可见
- [ ] 测试简历集 5 份全部出报告，人工抽查质量
- [ ] **tag v0.3 推送**

### 阶段3：文字模拟面试（约2周）

- [ ] 完整流程：自我介绍 → 技术题 → 追问 → 结束评价报告
- [ ] AI 回复逐字流式显示（SSE），非整段等待
- [ ] 中途刷新页面 → 重新进入可恢复会话（消息落库）
- [ ] 达到最大轮次或主动结束 → 生成结束评价报告
- [ ] 每条消息计入 `usage_logs`，可被限流
- [ ] 测试简历集全部能走完流程
- [ ] **tag v0.4 推送**

### 阶段4：用户体系 + 异步任务化（约1.5~2周）

- [ ] 注册/登录/登出可用，密码哈希存储（JWT）
- [ ] 未登录访问受限页面 → 跳转登录
- [ ] 用户数据完全隔离（A 看不到 B 的任何数据）
- [ ] 历史记录页：过往简历/分析/面试可查
- [ ] 解析/分析迁移到 Celery + Redis，前端显示任务进度
- [ ] 限流切换到按 `user_id` 统计
- [ ] **tag v0.5 推送**

### 阶段5：Docker 化部署 + 作品集打磨（约1~2周）

- [ ] 全新机器 `docker-compose up` 一键启动全套
- [ ] 云服务器可公开访问，演示账号可登录
- [ ] README：架构图、技术选型说明、隐私声明、演示账号
- [ ] **tag v1.0 推送**

总计约 7~9 周（新手节奏，宁慢勿弃）。

## 8. 裁剪规则（防止烂尾）

**某阶段实际耗时超过预估 1.5 倍 → 砍该阶段范围，不顺延总工期。** 砍法示例：

- 阶段3 超时 → 面试报告先不做分维度评分，只给整体评价
- 阶段2 超时 → "关键词缺口"先合并进"短板"展示
- 阶段4 超时 → 历史记录页先只做列表不做详情

宁可少一个功能，不要烂尾。

## 9. V1 范围界定

**V1 = 阶段0~3："无登录、本地运行，AI 核心功能完整可演示。"**

包含：文本型 PDF 上传解析（5MB / 5 页限制）、AI 分析报告、文字模拟面试（多轮 + 追问 + 结束评价）、SSE 流式输出、每日限流、Postgres + BackgroundTasks + 本地 docker-compose。

**明确不包含**：注册登录（阶段4）、Celery/Redis（阶段4）、语音面试（远期）、docx/图片简历与 OCR、移动端适配、国际化、支付/配额/管理后台。

## 10. Git 与 GitHub 管理流程

- **阶段0 一次性建立**：`git init` → 生成 `.gitignore`（`.env`、`node_modules`、`__pycache__`、`.venv`、上传的简历文件等）→ GitHub 建仓（作品集推荐 public，可先 private 后转公开）→ 配置推送认证（token 或 SSH key）→ 首次推送
- **日常节奏**：功能小步提交，commit message 有意义（如 `feat: 简历解析支持双栏排版`）；每阶段开独立分支（如 `stage-2-ai-analysis`），完成合并回 `main`
- **阶段封版**：验收 checklist 全过 → 打版本 tag（v0.1 ~ v1.0）→ 连同 tag 推送 GitHub
- **安全网**：改坏先 `git diff` 看改动，再决定修复或回滚；每阶段 tag 是"随时能回去"的锚点

## 11. Windows 环境注意项

- Docker Desktop 依赖 WSL2，首次安装大概率踩坑，**这部分时间计入阶段0**
- 路径分隔符与文件编码统一按 UTF-8 处理
- 终端建议固定用 Git Bash 或 PowerShell 其一，避免混用

## 12. 阶段0 开工前置条件

1. GitHub 账号（没有先注册）
2. 大模型 API Key（届时演示申请 DeepSeek 或通义免费额度）
3. 本机装好：Python 3.11+、Node.js 20+、Docker Desktop（含 WSL2）、Git
