# PRD: 学生就业帮助系统重构（企业级架构 + 统一登录 + 用户体系）

> **合并稿**：本 PRD 已并入 `tasks/prd-合并稿-重构计划与Agent工具扩展.md` 的 **Part 1**。本文件仍是**本项目权威原稿**，改动请改这里再重新装订。

> 编制日期：2026-09-27 · 状态：**待评审**
> **合并说明**：本文件是该校「学生就业帮助」系统重构的**单一权威文档**——已把原《重构实施规划》（现状反推 / 技术选型 / 分层设计 / 认证授权 / 目录结构 / 分期任务）与本 PRD 的 US 级验收**合并为一份**。原规划文档保留作历史稿：`tasks/学生就业帮助系统重构/重构实施规划.md`。
> ⚠️ **前置事实**：目标项目源码**不在本机**（已按文件名、目录名、页面内容三路全盘排查，零命中；`127.0.0.1:8000` 实际跑的是另一个项目 AIResume 的后端）。故第 3 章现状为**截图反推**，US-001 是硬阻塞项——**未完成 US-001 不得开工任何实现类故事**。

---

## 1. Introduction

现有系统是一个 Hogwarts 皮肤的多页静态站点：`注册登录.html`、`主界面.html` 等页面由后端在同一端口（8000）直接托管，登录只是"页面跳转惯例"而非安全边界；`admin/123456` 这类弱口令可直接使用；管理端（使用日志 / 数据看板 / 语料库管理）与用户端共用一套页面壳。

本 PRD 定义一次**保持业务不变**的结构性重构：

- **四大功能模块完整保留**：主界面、录音分析（三 Tab：录音转文本 / 角色审核 / 面试审核）、面试题生成、模拟面试（选简历 + 选题库 + 逐条问答）。
- **入口强制登录**：后端全局鉴权依赖 + 前端路由守卫双闸门；未认证一律 401 / 重定向登录；旧 `.html` 物理下线归档，不存在可直达的页面入口。
- **新增用户体系**：注册 / 登录 / 修改密码 / 登出，凭证以 argon2id 安全存储。
- **保留 admin/123456**：账号与口令仍可登录、管理端权限口径**原样冻结**，但口令改为哈希存储。
- 技术栈升级为 FastAPI（严格分层）+ Vue3 SPA + PostgreSQL + Alembic。

**交付边界**：只做本重构，不新增业务功能、不改管理端功能集合、不做手机号登录、不做移动端。

---

## 2. Goals

- 四大模块**功能零丢失**：逐条对照《模块迁移映射表》打勾，交互与文案与改造前一致。
- 未登录用户**无法访问任何业务数据**：所有 `/api/**` 默认要求认证，白名单仅 3 个；跨用户访问一律 404。
- 口令安全达标：全库无明文/弱哈希口令；`admin/123456` 以 argon2id 存储且可正常登录。
- 前端从"多页静态 HTML"升级为带路由守卫的 SPA，旧 `.html` 不可再直接访问。
- 引入 Alembic 迁移：数据库变更全部可追溯，`alembic check` 零差异。
- 四大模块各自完成"接口 → 页面对照 → 用例"闭环后再进入下一模块。
- 质量门禁落地：`ruff check` / `alembic check` / `pytest` / `npm run build` 四关全绿。

---

## 3. 现状反推与核对（合并自原规划 §1）

### 3.1 从截图能确证的事实

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

### 3.2 推断的现状技术栈（待 US-001 核对）

| 层 | 推断 | 置信度 |
|---|---|---|
| 后端 | Flask（或同类 WSGI）单入口 + `static/` 托管 HTML | 中 |
| 视图 | 静态 HTML + 内联 `<script>`/`fetch`，无框架无构建链 | 高 |
| 存储 | SQLite 或 MySQL，表结构扁平 | 低 |
| 认证 | 前端存储或后端 session，**无统一中间件** | 中 |
| 口令 | 明文或弱哈希 | 高 |
| 授权 | 仅前端隐藏菜单，后端无角色校验 | 中 |

