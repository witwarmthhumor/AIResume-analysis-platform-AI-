# PRD: Agent 工具扩展（4 → 11 个）

> 本文件是需求文档，不包含实现。实施前请先读 `docs/Agent工具设计.md`（原实现 → 工具封装的对照）。

## 1. Introduction

AI 客服 Agent（v3.4 落地、v3.5 扩到 4 个工具）目前只有 `kb_search` / `resume_lookup` / `interview_history` / `usage_stats`。

前置结论（2026-09-16 讨论）：**工具不足 10 个时拆多 Agent 是过度工程** —— 每加一层路由就多一次 LLM 调用，而子 Agent 各自只有 1~2 个工具，等于纯开销。正确路径是**先把单 Agent 的工具扩到 10+**，用真实数据回答"模型会不会选错工具"，再决定是否拆。

本 PRD 描述扩到 **11 个工具**的完整需求，实施分两批：

- **批次 1（4 个，零新增 LLM 调用）**：`analysis_read` / `score_trend` / `kb_list` / `platform_help` —— 纯读库或静态文案，风险最低
- **批次 2（3 个，工具内部调 LLM）**：`job_match` / `question_gen` / `answer_review` —— 需独立限额与记账

---

## 2. Goals

- 工具从 4 个扩到 11 个，全部具备归属隔离
- 批次 1 不新增任何 LLM 调用，可独立验收上线
- 批次 2 的三个工具具备**独立的限额口径**（`daily_agent_tool_llm_limit`）与**独立的记账类型**（`agent_tool_llm`），不污染现有 `daily_agent_limit`
- 建立**工具路由评测集**，量化"模型选对工具的准确率"，作为将来判断是否拆多 Agent 的客观依据
- 工具描述（docstring）具备互斥性，避免扩到 10+ 后互相抢调用

---

## 3. User Stories

### 批次 1 · 零新增 LLM 调用

#### US-001: 共享查询逻辑下沉到 service 层

**Description:** 作为开发者，我需要把分析报告的查询逻辑从 API 层挪到 service 层，这样工具（位于 services 层）不必反向依赖 `app/api/` 模块。

**背景：** `_latest_valid_analysis` 目前是 `backend/app/api/analyses.py` 的私有函数。工具层要复用它，但项目约定是 `Router → Service → Model` 单向依赖，service 不能 import api。

**Acceptance Criteria:**
- [ ] 在 `backend/app/services/analysis_service.py` 新增公开函数 `latest_valid_analysis(db, resume_id)`，筛选条件与原实现完全一致（`valid_json is True` + `prompt_version == PROMPT_VERSION` + 按 `created_at desc, id desc` 取第一条）
- [ ] `backend/app/api/analyses.py` 删除私有 `_latest_valid_analysis`，改为调用新的 service 函数
- [ ] 现有 `backend/tests/test_analyses.py` 全部通过（无行为变化）
- [ ] `ruff check .` 通过

#### US-002: 新增 `analysis_read` 工具

**Description:** 作为用户，我想让 AI 客服读出我某份简历的 AI 分析结论，这样我不用自己翻报告页。

**Acceptance Criteria:**
- [ ] 新增 `analysis_read(resume_hint: str) -> str`，加入 `make_tools` 返回值
- [ ] 无身份（`user_id` 与 `anonymous_id` 均为空）→ 返回"当前会话无法识别用户身份…"
- [ ] 有身份但无简历 → 返回"该用户名下没有已上传的简历…"
- [ ] 有简历但该简历无有效分析 → 明确说明"该简历还没有分析报告"，不编造内容
- [ ] `resume_hint` 为空字符串 → 取最近上传的一份简历
- [ ] `resume_hint` 非空 → 按文件名包含匹配；无匹配则说明未找到并列出实际文件名
- [ ] 命中时输出六块内容：目标岗位、岗位匹配、优势、短板、关键词缺口、改进建议、预测面试题
- [ ] **单次输出总长 ≤ 800 字符**，预测面试题最多列 5 条
- [ ] 查询过程抛异常 → 返回自然语言兜底（"查询暂时出错，请稍后再试"），不向上抛
- [ ] 单测覆盖上述 6 条分支路径

