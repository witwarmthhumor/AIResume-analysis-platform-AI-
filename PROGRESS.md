# 进度日志

> **本文件是干嘛的**：项目进度日志——当前进行、已完成、踩过的坑、下一步。AI 每次新会话先读它接续上下文；每次会话结束前更新本文件。
> 最后更新：2026-08-31（阶段0 收尾会话）。

## 当前进行
- 阶段0 实现完成，**待用户在浏览器验收 + 打 tag v0.1（仅本地，不推送）**
  - 后端 /health、前端 5173、数据库三端已实测 200 联通；五张表已在库内确认

## 已完成
- 项目规划定稿（PROJECT-PLAN.md / AI-COLLABORATION.md / ROADMAP.md）
- 环境复核：git 2.52 / Python 3.13.9 / Node 24.12 / Docker 29.7.2 全部就绪
- 记忆文件落成实体（AGENTS.md / PROGRESS.md）
- 用户指示变更：GitHub 推送推迟到全部阶段完成后（已写入 AGENTS.md）
- 阶段0 全部代码：Postgres 容器（具名卷 + 健康检查）、FastAPI 骨架 + /health、Alembic 迁移建齐五张表、pytest 冒烟、Vue 首页联调
- ruff 全绿（自动修复 11 处导入/格式问题；BLE001 加 noqa 注释说明"健康检查故意捕获所有异常"）

## 已知问题 / 踩过的坑
- **Vite 代理 404 坑（已修）**：前端调 `/api/health`，代理原样透传到后端 → 404（后端 /health 按计划不带前缀）。修复：vite.config.ts 给 `/api/health` 单独加 rewrite → `/health`，其余 `/api/*` 原样透传
- **残留进程占端口**：上次会话的前端 dev server 未关闭占着 5173，新启动的 Vite 自动跳 5174。已清理，本项目固定 5173
- ROADMAP.md 与 PROJECT-PLAN.md 内容重叠，权威以 PROJECT-PLAN.md 为准（去留待用户决定）

## 下一步
- 用户浏览器验收（http://localhost:5173 三徽标应为 运行中/已连接/0.1.0）→ 通过后打 tag v0.1
- 阶段1 开工前：准备 5 份固定测试简历（PROJECT-PLAN §6）、申请大模型 API Key
