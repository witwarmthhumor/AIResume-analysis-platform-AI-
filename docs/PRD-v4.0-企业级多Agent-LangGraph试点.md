# PRD v4.0：企业级多 Agent 改造（LangGraph 试点）

> **本文件是干嘛的**：v4.0 的产品需求文档（PRD）——为什么做、做什么、不做什么、图怎么编排、数据/接口怎么变、分几期、怎么验收。动工前先评审本文件，定稿后再拆解实施计划与更新 PROJECT-PLAN.md。
> 编制日期：2026-09-22　·　状态：**待评审（等 owner 拍板未决问题后定稿）**
> 关联文档：[Agent工具设计.md](./Agent工具设计.md)、[实施计划-v3.4-双端分离与AI客服.md](./实施计划-v3.4-双端分离与AI客服.md)、[后续开发规划.md](./后续开发规划.md)、[../PROJECT-PLAN.md](../PROJECT-PLAN.md)、[../AGENTS.md](../AGENTS.md)
> 输入依据：《高含金量 Agent 项目的六大硬核标准》（抖音资料/Agent项目的六大标准.md）+ v3.7.1 现状盘点

---

## 0. 结论先行

v3.7.1 已有一个 **LangChain 0.3 单 ReAct Agent + 11 工具**的 AI 客服（路由评测 33/33 = 100%），但对照「六大标准」，缺的是**多步任务编排、失败恢复回环、人机协同断点续跑、全链路 Trace**。

本 PRD 的决策：

1. **引入 LangGraph 作为新链路（v2）的编排层，现有 AgentExecutor 客服（v1）零改动保留**，两条链路并存；
2. 试点只做**一条真实多步业务链路**：「读简历 → JD 匹配 → 按岗位出题」的**一键求职准备**，外加一个**风险动作人工确认（HITL）**节点；
3. LangGraph 用**同步 API + 同步 PostgresSaver**，贴合项目同步栈（同步 FastAPI + 同步 SQLAlchemy + 后台线程 + queue.Queue），**不做全异步化**；
4. 单 Agent 能解决的问答继续走 v1，**不为炫技硬拆**（标准 3 的明确要求）；v2 的价值用「端到端任务成功率 + 人工节省时长」证明，跑不出数据就不扩面。

**版本门控**：LangGraph 与现有 langchain-core 0.3（锁 `<0.4`）的兼容版本在 M0 阶段实测确认；若不存在兼容版本线，本 PRD 整体回退到「Celery/Redis + DB 状态机自研编排」备选方案（见 §14）。

---

## 1. 背景与问题

### 1.1 现状基线（v3.7.1，已实现）

| 能力 | 现状 |
|---|---|
| AI 客服 | LangChain 0.3 `create_openai_tools_agent` + AgentExecutor，11 工具，后台线程 + queue + SSE（meta/reset/action/observation/delta/done） |
| 工具 | kb_search（混合检索 RRF，唯一回填 citations）、个人数据 ×5、kb_list/platform_help、工具内 LLM ×3（job_match / question_gen / answer_review，走 `_run_tool_llm`，限额 20/日） |
| 简历分析 | `analysis_service` + `chat_json` schema 校验重试，六维报告、版本对比 |
| 模拟面试 | 四阶段多轮 + 四维评分报告 |
| 在线对话 | Playground RAG，混合检索 + SSE |
| 工程底座 | Postgres16 + pgvector、Redis/Celery、Alembic、归属隔离（owner_clause/matches_owner）、usage_logs 记账、CI（ruff/alembic/pytest/npm build）、覆盖率基线 89% |
| 评测 | eval_rag（hybrid hit@1 87.8% / hit@5 100%）、eval_agent_routing（33/33 = 100%） |

### 1.2 对照六大标准的差距盘点

