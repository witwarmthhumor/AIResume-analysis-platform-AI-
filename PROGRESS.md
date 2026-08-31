# 进度日志

> **本文件是干嘛的**：项目进度日志——当前进行、已完成、踩过的坑、下一步。AI 每次新会话先读它接续上下文；每次会话结束前更新本文件。
> 最后更新：2026-09-01（阶段1 封版会话）。

## 当前进行
- 阶段1 已验收封版：分支 stage-1-resume-upload 合并回 main，tag v0.2（本地）
- 下阶段预告：阶段2（AI 简历分析）——开工前置条件：申请大模型 API Key（DeepSeek 或通义）

## 已完成
- 项目规划定稿（PROJECT-PLAN.md / AI-COLLABORATION.md / ROADMAP.md）
- 环境复核：git 2.52 / Python 3.13.9 / Node 24.12 / Docker 29.7.2 全部就绪
- 记忆文件落成实体（AGENTS.md / PROGRESS.md）
- 用户指示变更：GitHub 推送推迟到全部阶段完成后（已写入 AGENTS.md）
- 阶段0 全部代码：Postgres 容器（具名卷 + 健康检查）、FastAPI 骨架 + /health、Alembic 迁移建齐五张表、pytest 冒烟、Vue 首页联调
- ruff 全绿（自动修复 11 处导入/格式问题；BLE001 加 noqa 注释说明"健康检查故意捕获所有异常"）
- 阶段0 封版：用户验收通过，tag v0.1（本地）
- 阶段1 验收通过并封版：合并回 main，tag v0.2（本地）；用户用测试简历在浏览器逐项验收 OK
- 阶段1 后端：pypdf/python-multipart/fpdf2 依赖；config 上传限制（5MB/5页/uploads 目录）；schemas/resume.py；services/pdf_parser.py（文件头校验、页数检查、扫描件判定，换解析库只动这个文件）；api/resumes.py（上传/列表/详情，hash 去重）；9 个 pytest 全过，ruff 全绿
- 阶段1 前端：UploadCard.vue（本地预检 + 错误文案）、ResumeList.vue（状态徽标历史列表）、App.vue 重排（详情面板 + 页脚健康条）；未引入 router/axios，沿用 fetch
- 端到端验证：PDF 经 5173 代理上传 → 解析成功 → 纯文本正确返回（测试数据已清理）

## 已知问题 / 踩过的坑
- **Vite 代理 404 坑（已修）**：前端调 `/api/health`，代理原样透传到后端 → 404（后端 /health 按计划不带前缀）。修复：vite.config.ts 给 `/api/health` 单独加 rewrite → `/health`，其余 `/api/*` 原样透传
- **残留进程占端口（两次复现）**：上次会话的 uvicorn/Vite 未关闭，占 8000/5173。本次会话又遇到一次，已 taskkill 清理。**会话结束前记得关 dev server**
- **Docker Desktop 装在非默认路径**：实际路径 `C:\Users\ZhuanZ\AppData\Local\Programs\DockerDesktop\`，`C:\Program Files` 下没有；且不会随开机自启，需手动启动
- **Vite 只绑 IPv6**：`localhost:5173` 实际监听 `[::1]`，用 `127.0.0.1` curl 会连接失败，浏览器不受影响
- ROADMAP.md 与 PROJECT-PLAN.md 内容重叠，权威以 PROJECT-PLAN.md 为准（去留待用户决定）

## 下一步
- 阶段2 开工前：申请大模型 API Key（DeepSeek 或通义），录入 backend/.env
- 新会话按 AI-COLLABORATION §4 开场白模板开阶段2，先出方案再写代码
