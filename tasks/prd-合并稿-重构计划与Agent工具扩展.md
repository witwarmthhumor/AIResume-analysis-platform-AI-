# PRD 合并稿：学生就业帮助系统重构 + AIResume Agent 工具再扩展

> 编制日期：2026-09-27 · 状态：**待评审**
> **这是什么**：把两份独立 PRD **装订成一份**（你要的「合在一起输出」）。两部分属于**两个不同项目、两个不同代码仓库**，实施时严禁交叉挪用。
> **权威性**：本文件是**阅读与交付用的合并稿**；单份权威仍是各自原文件——要改内容请改原文件后重新装订，避免版本漂移。

## 目录与归属

| 部分 | 主题 | 归属项目 / 仓库 | US 编号 | 源文件 |
|---|---|---|---|---|
| **Part 1** | 学生就业帮助系统重构（企业级架构 + 统一登录 + 用户体系） | 「学生就业帮助」系统（**源码不在本机**，独立仓库） | `US-001 ~ US-021` | `tasks/学生就业帮助系统重构/prd-学生就业帮助系统重构.md` |
| **Part 2** | Agent 工具再扩展（11 → 13 个 + 两处参数增强） | **AIResume**（本仓库） | `US-101 ~ US-107` | `tasks/prd-agent-tool-expansion-v2.md` |

**编号说明**：Part 2 的 `US-101 ~ US-107` 是本合并稿为避免与 Part 1 编号撞车而加的前缀（`1xx` = 原 `US-00x`）；在单份 PRD 与 `tasks/prd-agent-tool-expansion-v2.md` 中仍写作 `US-001 ~ US-007`。

**两部分的边界（务必区分）**

| 维度 | Part 1 · 重构 | Part 2 · 工具扩展 |
|---|---|---|
| 性质 | 结构性重构：不新增业务功能、管理端冻结、换技术栈 | 增量功能：给既有 Agent 加 2 个工具 + 2 个可选参数 |
| 前置阻塞 | **有**——旧系统源码与数据库导出未到位（US-001 硬阻塞） | **无**——可直接开工 |
| 改动面 | 后端全部 + 前端全部（重写为 Vue3 SPA） | 仅后端 Agent 工具层 + 评测集 + 文档，**零 UI 改动** |
| 验收口径 | 模块保真对照表 + 四关（ruff / alembic check / pytest / npm build） | 路由评测 top-1 不低于 33/33 + 归属隔离用例 |
| 数据库 | 全新 schema（3 个迁移） | **不需要新迁移**（只读现有表） |

---
---

# Part 1 · 学生就业帮助系统重构（企业级架构 + 统一登录 + 用户体系）

## PRD: 学生就业帮助系统重构（企业级架构 + 统一登录 + 用户体系）

> 编制日期：2026-09-27 · 状态：**待评审**
> **合并说明**：本文件是该校「学生就业帮助」系统重构的**单一权威文档**——已把原《重构实施规划》（现状反推 / 技术选型 / 分层设计 / 认证授权 / 目录结构 / 分期任务）与本 PRD 的 US 级验收**合并为一份**。原规划文档保留作历史稿：`tasks/学生就业帮助系统重构/重构实施规划.md`。
> ⚠️ **前置事实**：目标项目源码**不在本机**（已按文件名、目录名、页面内容三路全盘排查，零命中；`127.0.0.1:8000` 实际跑的是另一个项目 AIResume 的后端）。故第 3 章现状为**截图反推**，US-001 是硬阻塞项——**未完成 US-001 不得开工任何实现类故事**。

---

### 1. Introduction

现有系统是一个 Hogwarts 皮肤的多页静态站点：`注册登录.html`、`主界面.html` 等页面由后端在同一端口（8000）直接托管，登录只是"页面跳转惯例"而非安全边界；`admin/123456` 这类弱口令可直接使用；管理端（使用日志 / 数据看板 / 语料库管理）与用户端共用一套页面壳。

本 PRD 定义一次**保持业务不变**的结构性重构：

- **四大功能模块完整保留**：主界面、录音分析（三 Tab：录音转文本 / 角色审核 / 面试审核）、面试题生成、模拟面试（选简历 + 选题库 + 逐条问答）。
- **入口强制登录**：后端全局鉴权依赖 + 前端路由守卫双闸门；未认证一律 401 / 重定向登录；旧 `.html` 物理下线归档，不存在可直达的页面入口。
- **新增用户体系**：注册 / 登录 / 修改密码 / 登出，凭证以 argon2id 安全存储。
- **保留 admin/123456**：账号与口令仍可登录、管理端权限口径**原样冻结**，但口令改为哈希存储。
- 技术栈升级为 FastAPI（严格分层）+ Vue3 SPA + PostgreSQL + Alembic。

**交付边界**：只做本重构，不新增业务功能、不改管理端功能集合、不做手机号登录、不做移动端。

---

### 2. Goals

- 四大模块**功能零丢失**：逐条对照《模块迁移映射表》打勾，交互与文案与改造前一致。
- 未登录用户**无法访问任何业务数据**：所有 `/api/**` 默认要求认证，白名单仅 3 个；跨用户访问一律 404。
- 口令安全达标：全库无明文/弱哈希口令；`admin/123456` 以 argon2id 存储且可正常登录。
- 前端从"多页静态 HTML"升级为带路由守卫的 SPA，旧 `.html` 不可再直接访问。
- 引入 Alembic 迁移：数据库变更全部可追溯，`alembic check` 零差异。
- 四大模块各自完成"接口 → 页面对照 → 用例"闭环后再进入下一模块。
- 质量门禁落地：`ruff check` / `alembic check` / `pytest` / `npm run build` 四关全绿。

---

### 3. 现状反推与核对（合并自原规划 §1）

#### 3.1 从截图能确证的事实

| # | 截图证据 | 推断结论 | 重构影响 |
|---|---|---|---|
| 1 | URL 为 `127.0.0.1:8000/注册登录.html`、`/主界面.html` | 多页静态 HTML，页面名即路由；前后端同端口同源 | 无法做统一入口拦截 → 必须改 SPA 或加服务端守卫 |
| 2 | 侧边导航 5 项在每个页面重复：简历评估 / 录音分析 / 面试题生成 / 模拟面试 / 返校列车 | 无公共布局层，导航为各页复制的静态结构 | 收敛为单一 `AppLayout` + `SideNav` |
| 3 | 录音分析三 Tab + 提示「MP3/WAV/M4A、<500MB、≤60分钟」 | 一个上传入口 + 三种分析目的 | 用 `audio_jobs.purpose` 区分，不建三张表 |
| 4 | 面试题生成页：拖拽上传简历 + 「基于简历内容生成定制化面试题」+ 历史记录 | 输入=简历文件，输出=题库，有历史 | 需 `resumes` + `question_banks` |
| 5 | 模拟面试页：选简历 + 选题库（「127道题」）+ 确认加载 + 聊天区 + 历史/清空 | 会话由 (简历, 题库) 初始化，逐条落库 | `interview_sessions` + `interview_messages` |
| 6 | 管理端菜单：使用日志 / 数据看板 / 语料库管理 | 同一套壳按角色显示菜单 | 角色需在前端导航与后端鉴权两处判 |
| 7 | 登录页「登录/注册」双 Tab + 「切换到手机号登录」文案 | 已有注册入口；手机号登录疑似未实现 | 新体系覆盖用户名注册/登录/改密；摘掉误导文案 |
| 8 | 账号 `admin` + 6 位口令 | 极可能硬编码比较或明文/MD5 入库 | 一次性重哈希为 argon2id，删除旧比对分支 |

#### 3.2 推断的现状技术栈（待 US-001 核对）

| 层 | 推断 | 置信度 |
|---|---|---|
| 后端 | Flask（或同类 WSGI）单入口 + `static/` 托管 HTML | 中 |
| 视图 | 静态 HTML + 内联 `<script>`/`fetch`，无框架无构建链 | 高 |
| 存储 | SQLite 或 MySQL，表结构扁平 | 低 |
| 认证 | 前端存储或后端 session，**无统一中间件** | 中 |
| 口令 | 明文或弱哈希 | 高 |
| 授权 | 仅前端隐藏菜单，后端无角色校验 | 中 |

#### 3.3 五个结构性问题（重构要解决的）