| # | 标准 | 现状 | 差距（本 PRD 范围标 ✅） |
|---|---|---|---|
| ① | 真实问题 + 端到端闭环 + 效果指标 | 功能真实，但都是"一问一答/单次生成"，无跨能力多步任务 | ✅ 一键求职准备端到端任务 + 任务成功率指标 |
| ② | 完整后端工程 | API 分层、DB、权限、CI 已具备 | ✅ 增量补齐：模型网关式统一调用、审计表 |
| ③ | 规划/恢复/验证/多 Agent 协同 | 有 ReAct 单轮规划、schema 校验、工具吞异常；无显式 Planner、无失败回环、无审阅者 | ✅ Planner 节点 + Verifier 回环 + 多节点图（核心） |
| ④ | 上下文工程 | 有历史 N 轮裁剪、工具结果 300 字硬截断 | 🟡 部分：State 化上下文 + 节点产物摘要；长期记忆挂 v4.1 |
| ⑤ | 可观测 + 测评 | 有 usage_logs（token 级）、两个离线评测脚本 | ✅ trace_id/span 调用链 + 成功率/延迟/重试率指标 + 端到端评测集 |
| ⑥ | 人机协同 | 无 | ✅ interrupt 审批节点 + 审批队列 + 断点续跑 + 审计日志 |

### 1.3 为什么是 LangGraph（选型结论）

目标架构里最硬的三个缺口，自研都容易翻车，而 LangGraph 是原生能力：

| 需求 | LangGraph | 自研替代 |
|---|---|---|
| Planner 路由 + Verifier 失败回环 | StateGraph 条件边 + 循环 | 自写状态机，分支/重试边界易漏 |
| HITL 中断、断点续跑 | `interrupt()` + checkpointer + `Command(resume=...)` | 自行序列化中间状态，最易出 bug |
| 调用链追踪/回放 | 每步 state 快照 + 节点事件 | usage_logs 只有 token，没有链路 |
| 流式 | 同步 `graph.stream()` 可直接接入现有「后台线程 + queue」 | —— |

**不引入 LangSmith 云**：简历属隐私数据，Trace 一律自研落库（agent_spans）；LangSmith 仅允许本地开发临时开启且默认关闭、不传简历正文（见 §10.4）。

---

## 2. 目标与非目标

### 2.1 目标（v4.0 验收口径）

- **G1 试点链路**：用户在 AI 客服页发起「一键求职准备」，系统自动完成「定位本人简历 → 读取/补全分析 → JD 匹配 → 按岗位出题 → 校验汇总」，全程节点级流式可见。
- **G2 失败恢复**：任一节点模型输出不合格时自动回环重试（每节点上限 2 次），超限走明确失败路径，不把错误结果交给用户。
- **G3 人机协同**：图执行到含持久化副作用/不可逆动作时中断，用户在前端审批后**从断点续跑**；审批与动作全程入审计日志。
- **G4 全链路可观测**：每次运行有 trace_id，节点/工具/LLM/检索 span 全落库，管理端可看 trace 树；统计任务成功率、P50/P95 延迟、token 成本、重试率。
- **G5 评测回归**：新增端到端黄金任务集 `eval_agent_e2e`，改图/改提示词后可回归，进 CI 或至少进发布前检查单。

### 2.2 非目标（v4.0 明确不做）

- ❌ 不重写、不迁移现有 v1 AI 客服（AgentExecutor + 11 工具保持不动）；
- ❌ 不升级 LangChain 1.x，不破坏 `langchain <0.4` 锁定；
- ❌ 不做全栈 async 化（不引入 async SQLAlchemy Session、不改同步路由）；
- ❌ 不做自然语言自动分流 v1/v2（试点期 v2 由显式入口触发）；
- ❌ 不做长期记忆/用户画像、语音、多模态（挂 v4.1+）；
- ❌ 不接 LangSmith 云、不接外部工作流 SaaS；
- ❌ 不把模拟面试整场搬进图（面试已有成熟状态机，v4.0 只在图里"出题/创建面试场次"边缘对接）。

### 2.3 成功指标（量化，M4 结束取数）

| 指标 | 口径 | 目标值 |
|---|---|---|
| 端到端任务成功率 | completed 且通过 Verifier 的 run / 全部 run（评测集 + 灰度真实） | ≥ 85%（评测集 ≥ 90%） |
| 人工节省时长 | 一次求职准备串联 3 个功能的人工操作耗时 vs 自动耗时（实测计时） | 给出对比数据，目标 ≥ 60% |
| HITL 误执行率 | 高风险动作未经审批被执行的次数 | **0**（硬门禁） |
| Trace 覆盖率 | 有完整 span 链的 run / 全部 run | 100% |
| v1 回归 | eval_agent_routing / eval_rag 基线不下降 | 33/33、hit@1 87.8% 持平 |

---

## 3. 用户与场景

### 3.1 角色

- **求职者（主用户，含匿名）**：上传过简历，准备投递某个岗位；
- **管理员**：看 trace、排查失败 run、审计风险动作。

