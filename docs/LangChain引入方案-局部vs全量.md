# LangChain 引入方案对比：局部引入 vs 全量替换

> 编制日期：2026-09-04
> 状态：**待评审，未动工**（等你选定方案后再实施）
> 适用项目：AI 简历分析 + AI 模拟面试（v3.3 已完成，90 pytest 全绿）

---

## 〇、现状盘点（两个方案的共同起点）

### 现有 AI 层是纯手写的，4 个核心服务文件

| 文件 | 行数 | 职责 | 被谁调用 |
|---|---|---|---|
| `services/ai_client.py` | 185 | LLM 调用：`chat_json()`（结构化 JSON+重试）、`stream_chat()`（SSE 流式）、`analyze_resume()` | analyses、interviews、playground、worker/tasks、analysis_service |
| `services/embedding_service.py` | 48 | 批量向量化 `embed_texts()`，OpenAI 兼容协议调 Ollama | kb_service、playground |
| `services/kb_chunker.py` | 64 | 段落合并切块 `chunk_text()`，~600字/块~60重叠 | kb_service |
| `services/kb_service.py` | 218 | 入库 `ingest_kb_document()`、检索 `search_chunks()`、文档 CRUD + owner 隔离 | playground、knowledge_base、admin_kb、worker/tasks |

### 现有调用链路（RAG 问答）
```
用户提问 → playground.py
  → embed_texts() 问题向量化
  → search_chunks() pgvector HNSW 余弦检索 + 阈值过滤
  → 手写拼接 RAG Prompt
  → stream_chat() SSE 流式输出
  → 记账 + 持久化消息
```

### 现有依赖（requirements.txt 中 AI 相关）
```
openai>=1.40          # 唯一的 AI SDK，LLM 和 Embedding 都用它
pgvector>=0.3         # SQLAlchemy 向量类型映射
```

### 现有测试基线
90 个 pytest 全绿，其中涉及 AI 层的测试约 30+ 个（全部 mock embedding/AI，不烧真实调用）。

---

# 方案 A：局部引入（增量封装，不碰现有 RAG）

## A.1 核心思路

**现有手写 RAG 一行不改**，新增一个独立的 Agent 层。LangChain 只负责"Agent 工具调用循环"这一件事，现有的知识库检索、简历分析、模拟面试全部封装成 LangChain Tool，由 Agent 按需调度。

```
现有链路（不动）：          新增链路（LangChain）：
playground RAG 问答         /api/agent/ask
  手写 embed→检索→LLM          Agent（LangChain ReAct/Tool Calling）
                                ├─ Tool: 知识库检索（调现有 search_chunks）
                                ├─ Tool: 简历分析（调现有 analyze_resume）
                                ├─ Tool: 使用日志查询（调现有 usage 接口）
                                └─ Tool: 面试历史查询（读 interview_sessions）
                              → SSE 流式输出 Agent 的思考+行动+回答
```

## A.2 依赖变更（最小化）

`requirements.txt` 新增：
```
langchain-core>=0.3      # 核心抽象（Runnable/Tool/Message），轻量
langchain-openai>=0.2    # ChatOpenAI，OpenAI 兼容协议（通义/DeepSeek/Ollama 都能用）
```
**不引入**：`langchain`（主包，含旧 Chain）、`langchain-community`（社区集成，体积大）、`langgraph`（暂不需要复杂状态图）。

## A.3 文件变更清单

### 新增（6 个文件）
| 文件 | 职责 |
|---|---|
| `services/agent/__init__.py` | 包初始化 |
| `services/agent/llm_factory.py` | 统一创建 LangChain `ChatOpenAI`，复用现有 settings（base_url/api_key/model），支持流式 |
| `services/agent/tools.py` | 把现有功能封装成 LangChain `@tool`：`kb_search`、`resume_analyze`、`query_usage_logs`、`query_interview_history` |
| `services/agent/react_agent.py` | 创建 Agent（`create_openai_tools_agent` + `AgentExecutor`），系统提示词、最大步数、错误兜底 |
| `api/agent.py` | 新增 `POST /api/agent/ask`，SSE 流式返回 Agent 的 Thought/Action/Observation/Final Answer |
| `tests/test_agent.py` | Agent 测试（mock LLM，验证工具调用决策、最大步数、错误兜底、归属隔离） |