1. **入口无强制校验**：`.html` 是物理文件，知道 URL 即可访问。
2. **口令存储不安全**：弱口令 + 明文/弱哈希，一次库泄露即全量失守。
3. **无分层**：路由里混 SQL、文件解析与业务判断。
4. **无授权校验**：管理端接口未鉴权，典型横向越权风险。
5. **无迁移机制**：改表靠手写 SQL，无法演进。

---

### 4. 技术框架选型（合并自原规划 §2）

选型原则：**与 owner 现有工程链路保持一致**（同一套 FastAPI + SQLAlchemy 2 + Alembic + Vue3 + ruff/pytest 约定），降低维护成本。

| 关注点 | 选型 | 版本约束 | 理由 |
|---|---|---|---|
| Web 框架 | FastAPI | `>=0.115` | 依赖注入做统一鉴权；Pydantic v2 校验；自带 OpenAPI |
| ORM / 迁移 | SQLAlchemy 2 + Alembic | `>=2.0.30` / `>=1.13` | 类型友好；禁手改表；`alembic check` 入 CI |
| 数据库 | PostgreSQL 16（Docker） | 16 | JSONB 存题库/审核结果；后续可加 pgvector |
| 驱动 | psycopg[binary] | `>=3.1` | 本地主机用 `127.0.0.1`（双栈会翻倍连接超时） |
| 配置 | pydantic-settings | `>=2.3` | `.env` → 配置对象；密钥不入库 |
| 认证 | PyJWT(HS256) + pwdlib[argon2] | `>=2.8` / `>=0.3` | 无状态 Token；argon2id 为当前推荐口令哈希 |
| 文件上传 | python-multipart | `>=0.0.9` | |
| 简历解析 | pypdf（PDF）/ python-docx（Word） | | |
| 后台任务 | Celery + Redis（长任务）/ BackgroundTasks（轻任务） | `celery[redis]>=5.4` | 转写、题库生成是分钟级任务 |
| 音频处理 | ffmpeg（容器内装） | | 统一 16k 单声道后再转写 |
| 代码质量 | ruff（check）+ pytest + httpx | `>=0.4` / `>=8.0` / `>=0.27` | 与既有项目同一把尺 |
| 前端 | Vue 3 + Vite 5 + vue-router 4 + Pinia | | 路由守卫做入口闸门；不引 axios |
| 样式 | 原生 CSS + CSS 变量（延续霍格沃茨皮肤） | | 零组件库，避免与自绘皮肤冲突 |

**明确不引入**：Django、微服务/K8s、消息中间件扩展、Element Plus/图表库、LangChain 1.x、刷新令牌（本期不做）。

---

### 5. 系统分层设计（合并自原规划 §3）

```
浏览器 (Vue3 SPA) → api/（薄）→ services/（厚）→ repositories/ → models/ → PostgreSQL
横切：core/{config, security, errors, logging, deps}、schemas/、worker/
```

**硬规则**

1. 路由层不写 SQL、不写业务分支（除"不存在就 404"的归属校验）。
2. 跨层只允许向下依赖；`services` 不许 import `api`。
3. 外部输入先过 Pydantic schema；ORM 实体不出现在响应体。
4. 事务边界在 service：一个业务用例一个事务。
5. 统一错误体 `{"code","message","details"}`，异常在 `core/errors.py` 转 HTTP 语义。
6. 新模型必须在 `models/__init__.py` 登记（漏登记会让 autogenerate 生成 `DROP TABLE`）。
7. 改模型后 `alembic check` 必须无差异。
8. 前端页面组件禁止直接 `fetch`，一律走 `api/`。

---

### 6. 认证与授权方案（合并自原规划 §4）

#### 6.1 用户模型

```sql
users(
  id BIGSERIAL PK, username VARCHAR(64) UNIQUE NOT NULL, email VARCHAR(255) UNIQUE,
  password_hash VARCHAR(255) NOT NULL,          -- argon2id 编码串（含参数与盐）
  role VARCHAR(16) NOT NULL DEFAULT 'user',     -- 'admin' | 'user'
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  token_version INT NOT NULL DEFAULT 0,         -- 改密后递增，旧 token 立即失效
  pwd_changed_at TIMESTAMPTZ, last_login_at TIMESTAMPTZ,
  created_at / updated_at TIMESTAMPTZ NOT NULL
)
```

#### 6.2 口令安全

argon2id（`pwdlib[argon2]`）；盐由算法内置生成，**不自造盐**；只存哈希，日志/异常/响应不得回显；**禁止**明文、MD5、SHA1、无盐 SHA256、可逆加密、`==` 直接比较；新注册与改密要求长度 ≥8 且含字母与数字（前端提示 + 后端再校验）。

#### 6.3 Token 与会话

| 项 | 方案 |
|---|---|
| 形态 | JWT(HS256)，**HttpOnly + SameSite=Lax Cookie**（生产 HTTPS 加 `Secure`） |
| Claims | `sub` / `role` / `iat` / `exp` / `ver`(token_version) |
| 有效期 | 24h（可配）；`jwt_secret_key` 仅存 `.env`，若为默认值则**拒绝启动** |
| 登出 | 清 Cookie；如需即时吊销把 `jti` 写 Redis 黑名单（配置开关，默认关） |
| 改密 | `token_version += 1` → 旧 Token 全端失效 |
| 前端存储 | **不落 `localStorage`**，Cookie 由浏览器自动携带 |

#### 6.4 入口强制登录：双闸门 + 一条白名单

- **闸门一（权威）后端全局鉴权依赖**：`/api/**` 默认要求认证，未过 → **401**；白名单仅 `POST /api/auth/register`、`POST /api/auth/login`、`GET /health`。用统一挂载前缀依赖实现，避免"漏写一个 Depends 就漏一个洞"。
- **闸门二（体验）前端 `router.beforeEach`**：无会话 → `redirect=/login?redirect=<原路径>`；`meta.requiresAdmin` 且非 admin → 403 页；`api/` 统一拦截 401 → 清状态跳登录。
- **物理隔离**：旧多页 `.html` 迁入 `legacy/` 归档，静态站点只暴露构建后的 SPA。

#### 6.5 授权

| 角色 | 能力 |
|---|---|
| `user` | 四大模块全部功能；仅能访问**自己的**简历/录音/题库/面试会话 |
| `admin` | 用户能力 + 使用日志 / 数据看板 / 语料库管理（**本次冻结，只保证改造后仍可用**） |

管理端接口统一 `require_admin`（→ 403）；归属校验统一收敛到 helper，登录按 `user_id`、匿名按 `user_id IS NULL AND anonymous_id = ?`；**跨用户一律 404**（不泄露存在性）。

#### 6.6 admin/123456 的保留策略

| 要求 | 落地方式 |
|---|---|
| admin/123456 仍可登录 | 迁移脚本一次性以 argon2id 写入 `password_hash`，`role='admin'` |
| 管理权限原状 | 管理端路由、菜单、接口集合与判定口径不改；只把鉴权入口收敛到 `require_admin` |
| 安全兜底 | 配置开关 `force_admin_pwd_change_on_first_login`（**默认关**，保原行为；文档标注上线前建议置 true） |
| 弱口令风险 | admin 登录写审计日志；连续失败按 `username+IP` 锁定（默认 5 次 / 15 分钟） |

---

### 7. 四大模块迁移映射（合并自原规划 §5）

| 模块 | 现状（截图） | 重构后前端 | 重构后接口 | 涉及表 | 保真要点 |
|---|---|---|---|---|---|
| 主界面 | 左侧 5 项导航 + 顶部标题 + 右上头像与历史记录 | `AppLayout` + `NavSideBar` + `HomeView`（按角色渲染菜单） | `GET /api/auth/me`、`GET /api/home/summary` | users | 导航命名与顺序不变；按角色显隐；头像与用户名保留 |
| 录音分析 | 三 Tab + 大拖拽上传 + 格式/大小/时长提示 + 历史 | `AudioAnalysisView` + `UploadCard` + Tabs + `HistoryDrawer` | `POST/GET /api/audio/jobs`、`GET /api/audio/jobs/{id}`、`POST /api/audio/jobs/{id}/retry` | `audio_jobs` | 三 Tab 与提示文案一致；结果为异步长任务且进度可见；历史可回溯 |
| 面试题生成 | 拖拽上传简历（PDF/Word <10MB）+ 生成提示 + 历史 | `QuestionGenView` + 进度 + 题库详情 | `POST/GET /api/question-banks`、`GET /api/question-banks/{id}` | `resumes`、`question_banks` | 上传约束与文案一致；结果按题目列表展示；历史可再打开 |
| 模拟面试 | 选简历 + 选题库（含题数）+ 确认加载 + 聊天 + 历史/清空 | `MockInterviewView` + `ChatPanel` + `HistoryDrawer` | `POST /api/interviews`、`POST /api/interviews/{id}/messages`、`GET/DELETE /api/interviews`、`GET /api/interviews/{id}` | `interview_sessions`、`interview_messages` | "选完两者才能加载"不变；消息逐条落库（刷新可恢复）；题库下拉显示题数 |
| （待确认）返校列车 | 侧栏第 5 项 | 占位页（默认处理） | — | — | 默认保留为"建设中"占位，不擅自删除 |

