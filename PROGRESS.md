# 进度日志

> **本文件是干嘛的**：项目进度日志——当前进行、已完成、踩过的坑、下一步。AI 每次新会话先读它接续上下文；每次会话结束前更新本文件。
> 最后更新：2026-09-03（v3.0 Playground 知识库问答封版）。

## 当前进行
- v3.0 Playground 知识库问答已封版，tag v3.0（本地）
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

## 已知问题 / 踩过的坑
- 安全审计：tasks 接口无认证（已修复）、SSE 缓冲（Nginx proxy_buffering off 已修）、upload 内存预检（已修）
- 注释过时：6 处与代码事实不符的注释已修正
- v3.0 检索阈值：nomic-embed-text 的中文相似度分布，相关 0.70+ 无关 0.60-，阈值设为 0.65；eval 基线的 3 题未命中（聚簇索引/乐观锁/动态规划）为检索质量固有局限
- 预置语料 seed 脚本需在 backend/ 目录下运行（因为读 backend/.env 配置）
- 本地 Ollama 首次需拉模型 ~275MB，已预拉好；容器重建后 ollama_data 卷保留模型
- 其余已知问题同上版本