#### US-003: 新增 `score_trend` 工具

**Description:** 作为用户，我想知道自己的面试表现是进步还是退步，这样我能判断复习有没有效果。

**Acceptance Criteria:**
- [ ] 新增 `score_trend(limit: int = 5) -> str`
- [ ] 只取本人 `status == "finished"` **且 `final_report_json` 是 dict** 的场次（注意：JSONB 列写入 Python `None` 会落成 JSON `null` 字面量，`is_not(None)` 过滤不掉，必须再判类型）
- [ ] `limit` 钳制到 `1~10`，非正数回落默认 5
- [ ] 按时间正序列出各场次四维分数（技术深度/表达结构/项目真实性/整体）
- [ ] 每场与**上一场**对比给出升降标记（↑ / ↓ / →），首场显示"基准场"
- [ ] 场次 = 0 → 返回"暂无已完成的模拟面试"
- [ ] 场次 = 1 → 返回该场分数并说明"只有一场，暂时看不出趋势"
- [ ] 单测覆盖：零场 / 单场 / 多场升降正确 / 只有 JSON null 报告时视为零场 / 归属隔离（查不到他人场次）

#### US-004: 新增 `kb_list` 工具

**Description:** 作为用户，我想知道平台知识库里有哪些资料，这样我才知道能问什么。

**Acceptance Criteria:**
- [ ] 新增 `kb_list(query: str) -> str`
- [ ] 返回当前归属者可见的文档：预置语料（`scope=public`）+ 本人上传（`scope=private`）
- [ ] 每条输出：标题、块数、状态、来源（预置/本人上传）
- [ ] **绝不包含文档正文**（`raw_text` 与切块内容都不返回）
- [ ] `query` 非空 → 按标题做关键词过滤（不区分大小写）；无匹配时说明未找到
- [ ] 最多列出 20 条，超出时说明"仅显示前 20 条"
- [ ] 单测覆盖：预置可见 / 他人私有不可见 / 空列表 / 标题过滤

#### US-005: 新增 `platform_help` 工具

**Description:** 作为用户，我想问"怎么开始模拟面试""在哪看用量"，希望 AI 客服直接答而不是去检索技术知识库。

**Acceptance Criteria:**
- [ ] 新增 `platform_help(topic: str) -> str`
- [ ] 纯静态文案，不查库、不调 LLM
- [ ] 至少覆盖 8 个主题：上传简历、AI 分析、模拟面试、在线对话、AI 客服、使用日志、个人中心、数据看板/语料库管理
- [ ] 未知或空 `topic` → 返回功能总览（一屏内讲清平台能做什么）
- [ ] **docstring 必须写清排他边界**：本平台功能使用问题用本工具；计算机技术知识点用 `kb_search`
- [ ] `kb_search` 的 docstring 同步补上反向排他句（平台功能问题请勿调用本工具）
- [ ] 单测覆盖：各主题有返回 / 空 topic 返回总览 / 未知 topic 返回总览

#### US-006: 工具描述互斥性校准

**Description:** 作为开发者，我需要每个工具的 description 都能让模型分清"什么时候不要用我"，避免工具变多后互相抢调用。

**Acceptance Criteria:**
- [ ] 全部 8 个工具（4 旧 + 4 新）的 docstring 均包含三段信息：**做什么** / **什么时候用** / **什么时候不用**
- [ ] 显式处理两对易撞组合：`kb_search` ↔ `platform_help`（技术知识点 vs 平台功能）、`resume_lookup` ↔ `analysis_read`（简历原文 vs 分析结论）
- [ ] 新增单测：断言每个工具的 `description` 长度 > 50 字符；断言 `kb_search` 与 `platform_help` 的 description 各自包含排他关键词
- [ ] 不动任何已有工具的行为逻辑（仅改 docstring）

#### US-007: 工具路由评测集与脚本（批次 1 · 8 工具口径）

**Description:** 作为开发者，我需要一份可重复运行的评测，回答"模型面对 8 个工具时选对的比例是多少"。