共用支撑：`resumes`、`usage_logs`（每日限额）、`audit_logs`（管理端使用日志数据源）。

---

### 8. 目录结构（合并自原规划 §6，精简版）

```
job-help-system/
├── docker-compose.yml          # postgres + redis（具名卷）
├── .env.example / .gitignore / .pre-commit-config.yaml
├── .github/workflows/ci.yml    # ruff → alembic check → pytest → npm build
├── legacy/                     # 旧多页 HTML 归档（不参与构建）
├── docs/                       # 本 PRD、现状核对表、接口清单、数据库设计
├── backend/
│   ├── alembic/versions/{0001_init_users,0002_init_business,0003_admin_rehash}.py
│   ├── scripts/{check_env.py, seed_admin.py}
│   ├── tests/{conftest.py,test_auth.py,test_authz.py,test_audio_jobs.py,test_question_banks.py,test_interviews.py}
│   └── app/
│       ├── main.py
│       ├── core/{config,security,errors,logging,router_registry}.py
│       ├── db/session.py
│       ├── api/{deps,auth,home,audio,question_banks,interviews}.py + api/admin/{admin,dashboard,kb}.py
│       ├── schemas/{auth,audio,question_bank,interview,common}.py
│       ├── models/{__init__,base,user,resume,audio_job,question_bank,interview,usage_log,audit_log}.py
│       ├── services/{auth_service,resume_service,audio_service,question_service,interview_service,ai_client,usage_service}.py
│       └── worker/{celery_app.py, tasks/{audio_transcribe,gen_questions}.py}
└── frontend/
    ├── vite.config.ts          # /api 代理到 127.0.0.1:8000
    └── src/
        ├── router/index.js     # 路由表 + beforeEach 守卫
        ├── stores/auth.js      # Pinia
        ├── api/{http,auth,audio,questionBanks,interviews,admin}.js
        ├── layouts/AppLayout.vue
        ├── components/{NavSideBar,UploadCard,HistoryDrawer,ChatPanel,ProgressBar,EmptyState}.vue
        ├── views/{LoginView,HomeView,AudioAnalysisView,QuestionGenView,MockInterviewView,NotFoundView,ForbiddenView}.vue
        ├── views/admin/{UsageLogView,DashboardView,KbAdminView}.vue
        └── assets/theme.css    # Hogwarts 皮肤：深木 #2b1a12 / 米金 #f7ecd2 / 金 #d9b45a
```

---

### 9. User Stories

> 顺序即执行顺序（依赖优先）。UI 类故事必须做浏览器验收；纯后端/文档类故事的收束条件是 `ruff check .` 与 `pytest`。

#### US-001: 现状核对与视觉基线存档（**硬阻塞**）
**Description:** 作为重构实施者，我需要先把旧系统的真实结构记录下来，这样后续迁移才有据可依、不会凭截图猜测。

**Acceptance Criteria:**
- [ ] 产出 `docs/现状核对表.md`，逐条填写：后端框架与版本、启动方式与端口、静态目录挂载方式、数据库类型与全部表结构
- [ ] 记录**口令存储实证**：查 admin 行，判定明文 / MD5 / hash 并截图或导出片段
- [ ] 导出全部接口清单（URL / 方法 / 入参 / 出参 / 是否鉴权）
- [ ] 记录 admin 权限实际控制的功能范围（界定"冻结边界"）
- [ ] 产出 `legacy/` 视觉存档：登录页、主界面、录音分析、面试题生成、模拟面试、管理端三页的页面文件与皮肤 CSS
- [ ] 确认「返校列车」是否真实业务（是则补功能说明）
- [ ] 记录录音分析实际调用的转写能力（本地模型 / 云 API / SDK）
- [ ] `ruff check .` 通过

#### US-002: 仓库骨架与环境自检
**Description:** 作为开发者，我需要一个能启动的空壳工程与一条自检命令，这样环境问题在写业务前就暴露。

**Acceptance Criteria:**
- [ ] 建出第 8 章目录骨架；`docker-compose.yml` 起 postgres + redis（具名卷）
- [ ] `.env.example` 含数据库/密钥/Redis 变量；`.gitignore` 忽略 `.env`
- [ ] `core/config.py`（pydantic-settings）读 `.env`；`jwt_secret_key` 为默认值时启动失败
- [ ] `core/errors.py` 输出统一错误体 `{code,message,details}`；`core/logging.py` 可用
- [ ] `GET /health` 返回服务与数据库连接状态
- [ ] `scripts/check_env.py` 一条命令输出容器/迁移/端口/DB 状态
- [ ] `ruff check .` 通过

#### US-003: 数据层与初始迁移
**Description:** 作为开发者，我需要全部业务表通过迁移一次建齐，这样 schema 可演进、可复现。

**Acceptance Criteria:**
- [ ] 建出 users / resumes / audio_jobs / question_banks / interview_sessions / interview_messages / usage_logs / audit_logs 模型
- [ ] 所有模型在 `models/__init__.py` 登记
- [ ] 迁移 `0001_init_users.py` + `0002_init_business.py` 可从零 `alembic upgrade head`
- [ ] 索引与唯一约束按预期生成（username/email 唯一、外键索引）
- [ ] `alembic check` 无差异
- [ ] `ruff check .` 通过

#### US-004: 口令与令牌安全模块
**Description:** 作为开发者，我需要一套统一的口令哈希与 JWT 工具，这样任何地方都不会各写一套。

**Acceptance Criteria:**
- [ ] `core/security.py` 提供 `hash_password` / `verify_password`（argon2id，`pwdlib`）
- [ ] 提供 `create_access_token(user_id, role, token_version)` 与 `decode_access_token`
- [ ] 同口令两次哈希结果不同（盐生效），且都能校验通过
- [ ] 篡改过的 Token 解码返回 `None`，过期 Token 解码返回 `None`
- [ ] `ruff check .` 且新增 `pytest` 用例通过

#### US-005: 注册接口
**Description:** 作为新用户，我想注册账号，这样我才能登录并使用系统。

**Acceptance Criteria:**
- [ ] `POST /api/auth/register` 接受用户名 + 口令（+ 可选邮箱），返回用户基本信息（**不含** `password_hash`）
- [ ] 口令长度 <8 或缺字母/数字时返回 400，并说明规则
- [ ] 用户名重复返回 409；用户名（`admin`/`root` 等保留字）返回 400
- [ ] 落库的 `password_hash` 以 `$argon2id$` 开头
- [ ] 注册接口按 IP 限流（超限 429）
- [ ] 测试造数用专属用户名前缀并在用例后范围化清理（禁全表 DELETE）
- [ ] `ruff check .` 且 `pytest` 通过

#### US-006: 登录接口与失败锁定
**Description:** 作为用户，我想用账号口令登录，这样我能拿到访问凭证。

**Acceptance Criteria:**
- [ ] `POST /api/auth/login` 校验通过后下发 **HttpOnly Cookie**（`SameSite=Lax`），响应体返回用户信息与角色
- [ ] 口令错误返回 401，文案统一为「用户名或密码错误」（不泄露用户是否存在）
- [ ] 同一 `username+IP` 连续失败 5 次后锁定 15 分钟，期间返回 429/423 并带剩余时间
- [ ] 登录成功写 `last_login_at`，并写一条审计日志
- [ ] 错误口令不会在日志或响应中出现
- [ ] `ruff check .` 且 `pytest` 通过

