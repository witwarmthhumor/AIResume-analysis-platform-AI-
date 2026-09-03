# 进度日志

> **本文件是干嘛的**：项目进度日志——当前进行、已完成、踩过的坑、下一步。AI 每次新会话先读它接续上下文；每次会话结束前更新本文件。
> 最后更新：2026-09-03（v3.1 在线对话多会话+语料库管理迁移 已完成；v3.2 数据看板 UI 重设计、v3.3 使用日志重设计 均规划中待动工）。

## 当前进行
- v3.0 Playground 知识库问答已封版，tag v3.0（本地）
- 前端布局重构完成（顶栏+可折叠侧边栏+四视图 KeepAlive）
- **v3.1 已完成（5 提交，77 pytest 全绿，npm build 通过）**：
  - 在线对话多会话：chat_sessions + chat_messages 两表持久化，会话 CRUD API，playground/ask 加 session_id 持久化消息，首条消息前 20 字自动命名标题，无对话自动新建
  - ChatView.vue：左侧对话列表侧栏（新建/切换/删除/可折叠）+ 右侧聊天主区域（SSE 流式+引用来源），替代原 Playground.vue
  - 语料库管理迁移到管理端独立视图：admin_kb API（列所有文档/删任意文档含预置/上传预置 public）+ KbAdminView.vue（admin 专属导航项）
  - 完整计划与 12 项决策点：`docs/实施计划-在线对话与语料库管理.txt`
- **v3.2 规划中（待动工，等用户指令）**：
  - 数据看板 UI 全面重设计：管理面板→数据看板改名、五统计卡片重设计（五卡一行等宽+五色+悬浮动效）、用户列表卡片化+前端分页（每页10条）、近7日用量卡片化+前端分页（每页5条）+Token趋势柱状图、两卡片一上一下一比一、页面宽度1100px
  - 纯前端，后端零改动，2 个提交
  - 完整计划：`docs/实施计划-v3.2-数据看板重设计.txt`
- **v3.3 规划中（待动工，等用户指令）**：
  - 我的历史→使用日志重设计：改名、内容改为 usage_logs 调用明细（动作/模型/Token/IP/时间）、筛选条件卡片（日期范围+今日/7天/30天+动作类型+模型+IP+重置搜索）、新增 DateRangePicker.vue 日期时间选择器组件（点击日历图标弹出浮层：月份切换+日期网格+开始/结束时间+确定取消）、使用日志表格+动作彩色徽章+空态大虚线框+后端分页、后端新增 GET /api/usage/logs 接口（分页+筛选）
  - 后端新增 1 接口 + 前端重写 HistoryView.vue + 新增 DateRangePicker.vue，3 个提交
  - 完整计划：`docs/实施计划-v3.3-使用日志重设计.txt`
- 项目功能开发完结，进入按需维护

## 已完成
- 项目规划定稿 / 记忆文件落成 / 阶段0 封版 v0.1 / 阶段1 封版 v0.2 / 阶段2 封版 v0.3 / 阶段3 封版 v0.4 / 阶段5 封版 v1.0
- 阶段2（AI 简历分析）：后端封装层（JSON 校验重试/4xx 分类话术）+ 分析接口（去重/限流/留痕/记账）+ 前端报告页（六块内容 + token 可见 + 错误提示）
- 阶段3（文字模拟面试）：四阶段状态机、SSE 逐段流式回复、消息落库恢复、最大轮次、结束评价报告、面试消息限流
- 阶段4：users 表与 Alembic 迁移、Argon2/JWT HttpOnly Cookie 认证、用户归属隔离、Redis 7 + Celery 业务任务、登录/注册/退出/历史记录前端
- 阶段5部署：后端/前端生产 Dockerfile、Nginx SPA fallback 与 /api 反代、生产 compose 五容器栈、健康就绪探针、uploadsdata 持久卷
- v1.1 前端美化：全局设计系统（main.css）+ 吸顶毛玻璃导航 + 拖拽上传区 + 头像气泡/打字动画 + 胶囊状态条
- **v2.0 最终版**（P0~P7）：日志系统 / 连接池+索引+截断+abandoned / 统一错误体系 / 前端 api.js 封装 / RouterRegistry+服务层下沉 / 智能出题 position_type / 只读管理面板 / 简历软删除
- 真机 E2E：DeepSeek 出完整报告 + 缓存去重 + 换模型验证（通义失败路径/DeepSeek 成功路径）
- 测试简历集：test-resumes/ 5 份 PDF + 生成脚本
- 代码审查 P1 修复 + 安全审计修复（tasks 认证、.env.docker 创建、注释修正）
- **v3.0 Playground 知识库问答**（50 个 pytest 全绿、ruff 全绿、npm build 通过）：
  - 基础设施：pgvector 镜像（vector 0.8.6 + HNSW 余弦索引）、Ollama 服务（nomic-embed-text 768 维）
  - 后端：embedding 封装层（本地 Ollama / 云端切换点）、段落切块器（~600字/块~60重叠）、知识库服务（ingest/search/owner 隔离）、Playground SSE 问答（RAG 流式+引用来源）、知识库文档 CRUD（上传 txt/md/pdf）
  - 异步入库：Celery ingest_kb 任务（幂等重试）
  - 预置语料：6 个文件 70+ 问答对（Java/数据库/系统设计/网络/算法/岗位JD），83 块入库
  - 前端：Playground.vue（聊天界面+打字动画+引用折叠+语料库管理）、App.vue 导航入口
  - 评测：49 题黄金问答集 + eval_rag.py 基线（hit@1 73.5%, hit@5 93.9%, avg_top1 0.732）
  - 测试：17 个新测试（chunker/rag/playground），全部 mock embedding/AI，不烧真实调用
  - 冒烟验证：命中（HashMap→5 条引用 0.79）与未命中（天气→明确提示无内容）均通过
