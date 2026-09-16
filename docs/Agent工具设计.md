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
| `job_match` | `ai_client.chat_json(system, user, settings, validator)` + `AIReport` 同款校验重试 | **新增一套 prompt + 递增 `PROMPT_VERSION`**；JD 原文要截断（用户可能贴一整页）；结构化输出必须走 validator 才能享受重试 |
| `question_gen` | `interview_prompts` 的 `position_type` 难度逻辑、`stage_for_turn` | 复用难度分级；返回题目列表而非单题；要注意与 `kb_search` 的分工——**出题用模型自身能力，查答案是 kb_search** |
| `answer_review` | `chat_json` + `build_final_report_system_prompt` 的四维评分 schema | 复用技术深度/表达结构/项目真实性三个维度做单次回答点评；用户贴的回答要限长（如 2000 字） |
| `score_trend` | `/api/interviews/scores` 同款查询（本人、finished、有报告） | 接口只给"列表"，工具要做**趋势**：对比首末两场的各维度升降，这是新增计算逻辑 |
| `kb_list` | `kb_service.list_documents(db, user_id, anonymous_id)` | 只输出标题 + 块数 + 状态 + 归属，**绝不能带 `raw_text`**（那是 kb_search 的职责） |
| `platform_help` | ⚠️ **无原实现** | 唯一"新增能力"而非封装；返回静态功能说明。**描述必须与 `kb_search` 划清界限**——平台功能 vs 计算机技术知识点，否则两个工具会互抢 |

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
3. **排他**：什么时候**不**用我（`kb_search`："平台功能使用问题请勿调用本工具，走 platform_help"）

现有 4 个工具的边界目前是清晰的（知识库 / 简历 / 面试 / 用量），扩到 10 个时最容易撞的是这两对：

| 易撞组合 | 划线方式 |
|---|---|
| `kb_search` vs `platform_help` | 前者=计算机技术知识，后者=**本平台**怎么用 |
| `resume_lookup` vs `analysis_read` | 前者=简历**原文**，后者=AI 对简历的**结论** |
| `interview_history` vs `score_trend` | 前者=**单场**记录与结束评价，后者=**跨场次**的四维分数升降对比 |

---

## 五、待办口径

- 新增工具若调用 LLM（`job_match` / `answer_review`），**必须计入 `usage_logs`**，否则限流与成本统计会漏。
- 现有 `daily_agent_limit=30` 是按"单 Agent 单轮"定的；工具内部再调 LLM 会让实际消耗翻倍，**扩到 10 个工具时需重算限额口径**。
- 新增提示词（`job_match` / `answer_review`）必须递增各自的 `PROMPT_VERSION`，旧结果不复用。
