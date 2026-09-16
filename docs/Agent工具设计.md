# Agent 工具设计：原实现 → 工具封装的对照

> **本文件是干嘛的**：把「平台已有的功能接口」与「给 LLM 调用的 Agent 工具」摆在一起对照，
> 说清楚封装过程中真正做了什么，以及扩展工具时该复用哪段代码。
> 建立日期：2026-09-16 · 对应版本 v3.5（4 个工具）

---

## 〇、先对齐一个概念

**工具不是接口的薄包装。** 两者的消费方完全不同：

| | HTTP 接口 | Agent 工具 |
|---|---|---|
| 消费方 | 前端的 JS 代码 / 人 | 大模型本身 |
| 入参来源 | 前端严格构造 + pydantic 校验 | **LLM 现编的字符串**（可能缺参、传错类型、编造参数名） |
| 返回值 | JSON，交给组件渲染 | **自然语言文本**，塞进下一轮 prompt 的上下文 |
| 报错 | 抛 `HTTPException(4xx/5xx)`，前端展示 | **必须吞掉**并返回一句说明，否则整轮 Agent 崩 |
| 身份 | `Depends(get_current_user)` 注入 | **闭包捕获**（工厂函数把 db/归属者绑进工具） |
| 生命周期 | 请求级，无状态 | 一次 Agent 运行内**共享 `ToolContext`** |
| 长度 | 想给多少给多少 | **必须截断**，否则一次调用就撑爆上下文与费用 |

所以"封装"的实质工作是这五件事：**容错解析入参 → 裁剪返回值 → 吞异常给兜底话术 → 闭包绑定身份 → 控制文本体积**。

---

## 一、现有 4 个工具的逐项对照

### 1. `kb_search` — 知识库检索

| | |
|---|---|
| **原实现** | `POST /api/playground/ask`（SSE）：`embed_texts` → `search_chunks` → 拼 RAG 上下文 → `stream_chat` 流式生成；另有 `/api/kb/*` 做文档 CRUD |
| **工具实现** | `embed_texts([query])` → `search_chunks(..., query_text=query)` → 回填 `ctx.citations` → 拼「5 块 × 300 字」来源文本返回 |
| **关键差异** | ① 接口做**完整 RAG**（检索 + 生成），工具**只做检索**——生成交给 Agent 主循环；② 引用来源不给 LLM，而是存进 `ToolContext` 交给前端；③ 未命中时**主动清空 citations**，并明确告知"没检索到"，防止模型编造"知识库说…"；④ embedding 失败时返回"向量服务不可用，请用通用知识回答"，而不是抛错 |

### 2. `resume_lookup` — 本人简历

| | |
|---|---|
| **原实现** | `GET /api/resumes`（列表）、`GET /api/resumes/{id}`（详情，含完整 `raw_text`）；归属校验是"跨用户一律 404" |
| **工具实现** | `_owner_filter(Resume,…)` + `where(deleted_at is None).limit(5)` → 每条简历再用 `_snippet()` 在正文里定位关键词，返回前后各 80 字的片段 |
| **关键差异** | ① 接口把整篇 `raw_text` 给前端自己渲染，工具**必须截断**（一份简历几千字，全灌进上下文会挤掉对话历史）；② 新增了接口层没有的**关键词定位**能力（LLM 问"我简历里有没有提过 K8s"，工具自己去找而不是把全文丢给它）；③ 直接把 `parse_status`、页数、上传日期这些元信息编进文本，省掉 LLM 理解字段的成本 |

### 3. `interview_history` — 本人面试记录