#### US-007: 登出、当前用户与修改密码
**Description:** 作为用户，我想退出登录并在需要时改密码，这样我能保护账号。

**Acceptance Criteria:**
- [ ] `POST /api/auth/logout` 清 Cookie 返回 204
- [ ] `GET /api/auth/me` 返回当前用户信息与角色；未登录 401
- [ ] `POST /api/auth/change-password` 需原密码 + 新密码 + 二次确认；原密码错返回 400
- [ ] 新密码不满足强度返回 400
- [ ] 改密成功后 `token_version` 递增，**用改密前的 Cookie 调 `/api/auth/me` 返回 401**
- [ ] 改密成功后 `pwd_changed_at` 更新
- [ ] `ruff check .` 且 `pytest` 通过（含"旧 token 失效"用例）

#### US-008: admin 账号保留与口令重哈希
**Description:** 作为管理员，我需要改造后仍能用 admin/123456 登录并保住原有管理权限。

**Acceptance Criteria:**
- [ ] 迁移 `0003_admin_rehash.py`：把 admin 口令以 argon2id 写入 `password_hash`（脚本只读旧值、不回显）
- [ ] `scripts/seed_admin.py` 幂等：重复执行不重复建号、不覆盖已改过的口令
- [ ] admin 行 `role='admin'`，用 `admin/123456` 登录成功
- [ ] 全库 `grep` 式校验：`users` 表中不存在明文/32 位 MD5 形态的口令
- [ ] 旧口径的登录比较分支代码被删除（无残留硬编码 `123456`）
- [ ] `ruff check .` 且 `pytest` 通过

#### US-009: 鉴权闸门与授权依赖
**Description:** 作为系统，我需要在入口处统一拦截未授权请求，这样任何新接口都不会漏鉴权。

**Acceptance Criteria:**
- [ ] 所有 `/api/**` 路由默认挂全局认证依赖；白名单仅 `register` / `login` / `health`
- [ ] 未携带凭证访问任意业务接口返回 **401**
- [ ] `require_admin` 依赖存在：非 admin 访问管理端接口返回 **403**
- [ ] 归属校验 helper 统一实现（登录按 `user_id`、匿名要求 `user_id IS NULL AND anonymous_id = ?`）
- [ ] 归属 helper **不允许**退化为"只判 `user_id IS NULL`"（用例覆盖）
- [ ] 跨用户访问他人资源返回 **404**（不是 403）
- [ ] `ruff check .` 且 `pytest` 通过（覆盖未登录/非 admin/跨用户三类）

#### US-010: 前端骨架、路由守卫与 401 拦截
**Description:** 作为用户，我未登录时不该看到任何业务页面，这样登录就是真正的入口。

**Acceptance Criteria:**
- [ ] Vite + Vue3 + vue-router + Pinia 骨架可 `npm run dev`
- [ ] `router/index.js` 中所有业务路由带 `meta.requiresAuth`；`beforeEach` 未登录跳 `/login?redirect=<原路径>`
- [ ] 未登录直接访问 `/`、`/audio`、`/questions`、`/interview`、`/admin/*` 均被重定向到登录页
- [ ] `api/http.js` 统一解析错误体，并对 `401` 清状态 + 跳登录
- [ ] `meta.requiresAdmin` 路由对普通用户显示 403 页
- [ ] `npm run build` 成功
- [ ] 浏览器验收：未登录访问受限路由被重定向（可用 agent-browser 或 cdp-ui-verify）

#### US-011: 登录 / 注册 / 修改密码页
**Description:** 作为用户，我想在一个页面完成注册、登录与改密，这样入口清晰。

**Acceptance Criteria:**
- [ ] `LoginView.vue` 含登录 / 注册 / 修改密码三个 Tab
- [ ] 表单含前端校验（必填、口令强度、二次确认一致）并在提交前拦截
- [ ] 登录成功按 `redirect` 参数跳回原目标页，无 `redirect` 时进主界面
- [ ] 错误提示使用后端统一错误体的 `message`（不暴露内部细节）
- [ ] 摘掉"切换到手机号登录"等未实现文案
- [ ] `npm run build` 成功
- [ ] 浏览器验收：注册 → 登录 → 改密 → 用新口令登录 全链路可用

#### US-012: 主界面（布局 + 导航 + 首页）
**Description:** 作为用户，我登录后要看到与改造前一致的工作台与导航。

**Acceptance Criteria:**
- [ ] `AppLayout` + `NavSideBar` 成为唯一壳；导航 5 项命名与顺序与截图一致
- [ ] `user` 角色不显示管理端菜单；`admin` 角色显示管理端 3 项
- [ ] 右上角显示用户名与头像；点击可登出
- [ ] `HomeView` 展示与改造前一致的功能入口
- [ ] 皮肤沿用 Hogwarts 配色（深木 / 米金 / 金），标题使用衬线字体
- [ ] `npm run build` 成功
- [ ] 浏览器验收：对照截图逐项核对导航与顶栏

#### US-013: 录音分析后端
**Description:** 作为用户，我想上传录音并按三种目的分析，这样我能拿到转写与审核结果。

**Acceptance Criteria:**
- [ ] `POST /api/audio/jobs` 接受文件 + `purpose`（`transcribe` / `role_review` / `interview_review`），落盘后建任务返回 job id
- [ ] 拒绝非 MP3/WAV/M4A、>500MB、超 60 分钟的输入，返回明确错误体
- [ ] 文件名随机化并存储在 web 根之外；下载/读取走鉴权接口
- [ ] 长任务经 Celery 执行，`GET /api/audio/jobs/{id}` 返回状态与进度
- [ ] 三种 `purpose` 各自产出结构化结果字段
- [ ] 失败可 `POST /api/audio/jobs/{id}/retry`
- [ ] 他人 job 返回 404
- [ ] `ruff check .` 且 `pytest` 通过

#### US-014: 录音分析前端
**Description:** 作为用户，我想在页面里上传并看到三种分析结果，这样我不用命令行操作。

**Acceptance Criteria:**
- [ ] `AudioAnalysisView.vue` 含三 Tab：录音转文本 / 角色审核 / 面试审核
- [ ] 上传区支持拖拽，展示与截图一致的提示文案（格式/大小/时长）
- [ ] 上传后展示进度，完成后分 Tab 渲染对应结果
- [ ] 历史记录抽屉可打开历史任务并回看结果
- [ ] 失败态显示错误与重试按钮
- [ ] `npm run build` 成功
- [ ] 浏览器验收：三种 purpose 各跑通一次并回看历史

#### US-015: 面试题生成后端
**Description:** 作为用户，我想用简历生成定制面试题，这样我能针对自己经历刷题。

**Acceptance Criteria:**
- [ ] `POST /api/question-banks` 接受简历文件（PDF/Word，<10MB），解析后生成题库
- [ ] 拒绝不支持格式与超限文件，返回明确错误体
- [ ] 生成结果落 `question_banks.questions`（JSONB），含题目分组与总数
- [ ] `GET /api/question-banks` 列历史（标题、题数、时间），`GET /api/question-banks/{id}` 取详情
- [ ] 他人题库返回 404
- [ ] 生成过程为异步任务，状态可轮询
- [ ] `ruff check .` 且 `pytest` 通过

#### US-016: 面试题生成前端
**Description:** 作为用户，我想在页面里上传简历并查看生成的题库。

**Acceptance Criteria:**
- [ ] `QuestionGenView.vue` 含拖拽上传区与「基于简历内容生成定制化面试题」提示
- [ ] 生成中显示进度，完成后按题目列表分组展示
- [ ] 历史记录可打开过往题库详情
- [ ] 上传约束（格式/大小）与截图文案一致
- [ ] `npm run build` 成功
- [ ] 浏览器验收：上传 → 生成 → 打开历史 全链路可用

#### US-017: 模拟面试后端
**Description:** 作为用户，我想基于某份简历和某个题库开始一场模拟面试并与 AI 逐条对话。

**Acceptance Criteria:**
- [ ] `POST /api/interviews` 接受 `resume_id` + `bank_id`，创建会话（校验两者归属，非法返回 404/400）
- [ ] `POST /api/interviews/{id}/messages` 接收用户回答并返回 AI 追问，逐条落 `interview_messages`
- [ ] `GET /api/interviews` 列历史（时间、简历、题库、状态、轮次）
- [ ] `GET /api/interviews/{id}` 返回会话消息（刷新可恢复）
- [ ] `DELETE /api/interviews/{id}` 清空会话内容
- [ ] 他人会话返回 404
- [ ] `ruff check .` 且 `pytest` 通过