### 3.2 核心用户故事

- **US-1（主链路）**：作为求职者，我贴一段 JD（或口述岗位要求），点「一键求职准备」，系统自动把我最新简历的分析结论、JD 匹配度/缺口词、按该岗位出的 3~5 道题一次性给我，我不用在三个功能间来回切。
- **US-2（无分析报告兜底）**：我的简历还没做过 AI 分析时发起任务，Planner 先安排补分析再继续；分析服务失败则任务明确报错并停在该节点，不拿空报告硬凑。
- **US-3（HITL 副作用确认）**：任务结果里可以一键「按这些题开始模拟面试」，但**创建面试场次会消耗额度并产生持久数据**，图在执行前中断，弹出确认卡，我确认后续跑、取消则不产生任何数据。
- **US-4（失败可见）**：某个节点重试 2 次仍失败时，前端步骤条标红该节点并说明原因，已完成节点的产物仍可查看，可「从失败节点重试」。
- **US-5（管理员追链路）**：管理员在 trace 页按 trace_id 回放一次 run 经过的节点、每节点耗时/token/重试次数与输入输出摘要。

### 3.3 风险动作清单（HITL 适用范围，试点）

| key | 动作 | 为什么要人审 |
|---|---|---|
| `create_interview_session` | 由任务结果自动创建模拟面试场次 | 产生持久数据 + 消耗面试额度 |
| `overwrite_analysis` | 用本次新分析覆盖/顶替当前有效报告版本 | 影响既有结论，旧版本需可追溯 |
| `hard_delete_resume` | 硬删除简历及关联产物（超出软删） | 不可逆，涉及隐私数据销毁 |

> 不在清单内的只读/纯生成动作（匹配、出题、读报告）不中断。清单写在配置/代码常量中，新增风险动作必须同步加审计与测试。

---

## 4. 总体方案

### 4.1 并存架构

```
前端 AI 客服页
  ├─ 普通提问 ──► POST /api/agent/ask        ──► v1：AgentExecutor + 11 工具（不动）
  └─ 一键求职准备 ─► POST /api/agent-v2/runs ──► v2：LangGraph StateGraph（新增）
                                                  ├─ 节点复用下沉后的 service 能力
                                                  ├─ PostgresSaver（checkpoint，中断续跑）
                                                  └─ agent_runs / agent_spans / agent_approvals
```

- v1 与 v2 **共用**归属体系、usage_logs、chat_sessions/chat_messages、混合检索、模型配置；
- v2 的最终交付物写一条 `chat_messages`（session_type='agent'，tool_steps 存节点过程），会话历史天然可见；
- 依赖方向不变：Router → Service → Model；图节点只调 service，**不 import api 层、不直接复用 @tool 闭包**。

### 4.2 依赖与配置（M0 定版）

- requirements 新增：`langgraph`、`langgraph-checkpoint-postgres`（同步 saver）；**候选 0.2.x 稳定线（与 langchain-core 0.3 兼容），具体版本以 M0 实测为准**，装完必须重新生成 `backend/requirements.lock`；
- 新增配置（`core/config.py`，全部给默认值，可经 .env 覆盖）：

| 配置 | 默认 | 说明 |
|---|---|---|
| `agent_v2_enabled` | true | 总开关，异常时可一键关 v2 回退 v1 |
| `daily_agent_v2_run_limit` | 10 | 每归属者每日 v2 运行次数（一次 run 计 1 次，与 30 次 v1 分开） |
| `agent_v2_max_retries_per_node` | 2 | Verifier 回环每节点最大重试 |
| `agent_approval_ttl_minutes` | 30 | 审批单超时，超时自动 expired |
| `langsmith_enabled` | false | 强制默认关闭 |

### 4.3 图设计（job_prep_pipeline）

**State（TypedDict，单次运行的共享状态）**：

| 字段 | 说明 |
|---|---|
| `resume_hint / jd_text / topic / position_type` | 用户输入 |
| `plan` | Planner 产出的节点计划（结构化 JSON，前端步骤条数据源） |
| `resume_id / resume_filename` | 定位到的本人简历 |
| `analysis_report` | 六维分析结论（读已有或新生成） |
| `match_report` | 匹配度/命中词/缺口词/建议 |
| `questions` | 3~5 道岗位题 + 出题依据 |
| `verifier_result` | 各产物校验结论与失败原因 |
| `retry_counts` | dict[节点名 → 已重试次数] |
| `pending_action` | 待审批的风险动作（HITL 载荷） |
| `citations / spans_meta / tokens` | 引用来源、span 累计、token 累计 |
| `messages` | 最终汇总用消息（经裁剪，不塞原始工具返回） |