### 修改（3 个文件，最小改动）
| 文件 | 改动 |
|---|---|
| `requirements.txt` | +2 行依赖 |
| `frontend/src/App.vue` | 侧边栏新增「AI 助手」导航项（admin 可见或全员可见，待定） |
| `frontend/src/components/AgentView.vue` | 新增前端页面（可复用 ChatView 的聊天 UI，额外展示工具调用过程） |

### 不改动（关键）
- `ai_client.py`、`embedding_service.py`、`kb_chunker.py`、`kb_service.py` **零改动**
- `playground.py`、`interviews.py`、`analyses.py`、`knowledge_base.py` **零改动**
- 现有 90 个测试全部不受影响
- 数据库零迁移（不新增表，Agent 会话可复用 chat_sessions 或新建 agent_sessions 表，见 A.6）

## A.4 关键设计

### Tool 封装示例（tools.py 核心逻辑）
```python
from langchain_core.tools import tool
from app.services.kb_service import search_chunks
from app.services.embedding_service import embed_texts

@tool
def kb_search(query: str) -> str:
    """在技术面试知识库中检索相关资料，输入问题，返回相关文档片段。"""
    # 直接复用现有手写检索，不重写
    vec = embed_texts([query])[0]
    chunks = search_chunks(db, vec, user_id=None, anonymous_id=None, top_k=5)
    return "\n\n".join(f"[{c['title']}] {c['content']}" for c in chunks)
```
**要点**：Tool 内部调用的全是现有函数，LangChain 只负责"决定什么时候调哪个工具"。

### Agent 创建（react_agent.py 核心逻辑）
```python
from langchain_openai import ChatOpenAI
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

llm = ChatOpenAI(
    base_url=settings.ai_base_url,
    api_key=settings.ai_api_key,
    model=settings.ai_model,
    temperature=0.3,
    streaming=True,
)
tools = [kb_search, resume_analyze, query_usage_logs]
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是技术面试 AI 助手，可以调用工具检索知识库、分析简历..."),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])
agent = create_openai_tools_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, max_iterations=8, verbose=True)
```

### SSE 流式输出设计
Agent 的流式比普通 RAG 复杂——它有"思考→工具调用→工具结果→最终回答"多个阶段。SSE 事件设计：
```
event: thought    → Agent 的思考过程
event: action     → 调用了哪个工具、传了什么参数
event: observation→ 工具返回结果摘要
event: delta      → 最终回答的流式 Token
event: done       → 完整结果 + token 统计
event: error      → 错误兜底
```

## A.5 实施步骤（4 个提交，小步走）

| 提交 | 内容 | 验证 |
|---|---|---|
| ① 依赖+基础设施 | requirements 加依赖；llm_factory.py；跑通最简 LangChain 调用（不接工具） | 1 个冒烟测试 |
| ② Tool 封装+Agent 核心 | tools.py（4 个 Tool）+ react_agent.py；test_agent.py | 8-10 个测试全绿 |
| ③ Agent API | api/agent.py，SSE 流式，记账限流，归属隔离 | 接口测试 + 全量 pytest |
| ④ 前端 AgentView | 新增 AI 助手页面，展示工具调用过程 | npm build + 浏览器验收 |

## A.6 一个待决策点：Agent 会话是否持久化
- **方案 A1（省事）**：Agent 对话复用现有 chat_sessions/chat_messages 表，加一个 `session_type='agent'` 字段区分（需 1 个轻量迁移）
- **方案 A2（最省事）**：Agent 不持久化，纯无状态问答（刷新即丢失），后续需要再加表
- **建议**：先 A2 跑通，验证 Agent 价值后再决定是否持久化

