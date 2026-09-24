# 内容规划（outline）

交付物：AIResume 项目「项目经历」简历板块 Word 文档（可直接粘贴进简历）。

## 结构（对齐参考图 A1）

1. **项目名**（加粗）：AIResume——AI 简历分析与模拟面试平台（个人全栈项目）
2. **技术栈：** FastAPI、PostgreSQL 16（pgvector）、Redis、SQLAlchemy 2、Celery、Vue 3、Vite、LangChain 0.3、Ollama（nomic-embed-text）
3. **项目简介**（一段）：全链路定位（注册登录 → PDF 简历上传解析 → AI 智能分析报告 → 基于简历的多轮文字模拟面试 → 结束评价 + 知识库问答）；三大痛点（AI 输出不可控、简历解析易翻车、长耗时任务阻塞）；架构手段（统一 AI 封装层 + JSON 结构化校验 + 异步任务化 + RAG 混合检索）。
4. **分点 ×7**（每条 = 技术点 + 做法 + 量化，格式对齐参考图）：

   | # | 技术点 | 做法 | 量化/效果 | 事实来源 |
   |---|---|---|---|---|
   | 1 | RAG 混合检索 | pgvector 向量 + jieba/BM25 词法 + RRF 融合，49 题黄金问答集评测 | hit@1 71.4%→87.8%，hit@5 91.8%→100%，无一题回退 | PROGRESS v3.5 |
   | 2 | LangChain 0.3 ReAct Agent | 11 个工具（kb_search/resume_lookup/interview_history/usage_stats/job_match 等），个人数据归属过滤 + 独立限额 | 工具路由评测 33/33 达 100% | PROGRESS Ralph 轮 |
   | 3 | SSE 流式 | 逐字输出 + Agent 工具调用过程可视化，会话快照守卫 + abort 句柄，消息落库可恢复 | 实测单次问答 500+ 流式分片，中断后刷新可续聊 | PROGRESS v3.4 + AGENTS v3.7 |
   | 4 | Celery + Redis 异步化 | 简历解析/知识库入库转后台任务，幂等重试；Redis 令牌桶每日限额限流 | 前端任务进度可见，遏制刷爆 AI API 成本 | PROJECT-PLAN 阶段4 / v3.0 |
   | 5 | 统一 AI 封装层 | OpenAI 兼容协议，JSON 输出校验 + 失败自动重试，提示词版本号留档 | 换模型仅改 3 行配置，输出结构化可控、可追溯对比 | PROJECT-PLAN §3 |
   | 6 | 工程质量门禁 | 77 项安全审计全修（含 2 项 P0 横向越权），统一 owner_clause 归属校验 | 264 个 pytest 全绿、服务层覆盖率 89%、CI（ruff→迁移→测试→构建）全绿 | PROGRESS v3.7.1 |
   | 7 | 高可用与快速失败 | DB 连接异常统一转 503，全局错误处理器 | 停库实测 5.3s 返回（修复前挂起 60s+），恢复 0.36s | PROGRESS v3.6 |

## 边界
- 只写项目经历板块，不生成整份简历（姓名/教育背景等由用户自行填写）
- 全部量化数字来自项目文档，不编造；个人项目定位为「个人全栈项目」，不虚构公司/团队背景
- 交付载体：本地 Word（.docx），无模板创建