**节点与边**：

```
START
 → planner（意图确认 + 计划生成；非求职准备意图直接结束并提示走 v1）
 → load_resume（定位本人最新简历；无简历 → fail 节点给引导话术）
 → need_analysis?（条件边：有 valid 分析则跳过）
     └─ analyzer（复用分析链路，chat_json 校验 + 版本号）
 → matcher（JD 匹配，复用 job_match 核心逻辑）
 → questioner（先 RAG 检索再出题，复用 question_gen 核心逻辑）
 → verifier（schema/引用/一致性校验）
     ├─ 不通过且未超限 → 条件边回到对应节点（Recovery 回环）
     └─ 超限 → fail（错误事件 + 保留已完成产物）
 → hitl_gate（计划含风险动作？）
     ├─ 是 → interrupt（挂起 waiting_approval；审批后 Command(resume)）
     └─ 否 ↓
 → deliver（汇总，逐字 delta 流式，落 chat_messages / agent_runs / usage）
 → END
```

**节点 → 现有能力复用映射**（M1 先下沉、后建图）：

| 图节点 | 复用的现有实现 | v4.0 动作 |
|---|---|---|
| planner | 新增（轻量 chat_json，输出计划 JSON，带独立 PROMPT 版本常量） | 新建 |
| load_resume | tools.resume_lookup 的查询部分 | 下沉 service |
| analyzer | `analysis_service` + `latest_valid_analysis` + prompts | 直接调 service |
| matcher | tools.job_match 闭包内逻辑（含 `_run_tool_llm` 限额记账） | **下沉为 service，v1 工具改为调同一函数** |
| questioner | tools.question_gen（含 `_kb_retrieve`） | 同上 |
| verifier | 新增（校验 JobMatchReport/QuestionGenReport/Analysis 结构 + 引用非空 + 题目数 3~5） | 新建 |
| hitl_gate | LangGraph interrupt + approvals 表 | 新建 |
| deliver | 复用 v1 的落库/记账/SSE 收尾模式 | 新建 |

> 下沉原则沿用 docs/Agent工具设计.md §三：**接口、v1 工具、v2 节点三方调同一份 service 函数，不复制 SQL / 提示词**；下沉是行为不变的重构，由现有测试 + 路由评测护航。

### 4.4 同步栈与流式适配

- 每个 run 一个 `langgraph_thread_id`（= run 维度），checkpointer 用**同步 `PostgresSaver`**，连接从独立 SQLAlchemy 引擎获取（不与请求级 Session 互持事务，避免 checkpoint 与业务写互相锁等待）；
- 沿用 v1 成熟模型：**后台 daemon 线程跑 `graph.stream(input, config)`，主生成器从 queue.Queue 取事件转 SSE**；线程结束（含 interrupt 挂起）后才允许用请求级 Session 落库（与 executor.py 现有的哨兵消费约定一致）；
- graph.stream 的节点事件在适配器里转成 SSE（见 §7.3），**对前端尽量复用 v1 协议**，新增事件只加不改。

### 4.5 HITL 中断与续跑

1. 节点调用 `interrupt(payload)` 时图暂停，checkpointer 持久化全部 state，run 置 `waiting_approval`，生成 `agent_approvals(pending)`；
2. SSE 发 `approval_required` 后**正常关闭本次流**（不是断连报错）；
3. 前端展示审批卡（动作说明 + 影响 + 批准/取消）；
4. 用户批准 → `POST .../approve` → 后端 `graph.invoke(Command(resume={decision}), {thread_id})`，重新打开 SSE 续跑；拒绝 → `Command(resume={rejected})`，图走 deliver（跳过副作用并说明）或 END；
5. TTL 超时未审批 → 审批单 expired，run 置 `aborted`；再次请求提示重新发起；
6. 中断期间允许用户离开页面：run 状态与 checkpoint 在库，随时可从 run 详情回到审批卡。

### 4.6 Trace 与上下文工程