### 3.3 五个结构性问题（重构要解决的）

1. **入口无强制校验**：`.html` 是物理文件，知道 URL 即可访问。
2. **口令存储不安全**：弱口令 + 明文/弱哈希，一次库泄露即全量失守。
3. **无分层**：路由里混 SQL、文件解析与业务判断。
4. **无授权校验**：管理端接口未鉴权，典型横向越权风险。
5. **无迁移机制**：改表靠手写 SQL，无法演进。

---

## 4. 技术框架选型（合并自原规划 §2）

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

## 5. 系统分层设计（合并自原规划 §3）

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

## 6. 认证与授权方案（合并自原规划 §4）

### 6.1 用户模型

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

### 6.2 口令安全

argon2id（`pwdlib[argon2]`）；盐由算法内置生成，**不自造盐**；只存哈希，日志/异常/响应不得回显；**禁止**明文、MD5、SHA1、无盐 SHA256、可逆加密、`==` 直接比较；新注册与改密要求长度 ≥8 且含字母与数字（前端提示 + 后端再校验）。

### 6.3 Token 与会话

| 项 | 方案 |
|---|---|
| 形态 | JWT(HS256)，**HttpOnly + SameSite=Lax Cookie**（生产 HTTPS 加 `Secure`） |
| Claims | `sub` / `role` / `iat` / `exp` / `ver`(token_version) |
| 有效期 | 24h（可配）；`jwt_secret_key` 仅存 `.env`，若为默认值则**拒绝启动** |
| 登出 | 清 Cookie；如需即时吊销把 `jti` 写 Redis 黑名单（配置开关，默认关） |
| 改密 | `token_version += 1` → 旧 Token 全端失效 |
| 前端存储 | **不落 `localStorage`**，Cookie 由浏览器自动携带 |

### 6.4 入口强制登录：双闸门 + 一条白名单

- **闸门一（权威）后端全局鉴权依赖**：`/api/**` 默认要求认证，未过 → **401**；白名单仅 `POST /api/auth/register`、`POST /api/auth/login`、`GET /health`。用统一挂载前缀依赖实现，避免"漏写一个 Depends 就漏一个洞"。
- **闸门二（体验）前端 `router.beforeEach`**：无会话 → `redirect=/login?redirect=<原路径>`；`meta.requiresAdmin` 且非 admin → 403 页；`api/` 统一拦截 401 → 清状态跳登录。
- **物理隔离**：旧多页 `.html` 迁入 `legacy/` 归档，静态站点只暴露构建后的 SPA。

### 6.5 授权

| 角色 | 能力 |
|---|---|
| `user` | 四大模块全部功能；仅能访问**自己的**简历/录音/题库/面试会话 |
| `admin` | 用户能力 + 使用日志 / 数据看板 / 语料库管理（**本次冻结，只保证改造后仍可用**） |

管理端接口统一 `require_admin`（→ 403）；归属校验统一收敛到 helper，登录按 `user_id`、匿名按 `user_id IS NULL AND anonymous_id = ?`；**跨用户一律 404**（不泄露存在性）。

### 6.6 admin/123456 的保留策略

| 要求 | 落地方式 |
|---|---|
| admin/123456 仍可登录 | 迁移脚本一次性以 argon2id 写入 `password_hash`，`role='admin'` |
| 管理权限原状 | 管理端路由、菜单、接口集合与判定口径不改；只把鉴权入口收敛到 `require_admin` |
| 安全兜底 | 配置开关 `force_admin_pwd_change_on_first_login`（**默认关**，保原行为；文档标注上线前建议置 true） |
| 弱口令风险 | admin 登录写审计日志；连续失败按 `username+IP` 锁定（默认 5 次 / 15 分钟） |

---

## 7. 四大模块迁移映射（合并自原规划 §5）