| | |
|---|---|
| **原实现** | `GET /api/history`（简历/分析/面试汇总）、`GET /api/interviews/scores`（四维评分列表，雷达图数据源） |
| **工具实现** | 查 `InterviewSession`（按 `created_at desc` 取前 N）→ **中文映射**（`finished`→已结束、`senior`→社招、`technical_depth`→技术深度）→ 拼成带评分的文本 |
| **关键差异** | ① 接口返回结构化字段（`position_type: "senior"`）让前端做徽章配色，工具要把它**本地化成中文**，否则 LLM 得猜字段含义；② 对"没有结束报告"的场次要显式说明（"尚未生成评价"），否则模型会把 `null` 当成 0 分；③ 工具返回的是**摘要**，不返回完整对话记录（那是另一个量级的数据） |

### 4. `usage_stats` — 本人用量

| | |
|---|---|
| **原实现** | `GET /api/me/stats`（五卡计数 + 今日/累计 token）、`GET /api/me/usage`（近 7 日趋势）、`GET /api/usage/logs`（明细 + 四类筛选 + 分页） |
| **工具实现** | `select(action_type, count(*), sum(tokens_total)).group_by(action_type)` → 按**动作类型聚合** + 中文动作名 + 合计 |
| **关键差异** | ① 三个接口是按"页面卡片"预设好口径的，工具要按**自然语言问法**组织数据（"我用了多少次"→ 按动作分组而不是五张卡）；② 支持 LLM 传 `days` 参数并做范围钳制（`1~90`），防止模型传 `99999`；③ 接口层没有的"跨动作汇总"由工具补上 |

---

## 二、拟扩展工具的对照

| 新工具 | 复用的原实现 | 工具实现要点 |
|---|---|---|
| `analysis_read` | `analyses._latest_valid_analysis(db, resume_id)`；`AnalysisOut` 结构 | 入参是**简历标识**而 LLM 手上只有文件名 → 需先 `resume_lookup` 或在工具内部按"本人最新一份"兜底；六块内容要**按需截断**（面试题列表最长） |
| `job_match` | `ai_client.chat_json(system, user, settings, validator)` + `AIReport` 同款校验重试 | ✅ 已实现（US-009）：提示词在 `prompts.py`（`JOB_MATCH_SYSTEM_PROMPT` + `build_job_match_prompt`），版本号**独立**为 `JOB_MATCH_PROMPT_VERSION`（与 analyses 表的 `PROMPT_VERSION` 无关，改它不会让旧分析报告失效）；校验模型 `JobMatchReport` 在 `schemas/agent.py`；JD 原文截到 4000 字并在输出里说明，简历正文截到 6000 字；LLM 调用走 `_run_tool_llm`（限额 + 记账），异常自己吞成话术 |
| `question_gen` | `interview_prompts` 的 `position_type` 难度逻辑；`kb_service.search_chunks` | ✅ 已实现（US-010）：**先检索平台知识库、再依据命中的语料出题**（不是纯模型生成）——命中的前 3 块截到 300 字送进提示词，输出里标出《出题依据》文档名；检索未命中或检索失败时退回模型自身出题，并在输出里明确标注「以下题目不来自平台知识库」。难度定位走 `_POSITION_LABELS`（intern/fresh/senior，空值与非法值回落"通用"），题目数由 `QuestionGenReport`（3~5）卡住。与 `kb_search` 的分工：**出题用本工具，查答案/讲解用 kb_search**（题目不带答案，描述里互相点名） |
| `answer_review` | `chat_json` + `build_final_report_system_prompt` 的四维评分 schema | ✅ 已实现（US-011）：复用了四维评分里**技术深度/表达结构/项目真实性**三项（共用 `_SCORE_LABELS` 口径，10 分制，**不评整体**——整体小结留给模拟面试的结束报告），再加 2~4 条改进建议；题目与回答都必填（缺哪个就提示补全，一次模型都不调），用户贴的回答截到 2000 字并在输出里说明；提示词/版本常量在 `prompts.py`（`ANSWER_REVIEW_PROMPT_VERSION`），输出契约 `AnswerReviewReport`（建议 `min_length=2 / max_length=4`、评分 `1~10`）在 `schemas/agent.py`；LLM 调用走 `_run_tool_llm`。与 `question_gen` 的分工：**出题用 question_gen，点评已写好的回答用本工具**（描述里互相点名） |
| `score_trend` | `/api/interviews/scores` 同款查询（本人、finished、有报告） | 接口只给"列表"，工具要做**趋势**：对比首末两场的各维度升降，这是新增计算逻辑 |
| `kb_list` | `kb_service.list_documents(db, user_id, anonymous_id)` | 只输出标题 + 块数 + 状态 + 归属，**绝不能带 `raw_text`**（那是 kb_search 的职责） |
| `platform_help` | ⚠️ **无原实现**（唯一"新增能力"而非封装） | 返回静态功能说明，**不查库、不调模型**；实现要点是 **topic 路由**——`_PLATFORM_TOPICS` 用「别名元组 → 文案」做小写包含匹配（别名顺序即优先级，具体主题排前防泛词抢匹配），空 topic 或全不命中回落总览。**描述必须与 `kb_search` 划清界限**——平台功能 vs 计算机技术知识点，否则两个工具会互抢 |