## A.7 工作量与风险
- **工作量**：约 1200-1800 行新代码，4 个提交，预计 1 个会话内完成
- **风险：低**。纯增量，现有功能不受影响，出问题直接回滚新增文件即可
- **依赖体积**：langchain-core + langchain-openai 及其传递依赖约 30-50MB
- **后续可扩展**：Rerank、多 Agent 协作、LangGraph 状态机都可以在 Agent 层独立演进，不影响现有 RAG

---

# 方案 B：全量替换（用 LangChain 重写整个 AI 层）

## B.1 核心思路

用 LangChain 标准组件**替换全部 4 个手写 AI 服务文件**，RAG 链路改用 LCEL（LangChain Expression Language）表达式组织，统一技术栈。现有 API 路由和前端保持不变，但内部实现全部重写。

```
替换前（手写）：                    替换后（LangChain）：
embedding_service.py                LangChain OpenAIEmbeddings/OllamaEmbeddings
kb_chunker.py                       RecursiveCharacterTextSplitter
kb_service.py                       LangChain PGVector VectorStore（自定义适配现有表）
ai_client.py                        ChatOpenAI + LCEL
playground.py 手写 RAG 流程         create_retrieval_chain / LCEL Runnable 管道
analyses/interviews 的 AI 调用      LCEL + PydanticOutputParser
```

## B.2 依赖变更

`requirements.txt` 新增：
```
langchain>=0.3                # 主包（chains/agents/output_parsers）
langchain-core>=0.3
langchain-openai>=0.2
langchain-text-splitters>=0.3 # RecursiveCharacterTextSplitter
langchain-community>=0.3      # PGVector VectorStore、OllamaEmbeddings
```
**注意**：`langchain-community` 体积较大，会拉入较多传递依赖。

## B.3 文件变更清单

### 重写（4 个核心服务）
| 文件 | 替换方案 | 风险点 |
|---|---|---|
| `embedding_service.py` | 改用 `OpenAIEmbeddings`（base_url 指向 Ollama），保持 `embed_texts()` 函数签名不变，调用方零改动 | 低：接口签名不变 |
| `kb_chunker.py` | 改用 `RecursiveCharacterTextSplitter`，保持 `chunk_text()` 返回格式不变 | **中**：切块算法变了，切块结果会变，已有语料需要重新入库才能对齐，且 eval_rag 基线会变 |
| `kb_service.py` | 检索改用 LangChain PGVector VectorStore；**不采用 LangChain 默认表结构**，自定义 VectorStore 适配现有 kb_chunks 表（保留 owner 隔离、软删除、阈值过滤） | **高**：PGVector 默认表结构和现有表不一致，需要写自定义 VectorStore，工作量不比手写少 |
| `ai_client.py` | `chat_json` 改用 LCEL + `PydanticOutputParser` + 重试；`stream_chat` 改用 `ChatOpenAI.astream` | **中**：流式行为、usage 统计、异常分类都要重新对齐 |

### 适配修改（5 个调用方文件）
| 文件 | 改动 |
|---|---|
| `api/playground.py` | RAG 流程改用 LCEL 管道（retriever → prompt → llm → output parser），SSE 事件格式保持前端兼容 |
| `api/interviews.py` | `stream_chat`/`chat_json` 调用适配新实现（如果函数签名保持不变则改动很小） |
| `api/analyses.py` | 同上 |
| `services/analysis_service.py` | 适配新的 AnalysisResult 结构 |
| `worker/tasks.py` | Celery 任务里的 `ingest_kb_document`/`analyze_resume` 适配 |

### 测试重写（约 20+ 个测试）
| 测试文件 | 改动 |
|---|---|
| `tests/test_kb_chunker.py`（如有） | 切块结果断言全部要改（算法变了） |
| `tests/test_rag*.py` / `test_playground.py` | mock 对象从手写函数改为 LangChain Runnable，mock 方式全变 |
| `tests/test_analyses.py` / `test_interviews.py` | AI 调用 mock 适配 |
| `tests/test_kb*.py` | 入库/检索适配新 VectorStore |