| 模块 | 现状（截图） | 重构后前端 | 重构后接口 | 涉及表 | 保真要点 |
|---|---|---|---|---|---|
| 主界面 | 左侧 5 项导航 + 顶部标题 + 右上头像与历史记录 | `AppLayout` + `NavSideBar` + `HomeView`（按角色渲染菜单） | `GET /api/auth/me`、`GET /api/home/summary` | users | 导航命名与顺序不变；按角色显隐；头像与用户名保留 |
| 录音分析 | 三 Tab + 大拖拽上传 + 格式/大小/时长提示 + 历史 | `AudioAnalysisView` + `UploadCard` + Tabs + `HistoryDrawer` | `POST/GET /api/audio/jobs`、`GET /api/audio/jobs/{id}`、`POST /api/audio/jobs/{id}/retry` | `audio_jobs` | 三 Tab 与提示文案一致；结果为异步长任务且进度可见；历史可回溯 |
| 面试题生成 | 拖拽上传简历（PDF/Word <10MB）+ 生成提示 + 历史 | `QuestionGenView` + 进度 + 题库详情 | `POST/GET /api/question-banks`、`GET /api/question-banks/{id}` | `resumes`、`question_banks` | 上传约束与文案一致；结果按题目列表展示；历史可再打开 |
| 模拟面试 | 选简历 + 选题库（含题数）+ 确认加载 + 聊天 + 历史/清空 | `MockInterviewView` + `ChatPanel` + `HistoryDrawer` | `POST /api/interviews`、`POST /api/interviews/{id}/messages`、`GET/DELETE /api/interviews`、`GET /api/interviews/{id}` | `interview_sessions`、`interview_messages` | "选完两者才能加载"不变；消息逐条落库（刷新可恢复）；题库下拉显示题数 |
| （待确认）返校列车 | 侧栏第 5 项 | 占位页（默认处理） | — | — | 默认保留为"建设中"占位，不擅自删除 |

共用支撑：`resumes`、`usage_logs`（每日限额）、`audit_logs`（管理端使用日志数据源）。

---

## 8. 目录结构（合并自原规划 §6，精简版）

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

## 9. User Stories

> 顺序即执行顺序（依赖优先）。UI 类故事必须做浏览器验收；纯后端/文档类故事的收束条件是 `ruff check .` 与 `pytest`。

### US-001: 现状核对与视觉基线存档（**硬阻塞**）
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

### US-002: 仓库骨架与环境自检
**Description:** 作为开发者，我需要一个能启动的空壳工程与一条自检命令，这样环境问题在写业务前就暴露。

**Acceptance Criteria:**
- [ ] 建出第 8 章目录骨架；`docker-compose.yml` 起 postgres + redis（具名卷）
- [ ] `.env.example` 含数据库/密钥/Redis 变量；`.gitignore` 忽略 `.env`
- [ ] `core/config.py`（pydantic-settings）读 `.env`；`jwt_secret_key` 为默认值时启动失败
- [ ] `core/errors.py` 输出统一错误体 `{code,message,details}`；`core/logging.py` 可用
- [ ] `GET /health` 返回服务与数据库连接状态
- [ ] `scripts/check_env.py` 一条命令输出容器/迁移/端口/DB 状态
- [ ] `ruff check .` 通过

### US-003: 数据层与初始迁移
**Description:** 作为开发者，我需要全部业务表通过迁移一次建齐，这样 schema 可演进、可复现。

**Acceptance Criteria:**
- [ ] 建出 users / resumes / audio_jobs / question_banks / interview_sessions / interview_messages / usage_logs / audit_logs 模型
- [ ] 所有模型在 `models/__init__.py` 登记
- [ ] 迁移 `0001_init_users.py` + `0002_init_business.py` 可从零 `alembic upgrade head`
- [ ] 索引与唯一约束按预期生成（username/email 唯一、外键索引）
- [ ] `alembic check` 无差异
- [ ] `ruff check .` 通过