---

## 三、通用封装骨架

新增一个工具的完整形态（以 `analysis_read` 为例）：

```python
@tool
def analysis_read(resume_hint: str) -> str:
    """读取当前用户某份简历的 AI 分析报告（岗位匹配/优势/短板/关键词缺口/改进建议/预测面试题）。
    当用户问"我的分析报告说了什么""上次分析结论"时调用。
    入参 resume_hint 是简历文件名或关键词，传空字符串表示最近一份。"""

    # ① 身份：闭包捕获的 user_id / anonymous_id，不依赖任何请求上下文
    owner = _owner_filter(Resume, user_id, anonymous_id)
    if owner is None:
        return "当前会话无法识别用户身份，查不到个人数据。请提示用户先登录后再提问。"

    try:
        # ② 入参容错：LLM 给的是自然语言提示，不是主键
        rows = db.scalars(select(Resume).where(Resume.deleted_at.is_(None), owner)...).all()
        if not rows:
            return "该用户名下没有已上传的简历。"          # ③ 空结果给明确说明
    except Exception:
        logger.exception("analysis_read 查询失败")
        return "简历查询暂时出错，请稍后再试。"            # ④ 异常吞掉，返回自然语言

    # ⑤ 体积控制：长列表截断后再拼给 LLM
    return "\n".join(_format_report(r)[:800] for r in rows[:2])
```

五个要点对应上一节表格里的五行——**这就是"封装"的工作量所在**，而不是把接口逻辑复制一遍。

**依赖方向（硬约束）**：工具住在 `services/` 层，**不得 import `app/api/`**——项目是
Router → Service → Model 单向依赖，反向引用会让路由层无法独立测试。需要复用接口层里的
查询时，先把纯查询下沉成 service 函数（`analysis_read` 就是这么拿到
`analysis_service.latest_valid_analysis` 的），接口与工具都调那一份，**不要复制 SQL**。

---

## 四、工具描述写作规范（互斥性）

工具数量上去后，第一个翻车点不是模型笨，而是**描述重叠**。每个描述要写清三件事：

1. **边界**：我管什么（"计算机技术知识点、编程语言、框架原理"）
2. **时机**：什么时候用（"用户询问具体技术知识时"）
3. **排他**：什么时候**不**用我（`kb_search`："问本平台怎么用请用 platform_help"）

**落地口径（8 个工具已统一）**：docstring 就是给模型看的路由说明，全部按
「摘要一句 → `什么时候用：` → `什么时候不用：` → 入参说明」四段写，两个小标题用中文冒号固定，
这样人看得懂、模型也认得出。`tests/test_agent_tools.py` 里有两条守门测试：
每个工具的 description 必须 > 50 字符且同时含"什么时候用"与"什么时候不用"；
易撞组合必须在描述里互相点名。新增工具时这两条会直接拦住漏写的描述。