#### US-018: 模拟面试前端
**Description:** 作为用户，我想选简历与题库后开始对话，并能清空或翻看历史。

**Acceptance Criteria:**
- [ ] `MockInterviewView.vue` 两个下拉（简历 / 题库，题库显示题数）+「确认加载」按钮
- [ ] 未选齐两项时按钮不可用（与改造前交互一致）
- [ ] 加载后进入聊天区，支持发送、逐条追加消息、流式或轮询刷新
- [ ] 「清空聊天」与「历史记录」两个动作都在且生效
- [ ] 刷新页面后可恢复当前会话
- [ ] `npm run build` 成功
- [ ] 浏览器验收：完整走一场对话 + 清空 + 打开历史

#### US-019: 管理端冻结校验
**Description:** 作为管理员，我需要改造后三个管理功能与改造前行为一致，且普通用户无法访问。

**Acceptance Criteria:**
- [ ] 使用日志 / 数据看板 / 语料库管理三页在新架构下可用，字段与交互与改造前一致
- [ ] 普通用户访问管理端页面被前端拦截且有后端 403 兜底
- [ ] 管理端接口全部经 `require_admin`
- [ ] 本次不新增、不删除、不改名任何管理端功能
- [ ] `npm run build` 成功
- [ ] 浏览器验收：admin 账号逐页核对三功能；普通账号访问被拒

#### US-020: 安全加固收口
**Description:** 作为 owner，我需要把已知风险逐条堵上，这样重构不会留下新的漏洞面。

**Acceptance Criteria:**
- [ ] 上传校验扩展名 + MIME + 文件头；大小限制在服务端强制执行
- [ ] 上传文件名随机化；存储目录不在 web 根内；读取走鉴权接口（防路径穿越）
- [ ] 写操作校验 `Origin`；Cookie 具备 `HttpOnly` + `SameSite=Lax`
- [ ] 登录/注册限流生效；日志对口令、Token、邮箱打码
- [ ] 越权用例覆盖全部资源类型（简历/录音/题库/面试会话/管理端）
- [ ] 禁用 `v-html` 或做白名单过滤（XSS 面）
- [ ] `ruff check .` 且 `pytest` 通过

#### US-021: 回归、交付与文档收口
**Description:** 作为 owner，我需要在交付前确认功能零丢失、四关全绿。

**Acceptance Criteria:**
- [ ] 模块迁移映射表逐条打勾，产出验收报告（含截图对照）
- [ ] `ruff check .` / `alembic check` / `pytest` / `npm run build` 四关全绿
- [ ] Docker 化交付：`docker compose up` 可起前后端 + 依赖；README 含启动步骤
- [ ] `docs/` 含数据库设计、接口清单、现状核对表
- [ ] 旧 `.html` 已归档至 `legacy/` 且不被静态托管
- [ ] 环境自检命令在 README 中说明

---

### 10. Functional Requirements

- **FR-1**：系统必须保留四大模块（主界面、录音分析、面试题生成、模拟面试）的全部原有功能与交互路径。
- **FR-2**：所有 `/api/**` 接口默认要求认证，未认证返回 401；白名单仅 `POST /api/auth/register`、`POST /api/auth/login`、`GET /health`。
- **FR-3**：前端所有业务路由必须经路由守卫；未登录访问被重定向到登录页并保留原目标路径。
- **FR-4**：旧多页 HTML 必须移出静态托管目录（迁 `legacy/`），不得存在可绕过登录的页面入口。
- **FR-5**：系统必须支持用户名注册，口令规则为长度 ≥8 且含字母与数字。
- **FR-6**：系统必须支持登录，凭证以 JWT 存于 HttpOnly + SameSite=Lax Cookie。
- **FR-7**：系统必须支持修改密码，改密后旧凭证立即失效（`token_version` 递增）。
- **FR-8**：口令必须用 argon2id 存储；禁止明文、MD5、SHA1、无盐哈希与可逆加密。
- **FR-9**：admin（用户名 `admin`，口令 `123456`）必须仍可登录，`role='admin'`，管理端功能与权限口径不变。
- **FR-10**：登录失败必须按 `username+IP` 计数并在阈值后锁定；登录失败文案统一，不泄露用户是否存在。
- **FR-11**：资源归属校验必须统一实现；跨用户访问返回 404。
- **FR-12**：管理端接口必须要求 admin 角色，否则 403。
- **FR-13**：录音分析必须支持三种分析目的（转写 / 角色审核 / 面试审核），且为异步长任务、状态可查。
- **FR-14**：面试题生成必须以简历文件为输入、以题库为输出，并保留历史记录。
- **FR-15**：模拟面试必须以 (简历, 题库) 初始化会话，消息逐条落库、刷新可恢复、支持清空。
- **FR-16**：数据库结构变更必须通过 Alembic 迁移，且 `alembic check` 无差异。
- **FR-17**：文件上传必须校验类型与大小，文件名随机化，存储位置不在 web 根内。
- **FR-18**：日志必须脱敏（口令、Token、邮箱）。
- **FR-19**：前后端错误响应必须统一为 `{code,message,details}` 结构。
- **FR-20**：`ruff check` / `alembic check` / `pytest` / `npm run build` 必须作为交付门槛全部通过。

---

### 11. Non-Goals (Out of Scope)

- 不新增任何业务功能；「返校列车」默认只做占位页（除非 US-001 确认它是真实业务）。
- 不改动管理端的功能集合、菜单结构与权限判定口径（冻结）。
- 不做手机号登录、短信验证、第三方 OAuth、找回密码邮件流。
- 不做多租户、计费、配额、组织/团队概念。
- 不做移动端 App、不做国际化、不做主题切换。
- 不做微服务拆分、消息队列扩展、K8s 部署。
- 不做旧业务数据的历史清洗（只做必要迁移：用户口令重哈希）。
- 不做刷新令牌 / 单点登录 / 会话管理后台。
- 不做 UI 视觉重设计（皮肤沿用现状）。

---

### 12. Design Considerations

- **视觉基准**：以 US-001 存档的 `legacy/` 页面与皮肤 CSS 为准，重构后逐页对照，颜色/间距/文案尽量一致。
- **皮肤令牌化**：把 Hogwarts 配色收敛为 CSS 变量（深木 `#2b1a12`、米金 `#f7ecd2`、金 `#d9b45a`），避免散落硬编码。
- **组件复用**：`UploadCard`、`HistoryDrawer`、`ChatPanel`、`ProgressBar`、`EmptyState` 在多个模块复用，避免各模块各写一套。
- **交互一致性**：异步任务统一呈现"提交 → 进度 → 结果 / 失败重试"三态。
- **文案一致性**：上传约束、提示语与改造前逐字对照（截图即验收标准）。
- **可访问性底线**：表单控件带 label，错误提示与字段关联。

---

### 13. Technical Considerations

- **源码可得性是最大前置风险**：US-001 未完成前不得进入实现；若无法取得旧系统访问权，需要 owner 提供数据库导出与接口清单，否则应改为"按需求重建"而非"等价迁移"。
- **口令迁移窗口**：迁移脚本只读旧值、不回显；迁移后立即校验 admin 可登录。
- **JWT 密钥**：`.env` 中的 `jwt_secret_key` 为默认值时拒绝启动，避免上线用弱密钥。
- **本地地址**：`DATABASE_URL` 主机用 `127.0.0.1` 而非 `localhost`（双栈会翻倍连接超时）。
- **CORS**：前端走 Vite 代理，同源，**不配 CORS**。
- **长任务**：转写与题库生成走 Celery；前端轮询/SSE 二选一，建议轮询（简单、可恢复）。
- **测试隔离**：测试不得依赖本地 `.env`（配置用 monkeypatch 注入假值）；造数用专属前缀 + 范围化清理，**禁止全表 DELETE**。
- **CI**：`ruff` → `alembic check` → `pytest` → `npm build`；`npm build` 必须过，防打包期错误漏到线上。
- **格式化**：以 `ruff check` 为门槛；若采用 `ruff format`，须一次性全仓执行并单独提交，避免与业务改动混在一起。

---

### 14. Success Metrics