### US-004: 口令与令牌安全模块
**Description:** 作为开发者，我需要一套统一的口令哈希与 JWT 工具，这样任何地方都不会各写一套。

**Acceptance Criteria:**
- [ ] `core/security.py` 提供 `hash_password` / `verify_password`（argon2id，`pwdlib`）
- [ ] 提供 `create_access_token(user_id, role, token_version)` 与 `decode_access_token`
- [ ] 同口令两次哈希结果不同（盐生效），且都能校验通过
- [ ] 篡改过的 Token 解码返回 `None`，过期 Token 解码返回 `None`
- [ ] `ruff check .` 且新增 `pytest` 用例通过

### US-005: 注册接口
**Description:** 作为新用户，我想注册账号，这样我才能登录并使用系统。

**Acceptance Criteria:**
- [ ] `POST /api/auth/register` 接受用户名 + 口令（+ 可选邮箱），返回用户基本信息（**不含** `password_hash`）
- [ ] 口令长度 <8 或缺字母/数字时返回 400，并说明规则
- [ ] 用户名重复返回 409；用户名（`admin`/`root` 等保留字）返回 400
- [ ] 落库的 `password_hash` 以 `$argon2id$` 开头
- [ ] 注册接口按 IP 限流（超限 429）
- [ ] 测试造数用专属用户名前缀并在用例后范围化清理（禁全表 DELETE）
- [ ] `ruff check .` 且 `pytest` 通过

### US-006: 登录接口与失败锁定
**Description:** 作为用户，我想用账号口令登录，这样我能拿到访问凭证。

**Acceptance Criteria:**
- [ ] `POST /api/auth/login` 校验通过后下发 **HttpOnly Cookie**（`SameSite=Lax`），响应体返回用户信息与角色
- [ ] 口令错误返回 401，文案统一为「用户名或密码错误」（不泄露用户是否存在）
- [ ] 同一 `username+IP` 连续失败 5 次后锁定 15 分钟，期间返回 429/423 并带剩余时间
- [ ] 登录成功写 `last_login_at`，并写一条审计日志
- [ ] 错误口令不会在日志或响应中出现
- [ ] `ruff check .` 且 `pytest` 通过

### US-007: 登出、当前用户与修改密码
**Description:** 作为用户，我想退出登录并在需要时改密码，这样我能保护账号。

**Acceptance Criteria:**
- [ ] `POST /api/auth/logout` 清 Cookie 返回 204
- [ ] `GET /api/auth/me` 返回当前用户信息与角色；未登录 401
- [ ] `POST /api/auth/change-password` 需原密码 + 新密码 + 二次确认；原密码错返回 400
- [ ] 新密码不满足强度返回 400
- [ ] 改密成功后 `token_version` 递增，**用改密前的 Cookie 调 `/api/auth/me` 返回 401**
- [ ] 改密成功后 `pwd_changed_at` 更新
- [ ] `ruff check .` 且 `pytest` 通过（含"旧 token 失效"用例）

### US-008: admin 账号保留与口令重哈希
**Description:** 作为管理员，我需要改造后仍能用 admin/123456 登录并保住原有管理权限。

**Acceptance Criteria:**
- [ ] 迁移 `0003_admin_rehash.py`：把 admin 口令以 argon2id 写入 `password_hash`（脚本只读旧值、不回显）
- [ ] `scripts/seed_admin.py` 幂等：重复执行不重复建号、不覆盖已改过的口令
- [ ] admin 行 `role='admin'`，用 `admin/123456` 登录成功
- [ ] 全库 `grep` 式校验：`users` 表中不存在明文/32 位 MD5 形态的口令
- [ ] 旧口径的登录比较分支代码被删除（无残留硬编码 `123456`）
- [ ] `ruff check .` 且 `pytest` 通过

### US-009: 鉴权闸门与授权依赖
**Description:** 作为系统，我需要在入口处统一拦截未授权请求，这样任何新接口都不会漏鉴权。