**Acceptance Criteria:**
- [ ] 新增 `data/agent_eval/routing.json`：≥ 24 条用例（8 工具 × 3 种问法），字段为 `{"question": "...", "expected_tool": "..."}`
- [ ] 问法要贴近真实口语（如"我简历里写过 K8s 吗"→ `resume_lookup`；"怎么上传简历"→ `platform_help`）
- [ ] 新增 `backend/scripts/eval_agent_routing.py`：对每道题跑**一次真实 LLM 调用**做工具选择，**工具执行体一律 mock**（只关心选了谁，不真正查库）
- [ ] 输出：top-1 准确率、逐题明细（问题 / 期望 / 实际 / 是否命中）、**混淆矩阵**（期望工具 × 实际选中工具）
- [ ] 结果写入 `data/agent_eval/report.md`
- [ ] LLM 不可用时（如 402 欠费）脚本**明确报错并退出**，不静默产出空报告
- [ ] 8 工具口径下 top-1 准确率 ≥ 85%

---

### 批次 2 · 工具内部调 LLM

#### US-008: 工具内 LLM 调用的独立限额与记账

**Description:** 作为开发者，我需要把"工具内部自己调 LLM"的消耗与"Agent 主循环调 LLM"分开统计和限制，否则实际可用次数会莫名腰斩且无法归因。

**Acceptance Criteria:**
- [ ] `backend/app/core/config.py` 新增 `daily_agent_tool_llm_limit: int = 20`
- [ ] 工具内 LLM 调用写 `usage_logs`，`action_type = "agent_tool_llm"`，`model_name` 记实际模型
- [ ] 超限时工具**返回自然语言提示**（"今日工具内 AI 调用已达上限，请明天再试"），**不抛异常** —— Agent 主循环必须继续可用
- [ ] 不影响现有 `daily_agent_limit`（Agent 主循环提问次数）的判定
- [ ] 复用 `usage_service.count_today_usage_by_owner` 与 `write_usage`，不新写查询
- [ ] 单测覆盖：未超限正常执行 / 恰好达上限 / 超限返回文案且不抛 / `usage_logs` 确实落了一条 `agent_tool_llm`

#### US-009: 新增 `job_match` 工具

**Description:** 作为用户，我想贴一段 JD 让 AI 告诉我匹配度如何、缺哪些关键词。

**Acceptance Criteria:**
- [ ] 新增 `job_match(jd_text: str) -> str`
- [ ] `jd_text` 截断至 4000 字符（超出部分丢弃并说明已截断）
- [ ] 复用 `ai_client.chat_json(system_prompt, user_prompt, settings, validator)`，走校验 + 失败重试（最多 2 次）
- [ ] 输出结构化结果：匹配度评分、命中的关键词、缺失的关键词、针对性建议
- [ ] 新增该提示词的 `PROMPT_VERSION` 常量并递增
- [ ] 无简历 → 明确说明"需要先上传简历"，不空跑 LLM
- [ ] `jd_text` 为空 → 提示需要提供 JD 内容
- [ ] LLM 报错（含余额不足）→ 返回可读话术，不抛异常
- [ ] 单测（全程 mock LLM）：成功路径 / JD 超长截断 / 无简历 / 空 JD / LLM 抛错

#### US-010: 新增 `question_gen` 工具

**Description:** 作为用户，我想让 AI 针对某个主题出一组面试题，并且这些题要贴合平台知识库。

**Acceptance Criteria:**
- [ ] 新增 `question_gen(topic: str, position_type: str) -> str`
- [ ] **实现路径：先调用 `kb_search` 检索平台语料，再依据检索结果出题**（不是纯模型生成）
- [ ] 检索未命中 → 明确说明"平台知识库未收录该主题"，并退回模型自身出题，同时在输出中标注"以下题目不来自平台知识库"
- [ ] `position_type` 取值 `intern` / `fresh` / `senior` / 空；非法值回落"通用"，并在输出中体现难度定位
- [ ] 单次生成 3~5 道题
- [ ] docstring 与 `kb_search` 互相划线（出题用本工具，查答案用 `kb_search`）
- [ ] 单测：检索命中路径 / 未命中回退路径 / 非法 position_type 回落

#### US-011: 新增 `answer_review` 工具

**Description:** 作为用户，我想贴一段自己的回答让 AI 点评，看看哪里能改进。