- 模块保真：四大模块对照表 100% 打勾，无功能缺失（以截图与 `legacy/` 存档逐项核对）。
- 认证有效：未登录访问任何业务接口 100% 返回 401（自动化用例覆盖全部路由前缀）。
- 授权有效：跨用户访问 100% 返回 404；普通用户访问管理端 100% 返回 403。
- 口令安全：`users` 表中 0 条明文/弱哈希口令；admin/123456 可登录。
- 交付质量：四关全绿；`alembic check` 零差异。
- 可维护性：新增一个业务模块只需"schema + service + router + view + api 文件"五步，无需改动横切逻辑。

---

### 15. 风险与对策（合并自原规划 §9）

| 风险 | 影响 | 对策 |
|---|---|---|
| 源码缺失导致反推偏差 | 迁移可能丢功能 | US-001 设为硬阻塞；未核对项不进入实现 |
| 旧库口令为明文/弱哈希 | 迁移期安全窗口 | 一次性重哈希；迁移前后行数比对；脚本不回显旧值 |
| 前端全量重写工作量大 | 工期拉长 | 按模块灰度迁移；`legacy/` 作视觉基准；每模块独立验收 |
| 录音/题库生成是长任务 | 请求超时、体验差 | 上传即建任务 + 轮询进度；Celery 独立队列 |
| 大文件上传被网关拦截 | 功能不可用 | 预留分片；Nginx `client_max_body_size` 与超时对齐 |
| 管理端"冻结"被改坏 | 违反要求 4 | US-019 专项校验；管理端改动须在提交说明中单列 |
| admin 弱口令被扫 | 安全事件 | 失败锁定 + 审计日志 + 可配置首登强制改密 |
| 转写引擎未定 | 无法估时 | US-001 记录现状能力；若无旧能力，需先做选型调研 |

---

### 16. Open Questions

1. **「返校列车」是什么？** 侧栏第 5 项，不属于四大模块。默认按"占位"处理；若为真实业务请补功能说明。
2. **数据库是否允许用 PostgreSQL？** 默认 PG 16（Docker）。若答辩/课程环境强制 MySQL 或 SQLite，需替换驱动与迁移细节（JSONB → JSON 等），请在 US-001 前确认。
3. **admin 是否要"首登强制改密"？** 默认关闭（保持要求 4 的原有行为）；开启可显著降低弱口令风险，但改变 admin 使用体验。
4. **旧业务数据要不要迁移？** 当前只规划"口令重哈希"。若旧库有简历/题库/面试数据需保留，需要单独的迁移故事（含字段映射与清洗规则）。
5. **录音转写用哪个引擎？** 现状能力未知（US-001 查证）。若需引入新服务，属于外部依赖，需明确账号与费用归属。
6. **`repositories/` 层是否保留？** 多人协作值得；单人维护可能偏重，可合并进 service（需 owner 拍板）。
7. **浏览器验收由谁执行？** UI 故事要求浏览器验收，需确认由 owner 人工确认还是用 agent-browser / cdp-ui-verify 自动化。

---
---

# Part 2 · AIResume Agent 工具再扩展（v3.10：11 → 13 个工具 + 两处参数增强）

> 归属：**AIResume 仓库**（本仓库）· 对应规划：`docs/后续开发规划.md` §4 · v3.10
> 本部分原稿：`tasks/prd-agent-tool-expansion-v2.md`（编号 `US-001 ~ US-007`，本稿写作 `US-101 ~ US-107`）

## PRD: Agent 工具再扩展（11 → 13 个 + 两处参数增强）

> 编制日期：2026-09-27 · 状态：**待评审**
> 关联规划：[docs/后续开发规划.md](../docs/后续开发规划.md) §4 · v3.10（本 PRD 是该批次的执行规格）
> 前序 PRD：`tasks/prd-agent-tool-expansion.md`（4 → 11 个，已完成并封版于 v3.5/v3.6）
> 关联设计文档：[docs/Agent工具设计.md](../docs/Agent工具设计.md)（§二 工具对照、§四 描述互斥规范、§六 工具内 LLM 限额）

---

### 1. Introduction

平台的 AI 客服（Agent）目前有 **11 个工具**，但它们全部只查**业务数据**——简历、面试结果与评分、用量、知识库。谁都无法回答两类最自然的用户问法：

1. **「我之前问过你什么来着？」**——现有没有任何工具能检索历史对话，用户想找旧对话只能手动翻 UI。
2. **「上次面试官问了什么？我怎么答的？」**——现有 `interview_history` 只给元数据与评分，**对话原文拿不到**。

本批次补上这两块记忆盲区，新增 2 个工具（`conversation_search`、`interview_transcript`，**均为纯查库、零 LLM 调用**），并给 2 个现有工具加可选参数（`kb_search.document`、`usage_stats.action`）。参数增强不动工具数量，比新增更便宜。

**交付边界**：只改后端 Agent 工具层与其文档/评测基线，**不动前端**（工具返回的文本直接走现有聊天流展示）。

---

### 2. Goals

- 新增 `conversation_search`：能按关键词在**当前用户自己的**历史会话里定位命中片段，并标注来源（在线对话 / AI 客服）。
- 新增 `interview_transcript`：能取回**当前用户自己的**某场（或最近一场）模拟面试的**一问一答原文**。
- `kb_search` 支持限定在某篇文档内检索；`usage_stats` 支持按动作类型过滤。
- 两项新增工具**不产生任何 LLM 调用**，不占用 `daily_agent_tool_llm_limit`，不改动现有记账口径。
- 工具数从 11 → 13，**路由评测基线同步扩容**（新增 ≥6 题），top-1 准确率不低于现有 33/33 口径。
- 归属隔离零缺陷：跨用户、跨匿名身份一律查不到（含"不泄露资源是否存在"的文案要求）。

---

### 3. User Stories

#### US-101: 新增 `conversation_search` 工具

**Description:** 作为使用 AI 客服的用户，我想让它帮我找回「之前聊过的内容」，这样我不用手动翻历史会话列表。

**Acceptance Criteria:**
- [ ] `tools.py` 的 `make_tools()` 内新增 `@tool def conversation_search(keyword: str, limit: int = 5) -> str`
- [ ] 查询为 `ChatMessage JOIN ChatSession`：`content ILIKE %keyword%`，且**排除已删除会话**（`ChatSession.deleted_at IS NULL`）
- [ ] 归属过滤复用 `_owner_filter(ChatSession, user_id, anonymous_id)`；**登录按 `user_id`、匿名要求 `user_id IS NULL AND anonymous_id = ?`**；`_owner_filter` 返回 `None` 时返回「无法识别用户身份，请先登录」文案（与 `resume_lookup` 口径一致）
- [ ] `keyword` 为空字符串时，退化为「列出最近 N 个会话的标题与时间」（与 `resume_lookup` 空 query 行为一致）
- [ ] `session_type` 的 **两类都搜**（`chat` 在线对话 / `agent` AI 客服），但每行**必须标注来源**，如 `【来源：AI 客服】`
- [ ] 每行包含：命中片段（用 `_snippet()`，宽度沿用 `_TOOL_SNIPPET_WIDTH=80`）、会话标题、会话时间
- [ ] `limit` 钳制在 1~5（新增常量 `_TOOL_CONV_LIMIT = 5`，与 `_TOOL_LIST_LIMIT` 同口径）
- [ ] **绝不回灌** `ChatMessage.citations` / `tool_steps`（避免把工具过程再塞回上下文）
- [ ] 无命中时的文案明确告知"没有搜到"，并提示可换关键词（不得编造历史）
- [ ] docstring 按三段式（做什么 → 什么时候用 → 什么时候不用）书写，与 `kb_search` / `platform_help` 互相点名（问"平台功能怎么用"用 `platform_help`；问知识库清单用 `kb_list`）
- [ ] 单测：命中 / 空关键词列清单 / 无命中 / **跨用户隔离**（他人会话关键词 → 查不到且不泄露存在性）/ **跨匿名隔离** / 超长 limit 钳制，共 ≥6 例
- [ ] 测试造数用专属前缀（如 `_CONV_PREFIX = "agentconv_"`），**禁止全表 DELETE**
- [ ] `ruff check .` 通过（**只跑 check**，不要顺手 `ruff format` 整文件——会造出与本 story 无关的大量 diff）

