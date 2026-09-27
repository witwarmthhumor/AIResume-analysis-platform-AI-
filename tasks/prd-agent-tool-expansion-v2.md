# PRD: Agent 工具再扩展（11 → 13 个 + 两处参数增强）

> **合并稿**：本 PRD 已并入 `tasks/prd-合并稿-重构计划与Agent工具扩展.md` 的 **Part 2**（该稿内编号写作 `US-101 ~ US-107`）。本文件仍是**本批次权威原稿**，改动请改这里再重新装订。

> 编制日期：2026-09-27 · 状态：**待评审**
> 关联规划：[docs/后续开发规划.md](../docs/后续开发规划.md) §4 · v3.10（本 PRD 是该批次的执行规格）
> 前序 PRD：`tasks/prd-agent-tool-expansion.md`（4 → 11 个，已完成并封版于 v3.5/v3.6）
> 关联设计文档：[docs/Agent工具设计.md](../docs/Agent工具设计.md)（§二 工具对照、§四 描述互斥规范、§六 工具内 LLM 限额）

---

## 1. Introduction

平台的 AI 客服（Agent）目前有 **11 个工具**，但它们全部只查**业务数据**——简历、面试结果与评分、用量、知识库。谁都无法回答两类最自然的用户问法：

1. **「我之前问过你什么来着？」**——现有没有任何工具能检索历史对话，用户想找旧对话只能手动翻 UI。
2. **「上次面试官问了什么？我怎么答的？」**——现有 `interview_history` 只给元数据与评分，**对话原文拿不到**。

本批次补上这两块记忆盲区，新增 2 个工具（`conversation_search`、`interview_transcript`，**均为纯查库、零 LLM 调用**），并给 2 个现有工具加可选参数（`kb_search.document`、`usage_stats.action`）。参数增强不动工具数量，比新增更便宜。

**交付边界**：只改后端 Agent 工具层与其文档/评测基线，**不动前端**（工具返回的文本直接走现有聊天流展示）。

---

## 2. Goals

- 新增 `conversation_search`：能按关键词在**当前用户自己的**历史会话里定位命中片段，并标注来源（在线对话 / AI 客服）。
- 新增 `interview_transcript`：能取回**当前用户自己的**某场（或最近一场）模拟面试的**一问一答原文**。
- `kb_search` 支持限定在某篇文档内检索；`usage_stats` 支持按动作类型过滤。
- 两项新增工具**不产生任何 LLM 调用**，不占用 `daily_agent_tool_llm_limit`，不改动现有记账口径。
- 工具数从 11 → 13，**路由评测基线同步扩容**（新增 ≥6 题），top-1 准确率不低于现有 33/33 口径。
- 归属隔离零缺陷：跨用户、跨匿名身份一律查不到（含"不泄露资源是否存在"的文案要求）。

---

## 3. User Stories

### US-001: 新增 `conversation_search` 工具

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

### US-002: 新增 `interview_transcript` 工具

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

### US-003: `kb_search` 支持限定文档检索

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

### US-004: `usage_stats` 支持按动作过滤

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

### US-005: 工具清单、断言测试与设计文档同步

**Description:** 作为后续维护者，我要保证「工具数量」在代码、测试、文档三处口径一致，这样不会出现"文档写 11 个实际 13 个"的失真。

**Acceptance Criteria:**
- [ ] `backend/tests/test_agent_tools.py::test_make_tools_exposes_eleven_tools` 改名为 `test_make_tools_exposes_thirteen_tools` 并断言 13 个工具名（含两个新工具名）
- [ ] `tools.py` 文件头「工具清单」注释更新为 13 条（12/13 号为新工具）
- [ ] `docs/Agent工具设计.md`：§一/§二 补两个新工具行（标注已实现），§四 的易撞组合表补 `interview_history ↔ interview_transcript`、`platform_help ↔ conversation_search` 两对
- [ ] 新增常量集中在既有 `_TOOL_*` 常量区，命名与现有风格一致
- [ ] 全量 `pytest` 全绿（不依赖本地 `.env`；AI 调用一律 mock，不烧额度）

### US-006: 路由评测集扩容并复跑基线

**Description:** 作为 owner，我要在工具数变化后拿到可对比的路由准确率，这样才知道新增工具没有把路由搞乱。