**Acceptance Criteria:**
- [ ] 新增 `answer_review(question: str, answer: str) -> str`
- [ ] `answer` 截断至 2000 字符（超出说明已截断）
- [ ] 复用四维评分口径中的三维：技术深度、表达结构、项目真实性，并给出 2~4 条改进建议
- [ ] 走 `chat_json` 结构化输出 + 校验重试
- [ ] `question` 或 `answer` 为空 → 提示需要补全，不空跑 LLM
- [ ] LLM 报错 → 话术兜底
- [ ] 单测（mock LLM）：成功 / answer 超长截断 / 参数缺失 / LLM 抛错

#### US-012: 评测集扩充到 11 工具口径

**Description:** 作为开发者，我需要在上线全部工具后重跑一次路由评测，确认扩到 11 个工具没有让准确率掉下来。

**Acceptance Criteria:**
- [ ] `data/agent_eval/routing.json` 扩到 ≥ 33 条（11 工具 × 3 问法）
- [ ] 重跑 `scripts/eval_agent_routing.py`，输出 11 工具口径的准确率与混淆矩阵
- [ ] 对比 8 工具口径的准确率，**下降不超过 5 个百分点**
- [ ] 若准确率 < 85%，报告中列出所有误选案例，并在 `docs/Agent工具设计.md` 记录结论（是否触发"该考虑多 Agent"的判断）
- [ ] 报告覆盖写入 `data/agent_eval/report.md`

#### US-013: 前端工具名中文化

**Description:** 作为用户，我在 AI 客服的"工具调用过程"里看到 `analysis_read` 这样的英文标识看不懂。

**Acceptance Criteria:**
- [ ] `frontend/src/components/agent/AgentChatCore.vue` 的工具调用过程显示中文名（如 `analysis_read` → "读取分析报告"，`kb_search` → "检索知识库"）
- [ ] 未在映射表内的工具名 → 原样显示（保证新增工具不会显示为空）
- [ ] 映射表集中定义在组件内一处常量，覆盖全部 11 个工具 + `unknown_tool` 占位
- [ ] Typecheck/lint passes（`npm run build` 通过）
- [ ] **Verify in browser using dev-browser skill**：真机发起一次会触发工具调用的提问，确认工具名显示为中文

---

## 4. Functional Requirements

- FR-1: `make_tools` 返回的工具列表包含 11 个工具，且 `kb_search` **仍排在第一位**（现有测试依赖 `tools[0]`）
- FR-2: 全部 11 个工具在无身份（`user_id` 与 `anonymous_id` 均空）时返回自然语言拒绝文案，不返回数据
- FR-3: 全部个人数据类工具按归属者过滤：登录按 `user_id`，匿名按 `user_id IS NULL AND anonymous_id = ?`
- FR-4: 全部工具的查询异常都被捕获并转为自然语言兜底，任何工具都不向 Agent 主循环抛异常
- FR-5: 需要入参的工具对 LLM 传入的非法值（空串、超大数、非法枚举）做钳制或回落，不产生 500
- FR-6: `job_match` / `answer_review` / `question_gen` 内部的 LLM 调用统一写入 `usage_logs`，`action_type="agent_tool_llm"`
- FR-7: 工具内 LLM 调用受 `daily_agent_tool_llm_limit` 约束，超限返回文案而非异常
- FR-8: 每个工具的 `description` 包含"做什么/何时用/何时不用"三段信息
- FR-9: `data/agent_eval/routing.json` 提供 ≥33 条路由评测用例，覆盖全部 11 个工具
- FR-10: `scripts/eval_agent_routing.py` 可重复运行，输出准确率 + 逐题明细 + 混淆矩阵，并写 `data/agent_eval/report.md`

---

## 5. Non-Goals（明确不做）

- **不做多 Agent / Supervisor / LangGraph** —— 本功能的目的是把工具做到 10+ 并取得量化数据，据此再判断；实现多 Agent 是后续独立决策
- 不做工具并行调用（现有 AgentExecutor 串行执行）
- 不引入任何新 Python 依赖（`langgraph` 一律不装）
- 不改现有 4 个工具的功能行为（US-006 仅允许改 docstring）
- 不做工具级细粒度权限（沿用归属过滤 + 管理端/用户端导航已做的隔离）
- 不做前端新页面（仅 US-013 的工具名映射）