- **Trace**：写一个 LangGraph 同步回调（或节点包装器），在节点/工具/LLM/检索四个粒度写 span（见 §6 表）；span 的 input/output 只存 ≤200 字预览，**不存简历正文**；
- **上下文工程（v4.0 范围）**：State 中各节点产物以「结构化摘要 + 必要原文片段」形式传递（沿用 300 字/块、报告 800 字等现有截断常量，matcher/questioner 的提示词入参仍受 4000/6000/2000 字上限保护）；deliver 只拿最终三份摘要，不回溯中间原始返回；
- 长期记忆（用户画像、跨 run 偏好）明确挂 v4.1，本版不建。

---

## 5. 功能需求与验收标准

| 编号 | 需求 | 验收标准（AC） |
|---|---|---|
| FR-1 | M0 版本兼容验证 | 输出兼容性结论文档；最小 2 节点图 + 同步 PostgresSaver + interrupt/resume 脚本跑通；requirements.lock 更新；不兼容则触发 §14 回退 |
| FR-2 | 工具逻辑下沉 service | matcher/questioner 核心逻辑迁出 tools.py；v1 11 工具行为不变，全量 pytest + routing 33/33 + RAG 基线不回归 |
| FR-3 | agent_runs 运行实例 | 发起 run 即建实例，状态机准确流转；列表/详情按归属隔离（owner_clause），越权访问 404 |
| FR-4 | Planner 节点 | 输出 plan JSON（节点序列 + 是否含风险动作）；非求职准备意图安全结束并引导 v1；输出非法走 Verifier 式重试 ≤2 次 |
| FR-5 | 主链路三节点 | 有报告：load→match→question 一次跑通；无报告：自动插 analyzer；产物字段完整（匹配分/命中/缺口/建议；3~5 题含依据） |
| FR-6 | Verifier + 回环 | 人为构造坏输出（缺字段/题目数不对/引用为空）时回环重试，第 3 次失败走 fail，前端标红且保留已完成产物 |
| FR-7 | HITL 审批 | 风险动作必产生 pending 审批；未批准前库中无面试场次/覆盖/删除（用例断言）；批准后续跑成功，拒绝/超时不产生副作用 |
| FR-8 | 断点续跑 | interrupt 后关闭浏览器再打开，run 详情可恢复审批卡；批准后从 hitl_gate 之后继续，前置节点不重复执行、不重复扣额度 |
| FR-9 | SSE 流式 | 节点开始/结束、重试、delta、审批、done 事件顺序正确；切会话/关窗 abort 旧流（沿用快照守卫）；断开路径补 0-token 账 |
| FR-10 | Trace 落库 | 每次 run 的节点/LLM/检索 span 完整（Trace 覆盖率 100%）；span 预览无简历正文与密钥；管理端可看 trace 树 |
| FR-11 | 指标统计 | 管理端展示成功率、P50/P95 延迟、token、重试率（近 7 日）；口径写进文档 |
| FR-12 | 端到端评测 | `scripts/eval_agent_e2e.py` + `data/agent_eval/e2e.json`（≥ 10 个黄金任务，覆盖正常/无报告/坏输出重试/审批/拒绝）；LLM 不可用以退出码 2 中止且不写报告（沿用现有评测脚本约定） |
| FR-13 | 限额与记账 | run 计 `agent_v2_run`（10/日），节点内 LLM 仍计 `agent_tool_llm`（20/日）；并发突破由现有限额咨询锁拦截；失败不记 LLM 账 |
| FR-14 | 开关与降级 | `agent_v2_enabled=false` 时入口隐藏、接口 503 并提示用 v1；v2 全局异常不影响 v1 问答 |
| FR-15 | 前端任务流 | 步骤条（plan 节点状态：等待/运行/重试/完成/失败）、审批卡、结果分区（分析/匹配/题目+引用）、失败节点「重试」按钮 |

---

## 6. 数据模型变更（走 Alembic，禁止手改表）

新增 4 张表，均带 `id`、`created_at`，业务表带可空 `user_id` + `anonymous_id`（归属口径同全项目）。

### agent_runs（图运行实例）