**Acceptance Criteria:**
- [ ] `data/agent_eval/routing.json` 新增 ≥6 条用例：`conversation_search` ≥3 条（含"我之前问过你…""上次聊到哪了"这类**中文口语化**问法）、`interview_transcript` ≥2 条、`kb_search`（限定文档）≥1 条
- [ ] 用例的 `expected_tool` 与最终 docstring 口径一致；易撞对（`conversation_search` vs `platform_help`、`interview_transcript` vs `interview_history`）各至少 1 条互相区分的用例
- [ ] 跑 `python -m scripts.eval_agent_routing` 生成新 `data/agent_eval/report.md`，用例集统计显示 **13 个工具 / ≥39 题**
- [ ] top-1 准确率不低于改造前口径（改造前 33/33 = 100%）；如有误选，逐题定位是描述问题还是用例问题并修正
- [ ] 报告保留"与旧基线对比"行（脚本已有该能力，确认未失联）

### US-007: 对外文档口径升版

**Description:** 作为读者，我要在 README 与技术文档上看到 13 工具的正确口径，这样对外描述不滞后。

**Acceptance Criteria:**
- [ ] `README.md` 工具数与工具清单更新到 13 个；如提及评测基线，数字与 `data/agent_eval/report.md` 一致
- [ ] `docs/tech-stack-and-features.md` 同步更新（工具数、评测口径）
- [ ] `docs/后续开发规划.md` v3.10 各任务行标记完成状态
- [ ] `grep -rn "11 个工具" README.md docs/` 不再命中旧口径（历史版本记录段除外，如 PROGRESS.md 的历史条目）
- [ ] 提示词版本判定有结论：本批次只改**工具描述**，不涉及 `PROMPT_VERSION` / `JOB_MATCH_PROMPT_VERSION` / `QUESTION_GEN_PROMPT_VERSION` / `ANSWER_REVIEW_PROMPT_VERSION`，**无需递增**（在 PR 说明里写明该判定）

---

## 4. Functional Requirements

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

## 5. Non-Goals (Out of Scope)

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

## 6. Design Considerations

- **工具描述是唯一的"路由说明"**：所有新增 docstring 必须按「做什么 → 什么时候用 → 什么时候不用」三段写（见 `docs/Agent工具设计.md` §四），易撞工具之间要在"什么时候不用"里**互相点名**。
- **输出文案面向模型而非人**：段落短、结构清晰、带来源标签；中文标签沿用现有风格（`【简历】`、`【来源：…】`）。
- **空态与异常态都要给"下一步建议"**：例如"可让用户换关键词""可提示用户先上传简历"——这是现有工具的既有范式，保持一致性。
- **复用既有组件**：`_owner_filter()`、`_snippet()`、`_ACTION_LABELS`、`_TOOL_*` 常量区、异常兜底范式。
- **本批次无 UI 改动**：故不含"用 dev-browser 验收"类条目；如需人工确认，走真机在 AI 客服页提问冒烟（见 §8）。

---

## 7. Technical Considerations

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

## 8. Success Metrics

- 新增工具后路由评测：13 工具口径 top-1 准确率 **≥ 100%（33/33 口径不下降）**，报告含新旧对比行。
- 人工真机冒烟：在 AI 客服页用 ≥3 条口语化问法（"我之前问过你怎么配置 JWT 来着""上次聊 RAG 优化聊到哪了""上次面试官问了我啥"）能选出正确工具并给出可用回答。
- 隔离类单测全绿：跨用户、跨匿名、指定他人 `session_id` 三类用例 100% 覆盖且全部通过。
- 工具数口径一致：`grep` 四处（代码注释 / 测试断言 / 设计文档 / README）均为 13，零残留"11 个工具"。
- 无新增 LLM 调用：`usage_logs` 中 `agent_tool_llm` 记录不因本批次新增（新工具为纯查库）。

---

## 9. Open Questions

1. **ILIKE 中文召回的触发阈值**：定"人工 10 条真实问法命中率 < 60% → 启动 jieba/pg_trgm 优化"这条判据可以吗？还是你更想一开始就上分词？
2. **`usage_stats.action` 是否接受中文**（如 `action="模拟面试"`）？建议只收英文枚举，中文由 `_ACTION_LABELS` 反向映射兜底——是否同意？
3. **`conversation_search` 要不要搜 AI 客服的 `tool_steps` 文本**（即"我上次让它查过什么"）？建议**不搜**（避免过程噪声污染命中片段）。
4. **评测集新增用例由谁写**：建议实现者随手写（含 3 条口语化问法），比事后补写更贴合真实 docstring 措辞——是否同意？
5. **本批次排期**：放在 v3.8（本地一键演示模式）**之后**，还是与 v3.8 并行（改动文件不重叠）？