#### US-102: 新增 `interview_transcript` 工具

**Description:** 作为使用模拟面试的用户，我想让 AI 客服复述我上次面试的**问答原文**，这样我能复盘自己当时怎么答的。

**Acceptance Criteria:**
- [ ] `tools.py` 的 `make_tools()` 内新增 `@tool def interview_transcript(session_id: int | None = None, limit: int = 10) -> str`
- [ ] `session_id` 省略时取**最近一场**面试（按 `created_at` 倒序、忽略 `id` 相同者），并在输出头部说明取的是哪一场
- [ ] 归属过滤复用 `_owner_filter(InterviewSession, user_id, anonymous_id)`；无法识别身份时返回引导登录文案
- [ ] **`session_id` 必须归属校验**：指定的场次不属于本人（或不存在）时，返回自然语言「没找到这场面试」，**不返回任何消息内容**，且文案不区分"不存在"与"不属于你"
- [ ] 输出头部含：场次时间、岗位类型、进行状态、已进行轮次
- [ ] 输出正文为该场 `InterviewMessage` 的逐条问答原文，按时间正序，取最近 `limit` 条（`limit` 钳制 2~20，新增常量 `_TOOL_TRANSCRIPT_LIMIT = 20`）
- [ ] 整段输出超过 `_TOOL_TRANSCRIPT_CHARS = 2000` 字符时截断，**并在文案里显式说明已截断**（如"（超出 2000 字符，仅显示前 2000 字符）"）
- [ ] docstring 与 `interview_history` **互相点名**：`history` 管「结果」（多少分、评价说了什么），`transcript` 管「过程」（问了什么、怎么答的）
- [ ] 单测：默认取最近一场 / 指定 `session_id` / 他人会话无内容 / 不存在的 `session_id` / 超长截断告知 / 空场次 / `limit` 钳制，共 ≥6 例
- [ ] 测试造数用专属前缀，禁止全表 DELETE
- [ ] `ruff check .` 通过（**只跑 check**，不要顺手 `ruff format` 整文件——会造出与本 story 无关的大量 diff）

#### US-103: `kb_search` 支持限定文档检索

**Description:** 作为使用知识库问答的用户，我想把检索范围限定在某篇文档内，这样我能在长文档里精确找答案。

**Acceptance Criteria:**
- [ ] `kb_search` 签名改为 `kb_search(query: str, document: str = "")`——**参数可省，默认行为与现在完全一致**
- [ ] `document` 非空时按**文档标题模糊匹配**（`ilike`）缩小检索范围
- [ ] 匹配到多篇文档时，返回候选清单（标题 + 块数）让用户挑，**不擅自选一篇**
- [ ] 匹配不到任何文档时，返回友好文案并列出知识库现有文档名（复用 `kb_list` 的口径），不抛异常
- [ ] `ctx.citations` 回填逻辑**不变**（仍只在命中时写入文档 id / 标题 / 块序号 / 相似度）
- [ ] 无命中时的既有话术（"知识库中没有检索到…"）保持不变
- [ ] docstring 的「入参」段补充 `document` 说明，并在"什么时候不用"里保持与 `kb_list` 的互斥点名
- [ ] 单测：限定文档命中 / 多篇候选提示 / 文档不存在 / 不传 `document` 时旧行为不回归，共 ≥4 例
- [ ] `ruff check .` 通过（**只跑 check**，不要顺手 `ruff format` 整文件——会造出与本 story 无关的大量 diff）

#### US-104: `usage_stats` 支持按动作过滤

**Description:** 作为关心额度的用户，我想只统计某一类动作的用量，这样我能回答「我这周光面试花了多少 token」。

**Acceptance Criteria:**
- [ ] `usage_stats` 签名改为 `usage_stats(days: int = 7, action: str = "")`——**参数可省，默认行为与现在完全一致**
- [ ] `action` 非空时按 `UsageLog.action_type` 精确过滤（分组统计仍按 `action_type`）
- [ ] `action` 取值沿用现有英文枚举（`parse` / `analysis` / `interview_message` / `kb_upload` / `playground` / `chat_create` / `agent` / `agent_create` / `agent_tool_llm`），复用 `_ACTION_LABELS` 渲染中文
- [ ] 传入非法 `action` 时**不抛异常**：返回文案列出可用动作中文名，供模型引导用户
- [ ] 过滤后无记录时，文案说明"最近 N 天该动作没有记录"
- [ ] 合计 token 行在过滤模式下只统计该动作（不得混入其他动作）
- [ ] docstring 补充 `action` 说明与合法值枚举
- [ ] 单测：按动作过滤 / 非法动作友好提示 / 过滤后为空 / 不传 `action` 时旧行为不回归，共 ≥4 例
- [ ] `ruff check .` 通过（**只跑 check**，不要顺手 `ruff format` 整文件——会造出与本 story 无关的大量 diff）

#### US-105: 工具清单、断言测试与设计文档同步

**Description:** 作为后续维护者，我要保证「工具数量」在代码、测试、文档三处口径一致，这样不会出现"文档写 11 个实际 13 个"的失真。

**Acceptance Criteria:**
- [ ] `backend/tests/test_agent_tools.py::test_make_tools_exposes_eleven_tools` 改名为 `test_make_tools_exposes_thirteen_tools` 并断言 13 个工具名（含两个新工具名）
- [ ] `tools.py` 文件头「工具清单」注释更新为 13 条（12/13 号为新工具）
- [ ] `docs/Agent工具设计.md`：§一/§二 补两个新工具行（标注已实现），§四 的易撞组合表补 `interview_history ↔ interview_transcript`、`platform_help ↔ conversation_search` 两对
- [ ] 新增常量集中在既有 `_TOOL_*` 常量区，命名与现有风格一致
- [ ] 全量 `pytest` 全绿（不依赖本地 `.env`；AI 调用一律 mock，不烧额度）

#### US-106: 路由评测集扩容并复跑基线

**Description:** 作为 owner，我要在工具数变化后拿到可对比的路由准确率，这样才知道新增工具没有把路由搞乱。

**Acceptance Criteria:**
- [ ] `data/agent_eval/routing.json` 新增 ≥6 条用例：`conversation_search` ≥3 条（含"我之前问过你…""上次聊到哪了"这类**中文口语化**问法）、`interview_transcript` ≥2 条、`kb_search`（限定文档）≥1 条
- [ ] 用例的 `expected_tool` 与最终 docstring 口径一致；易撞对（`conversation_search` vs `platform_help`、`interview_transcript` vs `interview_history`）各至少 1 条互相区分的用例
- [ ] 跑 `python -m scripts.eval_agent_routing` 生成新 `data/agent_eval/report.md`，用例集统计显示 **13 个工具 / ≥39 题**
- [ ] top-1 准确率不低于改造前口径（改造前 33/33 = 100%）；如有误选，逐题定位是描述问题还是用例问题并修正
- [ ] 报告保留"与旧基线对比"行（脚本已有该能力，确认未失联）

#### US-107: 对外文档口径升版

**Description:** 作为读者，我要在 README 与技术文档上看到 13 工具的正确口径，这样对外描述不滞后。

**Acceptance Criteria:**
- [ ] `README.md` 工具数与工具清单更新到 13 个；如提及评测基线，数字与 `data/agent_eval/report.md` 一致
- [ ] `docs/tech-stack-and-features.md` 同步更新（工具数、评测口径）
- [ ] `docs/后续开发规划.md` v3.10 各任务行标记完成状态
- [ ] `grep -rn "11 个工具" README.md docs/` 不再命中旧口径（历史版本记录段除外，如 PROGRESS.md 的历史条目）
- [ ] 提示词版本判定有结论：本批次只改**工具描述**，不涉及 `PROMPT_VERSION` / `JOB_MATCH_PROMPT_VERSION` / `QUESTION_GEN_PROMPT_VERSION` / `ANSWER_REVIEW_PROMPT_VERSION`，**无需递增**（在 PR 说明里写明该判定）

---

### 4. Functional Requirements