| 字段 | 说明 |
|---|---|
| id / user_id / anonymous_id | 主键 / 归属 |
| trace_id | UUID，贯穿全部 span |
| thread_id | LangGraph checkpoint thread 键 |
| session_id | 可空，绑定的 agent 类型 chat_session |
| run_type | `job_prep_pipeline`（预留扩展） |
| input_json | 归一化后的用户输入 |
| plan_json | Planner 计划 |
| status | planning / running / waiting_approval / verifying / completed / failed / aborted / rejected |
| current_node / output_json / error | 当前节点 / 最终产物 / 失败原因（面向用户话术） |
| iterations / tokens_total / duration_ms | 迭代次数 / token / 耗时 |
| approved_by / approved_at | 审批信息（单审批节点场景） |
| updated_at / completed_at |  |

索引：归属 + created_at；trace_id 唯一；status 部分索引（waiting_approval 扫描用）。

### agent_spans（调用链）

| 字段 | 说明 |
|---|---|
| id / trace_id / run_id | 关联 |
| parent_span_id | 层级（节点 → 其内部 LLM/检索） |
| span_type | agent_node / llm / retrieval / tool |
| name | planner / matcher / kb_search 等 |
| status | ok / error / retried |
| attempt | 第几次尝试（回环观测） |
| input_preview / output_preview | **≤200 字，禁止简历正文/密钥** |
| tokens_prompt / tokens_completion / duration_ms / error_type |  |

### agent_approvals（审批单）

| 字段 | 说明 |
|---|---|
| id / run_id / trace_id / 归属 |  |
| action_key | create_interview_session / overwrite_analysis / hard_delete_resume |
| payload_json | 动作参数快照（续跑输入） |
| status | pending / approved / rejected / expired |
| decided_by / decided_at / decision_note |  |
| expires_at | created_at + TTL |

### audit_logs（审计，标准⑥）

| 字段 | 说明 |
|---|---|
| id / actor_user_id / actor_anonymous_id / ip | 谁 |
| action / target_type / target_id | 做了什么、作用对象 |
| before_snapshot / after_snapshot | 变更摘要（软删/覆盖类动作，不放正文全文） |
| run_id / trace_id | 来源链路 |

> 审批过期清理：复用 Celery beat 周期任务扫 `expires_at`（若当前无 beat，则在查询审批时惰性置 expired，并在 PRD 实施时确认任务挂载方式）。

---

## 7. API 设计（新增 `api/agent_v2.py`，前缀 /api/agent-v2，不动 v1）

| 方法/路径 | 作用 |
|---|---|
| POST `/runs` | 发起运行；body `{resume_hint?, jd_text, topic?, position_type?, session_id?}`；**直接返回 SSE 流**，meta 带 run_id/thread_id/plan |
| GET `/runs` | 本人 run 列表（归属隔离，分页） |
| GET `/runs/{id}` | run 详情（状态、plan、各节点产物、待审批信息） |
| GET `/runs/{id}/stream` | SSE 重连：续跑/刷新后订阅该 run 的后续事件（waiting_approval 时立即推 approval_required） |
| POST `/runs/{id}/approve` | body `{decision: approved\|rejected, note?}`；校验归属与 pending/TTL，触发 `Command(resume)`，返回 202 并由前端转 `/stream` 续听 |
| POST `/runs/{id}/retry-node` | fail 后从失败节点重试图（retry 预算重置，计新 run 额度） |
| POST `/runs/{id}/abort` | 主动放弃（run=aborted，审批单 expired） |
| GET 管理端 `/api/admin/agent-runs`、`/runs/{id}/spans` | run 检索 + trace 树（admin only） |

### 7.3 SSE 事件协议（v2）

```
meta{run_id, thread_id, plan[]}
node_start{node, attempt}                 # 步骤条运转
node_end{node, status, preview, tokens, duration_ms}
action{tool,input} / observation{preview} # 节点内部调检索等工具时（沿用 v1 形态，可选）
approval_required{approval_id, action_key, summary, expires_at}   # 图中断，随后正常关流
delta{content}                            # deliver 汇总逐字
done{run_id, status, output, citations[], tokens_total, message_id}
error{content} / fatal{content}
```

前端协议处理复用 v1 的 reset/快照/abort 约定；`approval_required` 是唯一新状态机。

---

## 8. 前端需求

- AI 客服页新增显式入口「🚀 一键求职准备」（JD 输入 + 岗位难度选择），普通对话框不变；
- 新组件 `components/agent/AgentRunView.vue`（或在 AgentChatCore 内嵌 run 模式）：
  - 计划步骤条：节点图标随 node_start/end/retry 变状态，失败可点看重试原因；
  - 审批卡：动作影响说明 + 批准/取消 + 倒计时（TTL）；
  - 结果分区：分析结论 / JD 匹配（命中词、缺口词）/ 面试题（出题依据文档）+ 引用折叠；
  - 中断恢复：进页面检测 waiting_approval 的 run，提示继续；
