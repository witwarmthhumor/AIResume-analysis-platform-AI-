# 进度日志

> **本文件是干嘛的**：项目进度日志——当前进行、已完成、踩过的坑、下一步。AI 每次新会话先读它接续上下文；每次会话结束前更新本文件。
> 最后更新：2026-09-01（阶段5实现会话）。

## 当前进行
- 阶段5 实现完成，**待用户验收全套 Docker 部署 → 通过后合并 main 并打 tag v1.0（仅本地，不推送）**
- 当前分支：stage-5-deploy-polish；阶段4 已封版 v0.5

## 已完成
- 项目规划定稿 / 记忆文件落成 / 阶段0 封版 v0.1 / 阶段1 封版 v0.2 / 阶段2 封版 v0.3 / 阶段3 封版 v0.4
- 阶段2（AI 简历分析）：后端封装层（JSON 校验重试/4xx 分类话术）+ 分析接口（去重/限流/留痕/记账）+ 前端报告页（六块内容 + token 可见 + 错误提示）；18 个 pytest 全过、ruff 全绿、npm build 通过
- 阶段3（文字模拟面试）：四阶段状态机、SSE 逐段流式回复、消息落库恢复、最大轮次、结束评价报告、面试消息限流；26 个 pytest 全过、ruff 全绿、npm build 通过；DeepSeek 真机完整走通一轮并验证会话恢复与 usage 记账
- 阶段4 已完成并封版：users 表与 Alembic 迁移、Argon2/JWT HttpOnly Cookie 认证、简历/分析/面试用户归属隔离、Redis 7 + Celery 业务任务及状态接口、登录/注册/退出/历史记录前端；30 个 pytest 全过、ruff 全绿、npm build 通过
- Celery 真机验证：Redis healthy，health-check 任务提交后返回 success；解析任务会更新 resumes，分析任务会写入 analyses，提交接口会校验用户归属
- 阶段4封版：浏览器验收通过，合并回 main，tag v0.5（本地）
- 阶段5部署实现：后端/前端生产 Dockerfile、Nginx SPA fallback 与 `/api` 反代、生产 compose（db/redis/backend/worker/frontend）、健康就绪探针、uploadsdata 持久卷、Docker 安全忽略规则与部署文档；五容器栈已在 8080 本地全新构建启动验证
- 真机 E2E：DeepSeek 出完整报告（6.2s）+ 缓存去重 + 换模型验证（通义失败路径/DeepSeek 成功路径）
- 测试简历集：test-resumes/ 5 份 PDF + 生成脚本 scripts/generate_test_resumes.py

## 已知问题 / 踩过的坑
- **通义账户欠费**（Arrearage）：DashScope 拒绝计费请求（400）。已切 DeepSeek；如需回通义先充值
- **ezlinkapi.top 不可达**：80/443 握手均被重置（DNS 可解析），从本机网络无法连通该境外代理服务
- **fpdf2 生成中文 PDF 两个坑**：① simhei(GB2312) 缺 •(U+2022) 字形，bullet 改用 `- `；② multi_cell 结束后 x 停在右边距需手动归位，否则下个块宽度为 0 报错
- **Windows curl 传中文文件名乱码**：curl 用系统 GBK 编码发 multipart filename，浏览器上传是 UTF-8 无此问题（属测试工具现象，非后端 bug）
- Vite 代理 404 坑（已修）/ 残留进程占端口 / Docker 手动启动 / Vite 只绑 IPv6（沿用历史记录）

## 下一步
- 用户浏览器验收生产 Docker 页面（推荐临时端口 8080）：首页、登录/历史、API 反代与刷新 fallback
- 云服务器部署需用户提供服务器与域名；部署时复制 `backend/.env.docker.example` 为 `.env.docker` 并填写真实密钥
- 验收通过后合并 stage-5-deploy-polish 回 main，打本地 tag v1.0