当前 11 个工具的易撞组合与划线方式：

| 易撞组合 | 划线方式 |
|---|---|
| `kb_search` vs `platform_help` | 前者=计算机技术知识，后者=**本平台**怎么用（互相点名排他） |
| `kb_search` vs `kb_list` | 前者=某主题的**具体知识点内容**（检索出正文片段），后者=知识库**文档清单**（有哪些资料、各多少块）；`kb_list` **绝不返回正文** |
| `resume_lookup` vs `analysis_read` | 前者=简历**原文**（写过没写过某技能），后者=AI 对简历的**结论**（优劣与建议），互相点名排他 |
| `interview_history` vs `score_trend` | 前者=**单场**记录与结束评价，后者=**跨场次**的四维分数升降对比 |
| `usage_stats` vs `platform_help` | 前者=本人**真实用量数字**，后者="在哪看用量"的**功能入口说明** |
| `platform_help` vs 个人数据四件套 | 前者=功能怎么用（静态文案，不查库），后者=本人真实数据（resume_lookup / analysis_read / interview_history / usage_stats） |
| `job_match` vs `analysis_read` | 前者=拿用户**贴的 JD 现算**匹配度（工具内调模型），后者=读**已有的**简历分析结论（只查库，不生成新内容） |
| `job_match` vs `resume_lookup` | 前者=简历与 JD 的对比判断，后者=简历**原文**里有没有写过某技能/项目 |
| `kb_search` vs `question_gen` | 前者=查知识点的**答案与讲解**（检索出正文片段），后者=**出一组模拟面试题**（只出题、不给答案）；两者互相点名排他 |
| `question_gen` vs `answer_review` | 前者=**出题**（用户还没答），后者=**点评用户已经写好的一段回答**（题目 + 回答一起给）；两者互相点名排他 |
| `kb_search` vs `answer_review` | 前者=查知识点**答案**，后者=点评**用户自己的回答**；模型容易因为用户回答里出现技术名词（如"Redis 缓存穿透"）就跑去检索，两个描述里都写死了这条排他 |
| `kb_list` vs `platform_help` | 前者=知识库的**收录范围**（有哪些资料、能问哪些方向），后者=功能的**操作方式与入口**（怎么上传、在哪看）；"这个平台"字样不归 `platform_help` 独占 |

---

## 五、工具路由评测（准确率怎么量）

工具从 4 个扩到 8 个、再到 11 个，判断"要不要拆多 Agent"靠的不是感觉，是**路由 top-1 准确率**：
用户一句问法，模型挑的工具对不对。

- 用例集：`data/agent_eval/routing.json`（`question` + `expected_tool`，11 工具各 3 条口语化问法共 33 条；
  守门测试 `test_cases_cover_every_tool` 强制「用例数 ≥ 工具数 × 3」，加工具不加用例会直接红）
- 脚本：`backend/scripts/eval_agent_routing.py`（backend/ 目录下 `python -m scripts.eval_agent_routing`）
- 口径：每题**一次** LLM 调用做选择（temperature 0），只取返回消息里首个 `tool_calls` 的工具名；
  **工具执行体全程不运行**（不查库、不检索），所以脚本不碰任何业务数据
- 产出：`data/agent_eval/report.md`，含 top-1 准确率、分工具命中率、混淆矩阵（期望 × 实际）、逐题明细、误选清单
- LLM 不可用（欠费/未配置/超时）时脚本**以退出码 2 中止且不写报告**，避免空报告被当成评测结果

新增/改写工具描述后重跑该脚本即为路由回归；扩到 11 个工具后（US-012）要拿新报告与 8 工具基线对比。
报告里现在直接带一行「对比 8 工具口径基线：±X 个百分点」，不用翻历史报告。

### 实测记录：11 工具口径（US-012，2026-09-16，deepseek-chat / temperature 0）