---

## 6. Design Considerations

- **工具命名**：小写下划线，动词开头（`analysis_read` / `score_trend` / `kb_list` / `platform_help` / `job_match` / `question_gen` / `answer_review`）
- **输出风格统一**：与现有 4 个工具一致 —— 首行一句总述，随后条目用「【标签】内容」或「- 」；中文标点；不使用 emoji
- **前端展示**：工具中文名映射表放在 `AgentChatCore.vue` 内部常量（与 `HistoryView.vue` 的 `actionMap` 写法保持一致，都是组件内常量 + 回落原始值）
- **可复用的现有组件**：无需新增前端组件，`AgentChatCore` 已支持任意工具名的「工具调用过程」折叠区

---

## 7. Technical Considerations

| 事项 | 说明 |
|---|---|
| **层级依赖** | 工具在 `services` 层，不得 import `app/api/*`。US-001 的下沉是批次 1 的前置条件 |
| **JSONB null 陷阱** | `final_report_json` 写入 Python `None` 会落成 JSON `null` 字面量，`is_not(None)` 过滤不掉，必须再判 `isinstance(report, dict)` |
| **限额函数复用** | 用 `usage_service.count_today_usage_by_owner(db, action_type, user_id, anonymous_id)` 与 `write_usage(...)`，不新写 SQL |
| **超限不能抛异常** | 工具内部无法返回 HTTP 状态码，超限必须转成自然语言返回，否则整轮 Agent 崩 |
| **现有测试会受影响** | `tests/test_agent.py` 断言 `tools[0].name == "kb_search"`；`tests/test_agent_tools.py` 断言工具名列表 —— 新增工具后需同步更新断言 |
| **测试数据隔离** | 新测试沿用专属标记（`anonymous_id` 前缀 / `resume_id` 常量），只清自己造的数据，不要 `DELETE FROM` 全表 |
| **跑全量 pytest 的副作用** | 老 `tests/test_rag.py` fixture 会清空 `kb_chunks`/`kb_documents`，跑完需 `python -m scripts.seed_kb_preset` 恢复语料 |
| **评测依赖真实 LLM** | `eval_agent_routing.py` 测的是"模型怎么选工具"，无法 mock，必须具备可用 LLM 通道（当前 DeepSeek 欠费 402 未解决） |
| **配置新增** | `daily_agent_tool_llm_limit` 需同步写进 `backend/.env.docker.example` 与文档 |

---

## 8. Success Metrics

- 工具路由 top-1 准确率：8 工具口径 ≥ 85%，11 工具口径 ≥ 85% 且相比 8 工具下降 ≤ 5 个百分点
- 工具总数：4 → 11，全部有独立单测
- 测试规模：pytest 143 → **≥ 170**（每个新工具至少 3 条路径覆盖）
- 质量门禁：`ruff check` 全绿、`npm run build` 通过
- 真机冒烟：一次提问能触发 ≥2 个工具且选择正确，前端「工具调用过程」显示中文工具名
- 产出物：`data/agent_eval/report.md` 中的准确率数据可作为"是否需要多 Agent"的书面依据

---

## 9. Open Questions

1. `question_gen` 检索未命中时，是明确告知用户"这部分不来自知识库"，还是静默退回模型出题？（当前 PRD 采用前者）
2. `score_trend` 的"趋势"是否需要在个人中心页做可视化（复用 v3.5 的雷达图组件叠加多场）？当前仅在对话中以文字 + 升降标记呈现
3. 工具达到 11 个后，是否需要在 Agent 的 system prompt 里加一段"工具选择优先级/互斥说明"？（现有 prompt 只写了规则，未列工具清单）
4. `daily_agent_tool_llm_limit=20` 的初始值是否合适？需在真机观察后再定（工具内调用最贵的场景是 `job_match` 的 4000 字 JD + 简历全文）
5. 批次 2 的 `job_match` / `answer_review` 是否需要前端入口（如报告页加"贴 JD 匹配"按钮），还是只走 AI 客服对话？