**Acceptance Criteria:**
- [ ] 所有 `/api/**` 路由默认挂全局认证依赖；白名单仅 `register` / `login` / `health`
- [ ] 未携带凭证访问任意业务接口返回 **401**
- [ ] `require_admin` 依赖存在：非 admin 访问管理端接口返回 **403**
- [ ] 归属校验 helper 统一实现（登录按 `user_id`、匿名要求 `user_id IS NULL AND anonymous_id = ?`）
- [ ] 归属 helper **不允许**退化为"只判 `user_id IS NULL`"（用例覆盖）
- [ ] 跨用户访问他人资源返回 **404**（不是 403）
- [ ] `ruff check .` 且 `pytest` 通过（覆盖未登录/非 admin/跨用户三类）

### US-010: 前端骨架、路由守卫与 401 拦截
**Description:** 作为用户，我未登录时不该看到任何业务页面，这样登录就是真正的入口。

**Acceptance Criteria:**
- [ ] Vite + Vue3 + vue-router + Pinia 骨架可 `npm run dev`
- [ ] `router/index.js` 中所有业务路由带 `meta.requiresAuth`；`beforeEach` 未登录跳 `/login?redirect=<原路径>`
- [ ] 未登录直接访问 `/`、`/audio`、`/questions`、`/interview`、`/admin/*` 均被重定向到登录页
- [ ] `api/http.js` 统一解析错误体，并对 `401` 清状态 + 跳登录
- [ ] `meta.requiresAdmin` 路由对普通用户显示 403 页
- [ ] `npm run build` 成功
- [ ] 浏览器验收：未登录访问受限路由被重定向（可用 agent-browser 或 cdp-ui-verify）

### US-011: 登录 / 注册 / 修改密码页
**Description:** 作为用户，我想在一个页面完成注册、登录与改密，这样入口清晰。

**Acceptance Criteria:**
- [ ] `LoginView.vue` 含登录 / 注册 / 修改密码三个 Tab
- [ ] 表单含前端校验（必填、口令强度、二次确认一致）并在提交前拦截
- [ ] 登录成功按 `redirect` 参数跳回原目标页，无 `redirect` 时进主界面
- [ ] 错误提示使用后端统一错误体的 `message`（不暴露内部细节）
- [ ] 摘掉"切换到手机号登录"等未实现文案
- [ ] `npm run build` 成功
- [ ] 浏览器验收：注册 → 登录 → 改密 → 用新口令登录 全链路可用

### US-012: 主界面（布局 + 导航 + 首页）
**Description:** 作为用户，我登录后要看到与改造前一致的工作台与导航。

**Acceptance Criteria:**
- [ ] `AppLayout` + `NavSideBar` 成为唯一壳；导航 5 项命名与顺序与截图一致
- [ ] `user` 角色不显示管理端菜单；`admin` 角色显示管理端 3 项
- [ ] 右上角显示用户名与头像；点击可登出
- [ ] `HomeView` 展示与改造前一致的功能入口
- [ ] 皮肤沿用 Hogwarts 配色（深木 / 米金 / 金），标题使用衬线字体
- [ ] `npm run build` 成功
- [ ] 浏览器验收：对照截图逐项核对导航与顶栏

### US-013: 录音分析后端
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

### US-014: 录音分析前端
**Description:** 作为用户，我想在页面里上传并看到三种分析结果，这样我不用命令行操作。

**Acceptance Criteria:**
- [ ] `AudioAnalysisView.vue` 含三 Tab：录音转文本 / 角色审核 / 面试审核
- [ ] 上传区支持拖拽，展示与截图一致的提示文案（格式/大小/时长）
- [ ] 上传后展示进度，完成后分 Tab 渲染对应结果
- [ ] 历史记录抽屉可打开历史任务并回看结果
- [ ] 失败态显示错误与重试按钮
- [ ] `npm run build` 成功
- [ ] 浏览器验收：三种 purpose 各跑通一次并回看历史