- **前端布局重构**（3 提交，npm build 通过，后端零改动）：
  - 三段式布局：顶栏（SVG 对话气泡 logo + 头像下拉/登录按钮）+ 可折叠侧边栏（224px↔64px）+ 内容区
  - 新增 HomeView.vue：首页业务流（上传/列表/详情/分析/面试）从 App.vue 拆出，四视图统一挂 KeepAlive 保活
  - 登录改居中模态（fixed 遮罩 + ✕ 关闭 + 遮罩点击关闭），LoginPanel 去掉 align-self
  - 权限交互：未登录点"我的历史"弹登录模态而非切换视图；退出后回主页、导航项收敛
  - 删除健康状态区（页脚胶囊状态条）；≤900px 侧边栏自动收缩为图标条
- **v3.1 在线对话多会话 + 语料库管理迁移**（5 提交，77 pytest 全绿，npm build 通过）：
  - 后端 chat 全栈：ChatSession + ChatMessage 模型（逻辑关联无物理外键，软删除 session 保留消息），alembic 迁移（chat 两表+索引+补全 kb 表 created_at 索引）
  - chat API：GET/POST /api/chat/sessions（列表按 updated_at desc / 新建默认"新对话"）、DELETE 软删除（归属校验 404 不泄露存在性）、GET /api/chat/sessions/{id}/messages（按时间正序）
  - playground/ask 改造：加可选 session_id，有则校验归属+流式开始前存用户消息（首条且标题为"新对话"时更新为内容前 20 字）+流结束存 assistant 消息（含 citations+tokens）+更新 session.updated_at；消息保存失败只记日志不影响已返回内容；无 session_id 保持原行为向后兼容
  - admin_kb API：GET 列所有文档（含预置+所有用户上传，带 owner_email+块数）、DELETE 删任意文档（绕过 preset 不可删与 owner 校验，软删除可审计）、POST 上传为预置 public（不限流不限名下文档数，全局 file_hash 去重，pdf 50 页）；_admin_only 权限（未登录 401/普通用户 403）
  - 前端 ChatView.vue：左侧对话列表侧栏（230px，新建/切换/删除/可折叠 «，当前项青绿高亮，空态）+ 右侧聊天主区域（复用 SSE 流式+引用来源+打字动画，无对话空态+直接输入自动新建）；替代原 Playground.vue（删除）
  - 前端 KbAdminView.vue：语料库管理表格（ID/标题/来源/所有者/块数/状态/上传时间/删除），上传为预置 public，删除任意文档含预置（二次确认），状态徽标（排队中/入库中/就绪/失败）
  - App.vue：导航项 Playground→在线对话（💬），视图 key playground→chat；新增「语料库管理」导航项（admin 专属，管理面板下方，📚）；logout 时 kb-admin 也回主页
  - 测试：14 个 chat 测试（匿名 CRUD/自定义标题/倒序/空消息/软删除/404/登录匿名隔离/双用户隔离/ask 带 session_id 持久化+标题自动更新/ask 不带不写库/无效 session_id 404）+ 9 个 admin_kb 测试（权限/列所有/删预置/上传预置/去重/格式校验/空内容），全绿
  - 历史遗留修复：0bb14b80309f 迁移是分支从未执行，但数据库已有其创建的 6 张表 created_at 索引；kb_documents/kb_chunks 的 created_at 索引由本次迁移 cacaa643224a 补全；HNSW 索引 autogenerate 误报已手动排除

## 已知问题 / 踩过的坑
- 安全审计：tasks 接口无认证（已修复）、SSE 缓冲（Nginx proxy_buffering off 已修）、upload 内存预检（已修）
- 注释过时：6 处与代码事实不符的注释已修正
- v3.0 检索阈值：nomic-embed-text 的中文相似度分布，相关 0.70+ 无关 0.60-，阈值设为 0.65；eval 基线的 3 题未命中（聚簇索引/乐观锁/动态规划）为检索质量固有局限
- 预置语料 seed 脚本需在 backend/ 目录下运行（因为读 backend/.env 配置）
- 本地 Ollama 首次需拉模型 ~275MB，已预拉好；容器重建后 ollama_data 卷保留模型
- 前端布局重构：KeepAlive 动态组件用 viewRef 调用 HomeView.refreshList()（登录/退出后刷新列表），其他视图无此方法用可选链跳过
- 其余已知问题同上版本