- 必须实现 v3.7 同款**会话快照守卫 + abort 句柄**；SSE 解析复用现有 streamChat；
- 管理端新增 trace 查看（run 列表 → span 树 + 耗时/token），不做精美仪表盘，表格 + 折叠即可。

---

## 9. 异常与失败恢复矩阵

| 故障 | 行为 |
|---|---|
| Planner/节点 LLM 超时、5xx | 该节点重试（预算内），span 记 error/retried；超限 → fail 节点 |
| schema 校验失败 | Verifier 回环到产出节点并带回失败原因（纠错式重试，沿用 handle_parsing_errors 思路） |
| 混合检索/Ollama 不可用 | questioner 退回模型出题并显式标注「不来自平台知识库」（现有口径保留） |
| 无简历 / 简历无正文 | load_resume 直接 fail，引导上传，不调用任何模型（不耗额度） |
| 审批超时 | 审批单 expired、run aborted，无副作用 |
| 审批时图 checkpoint 丢失/版本不兼容 | 报错并引导重新发起，不尝试静默重放 |
| checkpointer 库不可用 | v2 整体 503（DB 异常统一走全局 503），v1 不受影响 |
| 客户端断连 | 后台跑到安全点；已产生 LLM 消耗如实记账；续跑靠 checkpoint 不重放节点 |
| 节点内工具 LLM 达 20/日 | 现有 `_TOOL_LLM_LIMIT_REPLY` 话术进入 state，Verifier 判定该产物缺失并 fail，提示次日恢复 |

---

## 10. 安全、权限与合规

1. **归属隔离**：runs/approvals/spans 查询一律走 `deps.owner_clause` / `matches_owner`（登录 user_id；匿名 user_id 为空且 anonymous_id 相等），管理端接口 admin only；
2. **限额双轨**：`agent_v2_run` 10/日（主流程）+ `agent_tool_llm` 20/日（节点内模型调用），并发用现有咨询锁；
3. **HITL 硬门禁**：风险动作的实际执行函数必须校验存在 approved 审批单，审批与执行在同一归属校验下；测试断言「无审批 → 动作零副作用」；
4. **隐私**：span/日志不记简历正文、JD 全文、Key；审计快照只记摘要；上传页隐私声明已有，v2 首次使用处补一句「任务过程会调用第三方大模型」；
5. **提示词版本**：planner/verifier 等新提示词各自独立版本常量（`AGENT_V2_*_PROMPT_VERSION`），**不动 analyses 口径的 PROMPT_VERSION**；
6. **LangSmith 默认关闭**，配置项 + 代码双保险；CI 中断言不传敏感 header。

---

## 11. 可观测与评测

- 指标口径（M4 入管理端）：
  - 任务成功率 = completed / 全部 run（剔除用户主动 aborted）；
  - 延迟 = run duration_ms 的 P50/P95；成本 = tokens_total 汇总；重试率 = retried span / 总 span；
  - 审批相关：审批率、批准率、平均审批时长、超时率；
- 离线评测：`data/agent_eval/e2e.json` ≥ 10 个黄金任务（正常链路 / 无报告补分析 / 坏输出回环 / 审批通过 / 审批拒绝 / 审批超时 / 无简历 / 检索不可用降级 / 限额命中 / 越权访问）；
- 回归纪律（沿用 v3.5 约定）：改图结构/节点提示词 → 跑 eval_agent_e2e；改工具描述 → 跑 eval_agent_routing；改检索 → 跑 eval_rag；三份报告入发布检查单；
- 报告写 `data/agent_eval/e2e_report.md`，LLM 不可用以退出码 2 中止且不写报告。

---

## 12. 里程碑与验收（每阶段 ruff + 全量 pytest + 评测回归 + tag）