| 轮次 | top-1 | 说明 |
|---|---|---|
| 首轮 | 27/33（81.8%） | 未达标（< 85%）。误选集中在 3 个工具 |
| 描述校准后 | 30/33（90.9%） | 改 5 个工具的 docstring 排他句 |
| 用例自包含化后 | **33/33（100.0%）** | 与 8 工具基线（24/24）持平，变化 0.0 个百分点 |

首轮全部 6 处误选：

| 问法 | 期望 | 实际 | 处置 |
|---|---|---|---|
| 我能在这个平台上问哪些方向的技术问题 | kb_list | platform_help | 描述划线：知识库**收录范围** vs 功能**操作入口** |
| 这家公司要求熟悉 MySQL 和 K8s，我匹配吗 | job_match | 未调用工具 | 描述写明「口述的岗位要求也算 JD」，只有完全没提岗位才请用户贴 JD |
| 我贴一段招聘 JD，帮我看看我简历还缺哪些关键词 | job_match | 未调用工具 | 用例不自包含（JD 不在本轮），改写为带要求原文 |
| 按这个岗位的招聘要求，我的简历该怎么改 | job_match | 未调用工具 | 同上 |
| 帮我看看我这段回答能得几分：Redis 缓存穿透… | answer_review | kb_search | 描述写明「回答里出现技术名词不等于要检索」 |
| 刚才那道题我是这样答的，帮我点评一下哪里还能改 | answer_review | 未调用工具 | 用例依赖上文（单轮评测拿不到回答），改写为自包含 |

**结论**：11 个工具挂在同一个 ReAct Agent 上，路由准确率 100%，与 8 工具基线持平 ——
扩工具**没有**造成路由退化，因此**暂不拆多 Agent**。首轮掉到 81.8% 的原因不是"工具太多记不住"，
而是两类可修问题：描述边界写得不够狠（job_match 被"没贴 JD"劝退、answer_review 被技术名词带跑），
以及用例依赖上下文。写新工具描述时，除了"什么时候用"，**"什么时候不用"里要写清同类工具的名字和触发场景**。

---

## 六、工具内 LLM 调用的限额与记账（已实现）

工具内部自己要调模型的三个工具（`job_match` / `question_gen` / `answer_review`）
**统一走 `_run_tool_llm(db, user_id, anonymous_id, call)`**，它按顺序做三件事：

1. **限额**：查当日 `agent_tool_llm` 次数，达到 `daily_agent_tool_llm_limit`（默认 20）就
   **不调模型**直接返回话术（`_TOOL_LLM_LIMIT_REPLY`）；`limit=0` 即关闭这类调用。
2. **执行**：放行后调 `call()`。异常**不在这里吞**——"JD 太短""回答为空""服务欠费"要给的
   上下文话术各不相同，由各工具自己 `try/except` 组织，保证不向上抛。
3. **记账**：成功后记一条 `agent_tool_llm` 用量（含 prompt+completion 合计）并提交，
   这样 Agent 后续崩了这笔消耗也不会丢。记账失败只告警，不连累工具返回结果。

- 与 Agent 主循环的 `daily_agent_limit`(30) **分开计**：单次提问若触发工具内模型调用，
  实际消耗是主循环的两倍，混在一起无法归因，也会让实际可用次数莫名腰斩。
- **失败的调用不记账**（重试仍受主循环 `daily_agent_limit` 约束）；
  识别不到归属者时也不写账（没法归因，免得污染全站总量统计）。
- 新增工具若调用 LLM，**必须复用 `_run_tool_llm`**，否则限流与成本统计会漏。
- 新增提示词（`job_match` / `question_gen` / `answer_review`）必须递增各自的版本常量
  （`JOB_MATCH_PROMPT_VERSION` / `QUESTION_GEN_PROMPT_VERSION` / `ANSWER_REVIEW_PROMPT_VERSION`），
  旧结果不复用；**绝不要动 analyses 表口径的 `PROMPT_VERSION`**。
