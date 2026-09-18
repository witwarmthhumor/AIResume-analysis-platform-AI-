# PRD: 收口止血（v3.6 发布一致性）

> 本文件是需求文档，不含实现。承接 2026-09-18 体检结论，是 `docs/后续开发规划.md` 第 1 批的详细规格。

## 1. Introduction

Ralph 自动循环已把 13 个 story 落地（Agent 工具 4→11、路由评测、混合检索提优），但成果**停在 `ralph/agent-tool-expansion` 分支未合并**，`main` 仍停在 v3.5；`origin/main` 落后 14 个提交；v3.5 及其后**没有任何 tag**。

同时体检暴露一个真实的健壮性缺陷：**数据库不可用时业务请求会挂起一分钟以上**（实测容器停止后 `POST /api/auth/register` 无响应），而 `db/session.py` 只配了连接池大小与 `pool_pre_ping`，缺连接超时与池获取超时。

此外还有一批一致性问题：vite 代理指 8001 而其余文档全写 8000、`.workbuddy/` 未被忽略、`scripts/ralph/` 忽略策略不统一、两份架构全景 HTML 与 PRD 未提交。

本 PRD 的目标是把这些**收口干净**，让 `main` 分支可信、环境可自检、故障可快速失败。**不做第 2 批**（CI / 覆盖率 / 依赖锁定 / 11 工具文档升版）。

---

## 2. Goals

- `main` 分支包含 Ralph 的全部 13 个提交，工作区干净
- 补齐 tag `v3.5` 与 `v3.6`，恢复"每阶段封版"的锚点（**不推送远端**）
- 端口口径统一为 **8000**（代码、配置、文档三处一致）
- 数据库不可用时接口**10 秒内返回 503**，不再挂起
- 提供一键环境自检脚本，任一失败项都给出**可直接复制的修复命令**
- 全量测试保持全绿（247 → ≥250）

---

## 3. User Stories

### US-001: 合并 ralph 分支到 main

**Description:** 作为开发者，我需要让 `main` 包含 Ralph 的全部成果，这样主线才代表项目真实状态。

**Acceptance Criteria:**
- [ ] 在 `main` 分支执行合并 `ralph/agent-tool-expansion`，**无冲突**
- [ ] `git log --oneline -1` 的 HEAD 为 `6424543`（`feat: [US-013] - 前端工具名中文化`）或其合并提交
- [ ] `git rev-list --count ralph/agent-tool-expansion..main` 输出 `0`（main 已含分支全部提交）
- [ ] 合并后位于 `main` 分支，且 `git diff --stat main ralph/agent-tool-expansion` 为空
- [ ] 合并后在 `backend/` 下 `python -m pytest -q` 全绿（≥247 passed）
- [ ] ruff check 通过

> ⚠️ 合并前先确认工作区没有会被卷入的业务代码改动（`frontend/vite.config.ts` 属 US-003 处理范围，合并前先记录其 diff）。

### US-002: 补齐 v3.5 与 v3.6 tag

**Description:** 作为开发者，我需要版本锚点，这样出问题能回到已知状态。

**Acceptance Criteria:**
- [ ] 打轻量 tag `v3.5` 指向 `2e45707`（混合检索提优 + 四工具版本）
- [ ] 打轻量 tag `v3.6` 指向 US-001 合并后的 `main` HEAD
- [ ] `git tag --sort=creatordate | tail -3` 依次显示 `v3.4`、`v3.5`、`v3.6`
- [ ] `git show v3.5 --stat | head -20` 包含 `lexical_service.py` 或 `kb_service.py`
- [ ] `git show v3.6 --stat | head -20` 包含 `services/agent/tools.py` 或 `eval_agent_routing.py`
- [ ] **不推送远端**（`git status` 显示 `ahead of origin/main` 属预期）

### US-003: 端口口径统一为 8000

**Description:** 作为开发者，我需要代码与文档说的是同一个端口，否则每次启动都要猜。

**Acceptance Criteria:**
- [ ] `grep 'target:' frontend/vite.config.ts` 输出 `http://127.0.0.1:8000`
- [ ] 后端以 8000 启动：`python -m uvicorn app.main:app --reload --port 8000`
- [ ] `curl --noproxy '*' http://localhost:8000/health` 返回 `{"status":"ok",...}`
- [ ] `curl --noproxy '*' http://localhost:5173/api/health` 返回同样内容（代理链路通）
- [ ] `npm run build` 通过
- [ ] `frontend/vite.config.ts` 的改动**已提交**，不再游离于工作区
- [ ] Verify in browser using dev-browser skill

### US-004: 补齐 .gitignore 规则

**Description:** 作为开发者，我需要工作区记忆与 Ralph 运行态不被误提交。

**Acceptance Criteria:**
- [ ] `.gitignore` 新增 `.workbuddy/`，且带一行注释说明用途
- [ ] `git check-ignore -v .workbuddy/` 能命中规则
- [ ] `git status --short` 不再出现 `.workbuddy/`
- [ ] `scripts/ralph/.last-branch` 加入忽略（运行态文件）
- [ ] `scripts/ralph/ralph.sh`、`watch.sh`、`CLAUDE.md`、`prd.json`、`progress.txt` **保留入库**（可复用工具与进度记录），并在 `.gitignore` 中不列它们