### 不改动
- 数据库模型和表结构（自定义 VectorStore 适配现有 kb_chunks 表，**不需要重新向量化**，前提是 embedding 模型不变）
- 前端（SSE 事件格式保持兼容）
- 认证、限流、记账等非 AI 逻辑

## B.4 关键技术难点

### 难点 1：PGVector VectorStore 适配现有表结构
LangChain 的 `PGVector` 默认会创建自己的表（`langchain_pg_collection`、`langchain_pg_embedding`），和现有的 `kb_documents`/`kb_chunks` 两表结构完全不同。两个选择：
- **B-1 自定义 VectorStore**：继承 `VectorStore` 基类，实现 `add_texts`/`similarity_search`，内部操作现有 kb_chunks 表——工作量约 200 行，且要处理 owner 隔离、软删除、阈值过滤等现有逻辑
- **B-2 改用 LangChain 默认表**：新建 LangChain 表，数据迁移，现有 kb_documents/kb_chunks 废弃——改动面更大，且丢失 owner 隔离等业务逻辑，不推荐

### 难点 2：切块算法变更导致基线失效
现有 `kb_chunker.py` 是"按段落合并到 600 字"，LangChain 的 `RecursiveCharacterTextSplitter` 是"递归分隔符切分"，两者切块结果不同。这意味着：
- 已有 111 块语料需要全部重新入库（`seed_kb_preset.py --reset`）
- eval_rag 的基线数据（hit@1=73.5%）失效，需要重新评测
- 用户上传的私有文档也要重新入库

### 难点 3：LCEL 流式与现有 SSE 事件对齐
现有 SSE 有 meta/delta/done/error 四种事件，done 事件带 citations/tokens/duration。LCEL 的流式是 Runnable.astream_events，事件粒度不同，需要写一层适配器把 LangChain 事件翻译成现有 SSE 格式，否则前端要改。

### 难点 4：结构化输出 + 重试
现有 `chat_json` 有"JSON 解析失败自动重试 2 次"的逻辑，LangChain 的 `PydanticOutputParser` 配合 `OutputFixingParser` 或 `RetryOutputParser` 实现，但行为细节（重试时是否带错误反馈、temperature 是否调整）需要重新调。

## B.5 实施步骤（7 个提交，风险递进）

| 提交 | 内容 | 验证 |
|---|---|---|
| ① 依赖 | requirements 加 5 个依赖，确认安装无冲突 | pip install 成功 |
| ② embedding 替换 | 重写 embedding_service，保持签名不变 | 现有 embedding 相关测试通过 |
| ③ chunker 替换 | 重写 kb_chunker，**重新入库全部语料**，重跑 eval_rag 建立新基线 | 切块测试 + 新基线记录 |
| ④ VectorStore | 自定义 PGVector 适配现有表，重写 kb_service 的 ingest/search | kb 全部测试通过 |
| ⑤ ai_client 替换 | LCEL 重写 chat_json/stream_chat，保持 SSE 事件格式 | analyses/interviews/playground 测试 |
| ⑥ RAG 管道 | playground 改用 LCEL 管道，端到端冒烟（命中/未命中） | 全量 pytest + 真机问答 |
| ⑦ 清理+文档 | 删除废弃代码，更新 PROGRESS/AGENTS，全量回归 | 90+ 测试全绿、ruff、build |

## B.6 工作量与风险
- **工作量**：重写+适配约 2500-3500 行，重写 20+ 测试，7 个提交，预计需要 2-3 个会话
- **风险：高**
  - 核心链路全部改动，任何一层适配不到位都会导致 RAG 问答/简历分析/模拟面试回归
  - LangChain 抽象层调试困难，出问题堆栈深
  - 切块算法变更导致检索质量波动，需要重新调参和评测
  - LangChain 版本迭代快，后续升级可能遇到 breaking change