| 阶段 | 内容 | 验收（出口条件） |
|---|---|---|
| **M0 版本门控** | 分支 `feature/v4-langgraph`；装依赖；2 节点 demo + 同步 PostgresSaver + interrupt/resume 脚本；核实版本矩阵；更新 lock | demo 脚本可跑通中断/续跑；兼容性结论写入 PROGRESS；不兼容 → 评审 §14 回退 |
| **M1 能力下沉** | job_match/question_gen（含 _kb_retrieve 调用部分）下沉 service；v1 工具改调 service | 行为零变化；pytest 全绿；routing 33/33、RAG 基线持平；ruff format 干净 |
| **M2 图骨架** | 4 张表 + Alembic；StateGraph（planner/load/analyzer?/matcher/questioner/verifier/deliver/fail）；/runs + SSE；步骤条前端 | 黄金任务正常链路端到端跑通；坏输出回环可复现；trace span 落库 |
| **M3 HITL** | approvals/audit；interrupt + approve/reject + TTL；/stream 重连；审批卡前端 | FR-7/FR-8 全部用例通过；无审批零副作用断言通过；刷新/关窗续跑实测 |
| **M4 观测与评测** | 管理端 trace 树 + 指标；eval_agent_e2e；文档（README/AGENTS/工具设计/后续规划）升版 | §2.3 指标达标；评测 ≥90%；v1 两基线不回归；打 tag v4.0.0，更新 PROJECT-PLAN |

> 每个 M 阶段独立提交、独立可回滚；M2 结束前 v2 入口不对匿名以外的真实使用开放（可挂 feature flag 灰度）。

---

## 13. 对照六大标准的达标自查（交付时逐项勾）

- [ ] 一句话说清真实用户/痛点/指标（US-1 + §2.3 指标）
- [ ] API、DB、权限、异常、部署完整（§6/§7/§9/§10，复用现有工程底座）
- [ ] 规划（Planner）、失败恢复（回环）、结果验证（Verifier）三项齐备
- [ ] 多 Agent 拆分有业务必要性：去掉 matcher/questioner/verifier 任一个，主链路任务即不完整
- [ ] 上下文按节点按需供给，超长有截断/摘要策略（§4.6）
- [ ] 调用链可回放，成功率/延迟/成本可统计（FR-10/FR-11）
- [ ] 评测集回归驱动迭代（FR-12）
- [ ] 风险动作人工确认 + 审计日志 + 可回滚（FR-7/§10.3）

---

## 14. 风险与备选

| 风险 | 等级 | 对策 |
|---|---|---|
| LangGraph 与 langchain-core 0.3 无兼容版本 | 高 | M0 即决策门；回退方案：Celery/Redis + agent_runs 状态机自研编排（状态字段本 PRD 已按无关框架设计，可平移），HITL 用 pending_approval 状态 + 恢复接口实现 |
| 同步 PostgresSaver 与请求级 Session 事务互锁 | 中 | checkpointer 独立引擎/连接；M0 demo 专门压测中断续跑并发场景 |
| v1/v2 双轨维护成本 | 中 | 能力单点下沉 service；v4.0 不扩第二条图；v4.0 收口后按数据决定 v1 是否迁入图 |
| 框架升级绑架（LangGraph 迭代快） | 中 | pin 版本 + lock；图定义薄、业务在 service，框架可替换 |
| 为拆而拆、面试被追问答不上必要性 | 中 | §13 第 4 条自查；主链路每节点不可替代性要能用评测数据说明 |
| 节点内多次 LLM 调用推高成本/延迟 | 中 | 限额双轨、P95 监控、analyzer 节点优先读已有报告避免重复生成 |

---

## 15. 待 owner 拍板的未决问题

1. **版本**：M0 实测的 langgraph / checkpoint-postgres 具体 pin 版本（M0 后回填本文件）；
2. **入口形态**：AI 客服页内嵌「一键求职准备」卡片，还是独立菜单项？（PRD 默认内嵌）
3. **HITL 清单**：§3.3 三个风险动作是否都做，还是 M3 只做 `create_interview_session` 一个？
4. **v2 限额**：`daily_agent_v2_run_limit=10` 是否合适（一次 run 约含 3~5 次 LLM 调用）；
5. **匿名用户**：v2 是否允许匿名（HITL 审批归属依赖 anonymous_id，技术可行；但 run 产物跨设备不可见，体验差）——默认允许、登录引导；
6. **管理端 trace 范围**：M4 是否包含 span 树页面，还是先只落库 + JSON 查看。

---

> 定稿后动作：更新 PROJECT-PLAN.md（v4.0 章节）、AGENTS.md（技术栈加入 langgraph、修订"不引 langgraph"约定并注明 M0 条件）、后续开发规划.md（批次重排），再按 M0→M4 拆实施计划。