### US-015: 面试题生成后端
**Description:** 作为用户，我想用简历生成定制面试题，这样我能针对自己经历刷题。

**Acceptance Criteria:**
- [ ] `POST /api/question-banks` 接受简历文件（PDF/Word，<10MB），解析后生成题库
- [ ] 拒绝不支持格式与超限文件，返回明确错误体
- [ ] 生成结果落 `question_banks.questions`（JSONB），含题目分组与总数
- [ ] `GET /api/question-banks` 列历史（标题、题数、时间），`GET /api/question-banks/{id}` 取详情
- [ ] 他人题库返回 404
- [ ] 生成过程为异步任务，状态可轮询
- [ ] `ruff check .` 且 `pytest` 通过

### US-016: 面试题生成前端
**Description:** 作为用户，我想在页面里上传简历并查看生成的题库。

**Acceptance Criteria:**
- [ ] `QuestionGenView.vue` 含拖拽上传区与「基于简历内容生成定制化面试题」提示
- [ ] 生成中显示进度，完成后按题目列表分组展示
- [ ] 历史记录可打开过往题库详情
- [ ] 上传约束（格式/大小）与截图文案一致
- [ ] `npm run build` 成功
- [ ] 浏览器验收：上传 → 生成 → 打开历史 全链路可用

### US-017: 模拟面试后端
**Description:** 作为用户，我想基于某份简历和某个题库开始一场模拟面试并与 AI 逐条对话。

**Acceptance Criteria:**
- [ ] `POST /api/interviews` 接受 `resume_id` + `bank_id`，创建会话（校验两者归属，非法返回 404/400）
- [ ] `POST /api/interviews/{id}/messages` 接收用户回答并返回 AI 追问，逐条落 `interview_messages`
- [ ] `GET /api/interviews` 列历史（时间、简历、题库、状态、轮次）
- [ ] `GET /api/interviews/{id}` 返回会话消息（刷新可恢复）
- [ ] `DELETE /api/interviews/{id}` 清空会话内容
- [ ] 他人会话返回 404
- [ ] `ruff check .` 且 `pytest` 通过

### US-018: 模拟面试前端
**Description:** 作为用户，我想选简历与题库后开始对话，并能清空或翻看历史。

**Acceptance Criteria:**
- [ ] `MockInterviewView.vue` 两个下拉（简历 / 题库，题库显示题数）+「确认加载」按钮
- [ ] 未选齐两项时按钮不可用（与改造前交互一致）
- [ ] 加载后进入聊天区，支持发送、逐条追加消息、流式或轮询刷新
- [ ] 「清空聊天」与「历史记录」两个动作都在且生效
- [ ] 刷新页面后可恢复当前会话
- [ ] `npm run build` 成功
- [ ] 浏览器验收：完整走一场对话 + 清空 + 打开历史

### US-019: 管理端冻结校验
**Description:** 作为管理员，我需要改造后三个管理功能与改造前行为一致，且普通用户无法访问。

**Acceptance Criteria:**
- [ ] 使用日志 / 数据看板 / 语料库管理三页在新架构下可用，字段与交互与改造前一致
- [ ] 普通用户访问管理端页面被前端拦截且有后端 403 兜底
- [ ] 管理端接口全部经 `require_admin`
- [ ] 本次不新增、不删除、不改名任何管理端功能
- [ ] `npm run build` 成功
- [ ] 浏览器验收：admin 账号逐页核对三功能；普通账号访问被拒

### US-020: 安全加固收口
**Description:** 作为 owner，我需要把已知风险逐条堵上，这样重构不会留下新的漏洞面。

**Acceptance Criteria:**
- [ ] 上传校验扩展名 + MIME + 文件头；大小限制在服务端强制执行
- [ ] 上传文件名随机化；存储目录不在 web 根内；读取走鉴权接口（防路径穿越）
- [ ] 写操作校验 `Origin`；Cookie 具备 `HttpOnly` + `SameSite=Lax`
- [ ] 登录/注册限流生效；日志对口令、Token、邮箱打码
- [ ] 越权用例覆盖全部资源类型（简历/录音/题库/面试会话/管理端）
- [ ] 禁用 `v-html` 或做白名单过滤（XSS 面）
- [ ] `ruff check .` 且 `pytest` 通过