- **收益**：技术栈统一，后续接 Agent/Rerank/高级 Memory 更顺，LCEL 管道声明式可读性好
- **隐性成本**：langchain-community 依赖体积约 100-150MB，Docker 镜像增大

---

# 两方案对比矩阵

| 维度 | 方案 A：局部引入 | 方案 B：全量替换 |
|---|---|---|
| **核心目标** | 新增 Agent 能力 | 统一技术栈，LangChain 化 |
| **现有 RAG 改动** | 零改动 | 全部重写 |
| **新增依赖** | 2 个（langchain-core/openai，~40MB） | 5 个（含 community，~120MB） |
| **新增/重写代码** | 新增 ~1500 行 | 重写 ~3000 行 |
| **测试影响** | 新增 8-10 个，现有 90 个不动 | 重写 20+ 个，全部重新对齐 |
| **数据库** | 零迁移（或 1 个轻量迁移） | 零迁移（自定义 VectorStore），但语料要重新入库 |
| **eval 基线** | 不受影响 | 失效，需重新评测 |
| **提交数** | 4 个 | 7 个 |
| **预计工时** | 1 个会话 | 2-3 个会话 |
| **回归风险** | 低（纯增量，可独立回滚） | 高（核心链路全改） |
| **获得 Agent 能力** | ✅ 直接获得 | ✅ 重写后也能获得 |
| **获得 Rerank 能力** | ✅ 可在 Agent 层独立加 | ✅ 管道里加 |
| **技术栈统一** | ❌ 手写+LangChain 并存 | ✅ 统一 |
| **调试难度** | 低（现有链路不变，Agent 层独立调试） | 高（全链路 LangChain 抽象） |
| **后续升级成本** | 低（只升级 Agent 层） | 高（LangChain 升级影响全链路） |
| **回滚成本** | 删除新增文件即可 | 需要 git revert 多个提交 |

---

# 推荐建议

## 推荐方案 A（局部引入），理由：

1. **现有手写 RAG 已经稳定且经过评测验证**（hit@5=93.9%，90 测试全绿），全量替换的边际收益不大，但回归风险很高。

2. **LangChain 的真正增量价值在 Agent/工具调用/多步推理**，这正是现有项目缺失的能力，方案 A 精准补这块短板，不做无用功。

3. **符合项目铁律**："只做当前阶段的事，不顺手重构"。方案 B 本质是对 v3.0 已封版代码的重构，违反这条铁律。

4. **可演进**：方案 A 跑通后，如果未来发现 LangChain 的某个组件确实比手写好（比如 Rerank、高级 Memory），可以再局部替换那一个组件，渐进式演进，而不是一次性 all-in。

5. **方案 B 的最大问题**：自定义 PGVector VectorStore 适配现有表的工作量，不比手写 SQL 少——相当于为了用框架而写了一遍框架适配层，得不偿失。

## 什么情况下才考虑方案 B：
- 团队决定后续所有 AI 项目都基于 LangChain，需要统一技术栈
- 现有手写 RAG 出现了难以维护的问题（目前没有）
- 需要大量使用 LangChain 生态的高级组件（多 Agent 协作、LangGraph 复杂状态机、大量第三方 Tool 集成），手写成本已经超过框架适配成本

---

# 待你决策的问题

1. **选哪个方案？** A（局部引入，推荐）还是 B（全量替换）
2. **如果选 A**：
   - Agent 入口是新增「AI 助手」导航项，还是嵌入现有「在线对话」？
   - Agent 可见范围：全员可用还是仅 admin？
   - 会话持久化：先做无状态（A2）还是直接复用 chat_sessions（A1）？
   - 第一批 Tool 除了知识库检索，还要封装哪些？（简历分析/使用日志/面试历史）
3. **如果选 B**：
   - 切块算法变更会导致语料重新入库 + eval 基线重测，接受吗？
   - 是否接受 2-3 个会话的工时？