### US-005: 提交本轮的文档与脚本产出

**Description:** 作为开发者，我需要把体检与文档产出纳入版本管理。

**Acceptance Criteria:**
- [ ] `docs/架构全景.html`、`docs/架构全景-v2.0.html` 已提交
- [ ] `docs/后续开发规划.md`（含 09-18 更新）已提交
- [ ] `tasks/prd-agent-tool-expansion.md`、`tasks/prd-release-consolidation.md` 已提交
- [ ] `scripts/ralph/ralph.sh`、`watch.sh`、`CLAUDE.md` 已提交
- [ ] 提交信息遵循中文 conventional commits（如 `chore: 收口工作区与文档产出`）

### US-006: 数据库连接快速失败

**Description:** 作为用户，我不想在数据库不可用时看着页面转圈一分钟；作为开发者，我需要马上得到明确的失败信号。

**Acceptance Criteria:**
- [ ] `backend/app/core/config.py` 新增 `db_connect_timeout_seconds`（默认 5）与 `db_pool_timeout_seconds`（默认 10）
- [ ] `backend/app/db/session.py` 的 `create_engine` 增加 `pool_timeout=settings.db_pool_timeout_seconds` 与 `connect_args={"connect_timeout": settings.db_connect_timeout_seconds}`
- [ ] 新增全局异常处理：数据库连接类异常（`sqlalchemy.exc.OperationalError`、`InterfaceError`）统一转 **503**，响应体为项目统一结构 `{code, message, details}`
- [ ] 503 的 `message` 为可读话术（示例：「数据库暂时不可用，请稍后重试；若长时间无响应请联系管理员」），不泄露连接串或堆栈
- [ ] `code` 取值与现有错误体系风格一致（如 `database_unavailable`）
- [ ] **实测**：停掉 `ai-interview-db` 后，`POST /api/auth/register` 在 **10 秒内**返回 503（记录实测耗时）
- [ ] **实测**：`docker start ai-interview-db` 后同一接口立即恢复正常
- [ ] 新增单测（mock 引擎抛 `OperationalError`）：断言状态码 503、`code` 与 `message` 符合预期
- [ ] 现有 247 个测试全绿，`ruff check .` 通过

### US-007: 一键环境自检脚本

**Description:** 作为开发者，我需要在启动项目前一条命令看清所有前置条件，而不是逐个试错。

**Acceptance Criteria:**
- [ ] 新增 `backend/scripts/check_env.py`，用法 `python -m scripts.check_env`
- [ ] 逐项检查并输出结果（通过 / 失败），检查项至少覆盖：
      ① Docker daemon 可用 ② 三容器 `ai-interview-db / redis / ollama` 均在 healthy
      ③ 数据库可达且 Alembic 版本为 `head` ④ Redis 可达
      ⑤ 后端端口（8000）与前端端口（5173）是否已被占用
      ⑥ 后端 `/health` 与 `/health/ready` 响应 ⑦ 预置语料 `kb_chunks` 条数
      ⑧ AI 通道可用性（最小请求 `max_tokens=1`）
- [ ] 任一检查失败**不中断后续检查**，最后输出汇总（通过 N 项 / 失败 M 项）
- [ ] 每个失败项附带**可直接复制的修复命令**（如 `docker start ai-interview-db ai-interview-redis ai-interview-ollama`）
- [ ] 支持 `--skip-ai` 跳过 AI 通道检查（避免无意消耗额度）
- [ ] 退出码：全部通过为 `0`，存在失败为 `1`
- [ ] 服务未启动时不崩溃，而是报告"后端未监听"并给出启动命令
- [ ] 新增测试（mock 各项检查结果）：断言汇总统计与退出码逻辑
- [ ] `ruff check .` 通过

### US-008: 文档同步自检脚本用法

**Description:** 作为后来者，我需要知道启动前该跑什么。

**Acceptance Criteria:**
- [ ] `README.md` 新增「启动前自检」小节，含命令与输出示例说明
- [ ] `AGENTS.md` 的「启动命令」区新增该命令，并在"已知"清单里补一条"容器闲置会自行退出，启动前先自检"
- [ ] README 与 AGENTS.md 中的后端端口表述统一为 8000
- [ ] 文档中不出现 8001

### US-009: 收口验收与进度记录

**Description:** 作为项目 owner，我需要这次收口在日志与规划里留痕，避免下次重复盘问。

**Acceptance Criteria:**
- [ ] `PROGRESS.md` 追加本次收口记录（分支合并、tag、端口、DB 超时、自检脚本、提交范围）
- [ ] `docs/后续开发规划.md` 第 1 批的 5 项任务标记为已完成（打勾或改为"✅"），未做项保持原状
- [ ] 最终 `git status --short` 为空（无未跟踪、无未提交）
- [ ] 最终在 `main` 上：`python -m pytest -q` ≥250 passed、`ruff check .` 通过、`npm run build` 通过
- [ ] `git log --oneline -8` 能看出收口批次