### US-021: 回归、交付与文档收口
**Description:** 作为 owner，我需要在交付前确认功能零丢失、四关全绿。

**Acceptance Criteria:**
- [ ] 模块迁移映射表逐条打勾，产出验收报告（含截图对照）
- [ ] `ruff check .` / `alembic check` / `pytest` / `npm run build` 四关全绿
- [ ] Docker 化交付：`docker compose up` 可起前后端 + 依赖；README 含启动步骤
- [ ] `docs/` 含数据库设计、接口清单、现状核对表
- [ ] 旧 `.html` 已归档至 `legacy/` 且不被静态托管
- [ ] 环境自检命令在 README 中说明

---

## 10. Functional Requirements

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

## 11. Non-Goals (Out of Scope)

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

## 12. Design Considerations

- **视觉基准**：以 US-001 存档的 `legacy/` 页面与皮肤 CSS 为准，重构后逐页对照，颜色/间距/文案尽量一致。
- **皮肤令牌化**：把 Hogwarts 配色收敛为 CSS 变量（深木 `#2b1a12`、米金 `#f7ecd2`、金 `#d9b45a`），避免散落硬编码。
- **组件复用**：`UploadCard`、`HistoryDrawer`、`ChatPanel`、`ProgressBar`、`EmptyState` 在多个模块复用，避免各模块各写一套。
- **交互一致性**：异步任务统一呈现"提交 → 进度 → 结果 / 失败重试"三态。
- **文案一致性**：上传约束、提示语与改造前逐字对照（截图即验收标准）。
- **可访问性底线**：表单控件带 label，错误提示与字段关联。

---

## 13. Technical Considerations

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

## 14. Success Metrics

- 模块保真：四大模块对照表 100% 打勾，无功能缺失（以截图与 `legacy/` 存档逐项核对）。
- 认证有效：未登录访问任何业务接口 100% 返回 401（自动化用例覆盖全部路由前缀）。
- 授权有效：跨用户访问 100% 返回 404；普通用户访问管理端 100% 返回 403。
- 口令安全：`users` 表中 0 条明文/弱哈希口令；admin/123456 可登录。
- 交付质量：四关全绿；`alembic check` 零差异。
- 可维护性：新增一个业务模块只需"schema + service + router + view + api 文件"五步，无需改动横切逻辑。

---

## 15. 风险与对策（合并自原规划 §9）

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

## 16. Open Questions

1. **「返校列车」是什么？** 侧栏第 5 项，不属于四大模块。默认按"占位"处理；若为真实业务请补功能说明。
2. **数据库是否允许用 PostgreSQL？** 默认 PG 16（Docker）。若答辩/课程环境强制 MySQL 或 SQLite，需替换驱动与迁移细节（JSONB → JSON 等），请在 US-001 前确认。
3. **admin 是否要"首登强制改密"？** 默认关闭（保持要求 4 的原有行为）；开启可显著降低弱口令风险，但改变 admin 使用体验。
4. **旧业务数据要不要迁移？** 当前只规划"口令重哈希"。若旧库有简历/题库/面试数据需保留，需要单独的迁移故事（含字段映射与清洗规则）。
5. **录音转写用哪个引擎？** 现状能力未知（US-001 查证）。若需引入新服务，属于外部依赖，需明确账号与费用归属。
6. **`repositories/` 层是否保留？** 多人协作值得；单人维护可能偏重，可合并进 service（需 owner 拍板）。
7. **浏览器验收由谁执行？** UI 故事要求浏览器验收，需确认由 owner 人工确认还是用 agent-browser / cdp-ui-verify 自动化。