- **FR-1**：系统必须提供 `conversation_search(keyword, limit=5)`，在**当前调用者自己的**会话消息内容中做关键词匹配，返回命中片段、会话标题、会话时间与来源类型。
- **FR-2**：`conversation_search` 必须同时覆盖 `session_type = 'chat'` 与 `'agent'` 两类会话，并在输出中标注来源；不得返回 `citations` / `tool_steps` 字段内容。
- **FR-3**：`conversation_search` 的 `keyword` 允许为空；为空时列出最近会话清单（标题 + 时间）。
- **FR-4**：系统必须提供 `interview_transcript(session_id=None, limit=10)`，返回指定或最近一场面试的逐条问答原文。
- **FR-5**：`interview_transcript` 在指定 `session_id` 不属于调用者时，必须返回统一的"没找到"文案，且**不泄露**该场次是否存在。
- **FR-6**：`interview_transcript` 的输出长度必须有硬上限（`_TOOL_TRANSCRIPT_CHARS = 2000`），超限截断并在文案中告知。
- **FR-7**：`kb_search` 必须接受可选 `document` 参数；非空时把检索范围限定在标题匹配的文档内；匹配多篇时返回候选清单。
- **FR-8**：`usage_stats` 必须接受可选 `action` 参数；非空时只统计该 `action_type`；非法值返回可用值清单而非异常。
- **FR-9**：所有新增/修改的工具都必须具备归属隔离：登录按 `user_id`、匿名按 `anonymous_id`（且 `user_id IS NULL`）；无法识别身份时明确拒绝。
- **FR-10**：所有新工具不得调用 LLM；不得写入 `usage_logs` 的 `agent_tool_llm` 记账。
- **FR-11**：所有工具必须复用现有异常处理范式（`try/except` + `logger.exception` + 自然语言兜底），不得让异常冒泡打断整轮对话。
- **FR-12**：工具数量在 `tools.py` 清单注释、`test_make_tools_exposes_thirteen_tools`、`docs/Agent工具设计.md`、README 四处必须一致。
- **FR-13**：路由评测集必须覆盖新增工具（`routing.json` ≥39 题 / 13 工具），并在报告里保留新旧基线对比。

---

### 5. Non-Goals (Out of Scope)

- **不做语义/向量检索的历史对话搜索**：本批次只用 `ILIKE` 关键词匹配；jieba 分词扩展与 `pg_trgm` 属于实测不达标后的后置优化。
- **不做跨用户 / 全平台范围搜索**：任何工具都只能看调用者自己的数据。
- **不改前端**：无新页面、新组件、新 SSE 事件类型；工具返回文本直接走现有聊天流。相关 UI 验收（dev-browser）在本批次不适用。
- **不做 `session_type` 过滤参数**：两类会话都搜、只标注来源（若实测噪声大再单开一批）。
- **不改 `interview_history` 的行为与返回结构**（只在 docstring 里补互斥说明）。
- **不给 `kb_search` 加多文档、标签、时间范围等更多过滤维度**（只加 `document`）。
- **不改 `daily_agent_limit` / `daily_agent_tool_llm_limit` 任何限额口径**。
- **不做历史对话的删除 / 清理 / 归档**功能。
- **不动 LangChain / Agent 版本**（继续锁 `langchain 0.3.x`、`<0.4`）。

---

### 6. Design Considerations

- **工具描述是唯一的"路由说明"**：所有新增 docstring 必须按「做什么 → 什么时候用 → 什么时候不用」三段写（见 `docs/Agent工具设计.md` §四），易撞工具之间要在"什么时候不用"里**互相点名**。
- **输出文案面向模型而非人**：段落短、结构清晰、带来源标签；中文标签沿用现有风格（`【简历】`、`【来源：…】`）。
- **空态与异常态都要给"下一步建议"**：例如"可让用户换关键词""可提示用户先上传简历"——这是现有工具的既有范式，保持一致性。
- **复用既有组件**：`_owner_filter()`、`_snippet()`、`_ACTION_LABELS`、`_TOOL_*` 常量区、异常兜底范式。
- **本批次无 UI 改动**：故不含"用 dev-browser 验收"类条目；如需人工确认，走真机在 AI 客服页提问冒烟（见 §8）。

---

### 7. Technical Considerations

- **零 LLM 调用**：两个新工具是纯 SQL 读 + 字符串拼接，故不占 `daily_agent_tool_llm_limit`、不影响主循环 `daily_agent_limit`。
- **归属过滤写法**：必须用 `_owner_filter(model, user_id, anonymous_id)`；**禁止只判 `user_id IS NULL`**——那是横向越权漏洞（另见 `backend/app/api/deps.py` 的 `owner_clause`，API 层同口径）。
- **ILIKE 对中文的局限**：口语化长问句与文档用词不一致时召回差；先用 `ILIKE` 上线，用真实问法实测决定是否接 `lexical_service`（jieba）或 `pg_trgm`（需 Alembic 迁移 + GIN 索引）。
- **上下文膨胀风险**：历史消息回灌过多会撑爆上下文——靠三层约束：`limit` 上限（5 / 20）、片段截断（80 字 / 2000 字符）、不回灌 `tool_steps` 与 `citations`。
- **数据库**：本批次**不需要**新迁移（只读现有表）。
- **格式化纪律**：本项目实际准入检查**只有 `ruff check .`**——`ruff format --check` 对 `tools.py` 等既有文件本来就报警（前格式化时代遗留），故本批次只跑 check，**不要整文件格式化**（会产生大量与业务无关的 diff）。ruff 启用扩展规则集（含 `RUF100`/`BLE001`/`B008`/`SIM`/`S110`），但 `E402` 未启用：`sys.path.insert` 后 import **不要加** `# noqa: E402`；`except Exception` + `logger.exception(...)` + 返回文案的既有范式**不要加** `# noqa: BLE001`（会被 RUF100 判为多余）。
- **工具顺序约束**：`kb_search` 必须始终是 `make_tools()` 返回列表的**第一个元素**（既有测试依赖该顺序）；新工具应追加在同类域工具附近，改完先跑 `test_agent_tools.py` 确认顺序断言没红。
- **测试**：`backend/tests/test_agent_tools.py` 扩写；造数用专属前缀（新增 `_CONV_PREFIX`、`_TRANSCRIPT_PREFIX` 之类）；跑测试前确认三容器在（`docker ps --filter name=ai-interview-`），否则用例会挂住；`pytest -q --basetemp=/tmp/pt_$(date +%s)` 规避沙箱批量删除保护。
- **评测成本提醒**：`scripts/eval_agent_routing` 每题一次真实 LLM 调用，39 题即 39 次；跑之前确认额度与模型可用。
- **CI**：`ruff` → `alembic check` → `pytest` → `npm build` 四关不变；评测脚本不进 CI（烧额度）。
- **不递增提示词版本**：工具描述不参与 `analyses` 表版本留档，故 `PROMPT_VERSION` 等常量不动（但**必须**跑路由评测）。

---

### 8. Success Metrics

- 新增工具后路由评测：13 工具口径 top-1 准确率 **≥ 100%（33/33 口径不下降）**，报告含新旧对比行。
- 人工真机冒烟：在 AI 客服页用 ≥3 条口语化问法（"我之前问过你怎么配置 JWT 来着""上次聊 RAG 优化聊到哪了""上次面试官问了我啥"）能选出正确工具并给出可用回答。
- 隔离类单测全绿：跨用户、跨匿名、指定他人 `session_id` 三类用例 100% 覆盖且全部通过。
- 工具数口径一致：`grep` 四处（代码注释 / 测试断言 / 设计文档 / README）均为 13，零残留"11 个工具"。
- 无新增 LLM 调用：`usage_logs` 中 `agent_tool_llm` 记录不因本批次新增（新工具为纯查库）。

---

### 9. Open Questions

1. **ILIKE 中文召回的触发阈值**：定"人工 10 条真实问法命中率 < 60% → 启动 jieba/pg_trgm 优化"这条判据可以吗？还是你更想一开始就上分词？
2. **`usage_stats.action` 是否接受中文**（如 `action="模拟面试"`）？建议只收英文枚举，中文由 `_ACTION_LABELS` 反向映射兜底——是否同意？
3. **`conversation_search` 要不要搜 AI 客服的 `tool_steps` 文本**（即"我上次让它查过什么"）？建议**不搜**（避免过程噪声污染命中片段）。
4. **评测集新增用例由谁写**：建议实现者随手写（含 3 条口语化问法），比事后补写更贴合真实 docstring 措辞——是否同意？
5. **本批次排期**：放在 v3.8（本地一键演示模式）**之后**，还是与 v3.8 并行（改动文件不重叠）？