---

## 4. Functional Requirements

- FR-1: `main` 分支必须包含 `ralph/agent-tool-expansion` 的全部提交
- FR-2: tag `v3.5` 与 `v3.6` 必须存在且指向正确的提交
- FR-3: 前端开发服务器的 API 代理目标必须是 `http://127.0.0.1:8000`
- FR-4: `.gitignore` 必须忽略 `.workbuddy/` 与 `scripts/ralph/.last-branch`
- FR-5: 数据库连接的 `connect_timeout` 与 `pool_timeout` 必须可配置，默认 5 秒 / 10 秒
- FR-6: 数据库连接失败必须返回 HTTP 503 + 统一错误结构，且响应时间不超过 15 秒
- FR-7: 错误响应不得包含数据库连接串、用户名、密码或 Python 堆栈
- FR-8: 环境自检脚本必须覆盖 Docker / 容器 / 数据库 / 迁移版本 / Redis / 端口 / 探针 / 语料 / AI 通道九类检查
- FR-9: 自检脚本的每个失败项必须输出可执行的修复命令
- FR-10: 自检脚本退出码必须可被 CI 或脚本判断（0 = 全通过）

---

## 5. Non-Goals（明确不做）

- **不推送远端**（保持项目"本地 tag、不推远端"的既有策略；`origin/main` 的落后问题留待单独决策）
- **不做第 2 批**：不建 CI、不加覆盖率门槛、不做依赖锁定、不清 `httpx2`、不做 11 工具的文档升版（README 仅改端口与新增自检小节）
- 不改动任何业务逻辑（除 US-006 的错误处理外）
- 不升级任何依赖版本
- 不修改 Ralph 已落地的功能代码
- 不做测试隔离改造（老测试的全表清理留待第 2 批）

---

## 6. Design Considerations

- **自检脚本输出风格**：与 `scripts/eval_rag.py` 保持一致 —— 分节标题 + 逐项一行结论 + 末尾汇总；失败项用醒目前缀（如 `[FAIL]`）而非 emoji
- **修复命令**：直接给可复制的一行命令（项目在 Git Bash 下运行，避免多行与续行符）
- **503 话术**：与现有统一错误体系（`ValidationError` / `RateLimitError` 等）风格一致，中文、可读、不含技术细节
- **可复用的现有设施**：`db/session.py` 的 `ping_database()` 已有"不抛异常只报状态"的写法，自检脚本可直接复用它

---

## 7. Technical Considerations

| 事项 | 说明 |
|---|---|
| **两个超时的位置** | `pool_timeout` 是 SQLAlchemy `create_engine` 参数；`connect_timeout` 是 **psycopg 驱动参数**，必须放在 `connect_args` 里 |
| **psycopg3 兼容性** | 项目用 `psycopg[binary]>=3.1`，其 `connect_timeout` 语义为秒；需实测确认生效 |
| **异常处理器落点** | 项目已有全局异常处理（统一错误结构），新增 DB 异常的 handler 应加在同一处，不要在路由内 try/except |
| **测试不要真停容器** | US-006 的自动化测试用 monkeypatch 让引擎抛 `OperationalError`，避免测试依赖 Docker 状态；"停容器实测"只在验收时人工做一次并记录耗时 |
| **跑全量测试的前置** | 必须先确认三容器在（否则会挂起），跑完需 `python -m scripts.seed_kb_preset` 恢复语料 |
| **沙箱会干扰测试** | pytest 清理临时目录可能被批量删除保护拦截而产生假失败；用 `--basetemp=/tmp/pt_$(date +%s)` 规避 |
| **合并顺序** | 先合并分支（US-001）→ 再处理工作区文件（US-003/004/005）→ 最后打 tag（US-002），避免 tag 指向含未提交改动的状态 |
| **tag 类型** | 用轻量 tag（与项目既有 v0.1~v3.4 一致） |

---

## 8. Success Metrics

- `git rev-list --count ralph/agent-tool-expansion..main` = **0**
- `git status --short` 输出为空
- 停容器后接口返回 503 的实测耗时 **≤ 15 秒**（目标 10 秒内）
- `check_env.py` 在三容器停止状态下运行：一次性列出全部失败项与修复命令，退出码 1
- `check_env.py` 在环境正常时退出码 0
- pytest 从 247 增至 **≥250**，全绿；`ruff check .` 通过；`npm run build` 通过

---

## 9. Open Questions

1. `origin/main` 落后 14 个提交 —— 这次明确不推，但要不要在 `docs/后续开发规划.md` 里把它列为独立待办？（现状：已列为 P0 问题 #2）
2. `scripts/ralph/CLAUDE.md` 是否入库？本 PRD 按"入库"处理（它是 Ralph 的 agent 指令，属工具配置），若认为属本地配置可改为忽略
3. US-006 的 503 是否需要附带 `Retry-After` 头，还是仅靠 `message` 提示即可？
4. 自检脚本是否要顺带检查 `frontend/node_modules` 是否存在（未安装时 `npm run dev` 会失败）？
