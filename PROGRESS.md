# 进度日志

> **本文件是干嘛的**：项目进度日志——当前进行、已完成、踩过的坑、下一步。AI 每次新会话先读它接续上下文；每次会话结束前更新本文件。
> 最后更新：2026-08-31（阶段0 施工会话）。

## 当前进行
- 阶段0 施工中（本会话）：按 PROJECT-PLAN.md §7 定稿目录结构搭建
  - 步骤：仓库初始化 → Postgres 容器 → 后端骨架 + /health → Alembic 五张表（建后在库内实际确认）→ pytest 冒烟 → 前端联调 → tag v0.1（仅本地，不推送）

## 已完成
- 项目规划定稿（PROJECT-PLAN.md / AI-COLLABORATION.md / ROADMAP.md）
- 环境复核：git 2.52 / Python 3.13.9 / Node 24.12 / Docker 29.7.2 全部就绪；端口 5432、8000、5173 空闲
- 记忆文件落成实体（AGENTS.md / PROGRESS.md）
- 用户指示变更：GitHub 推送推迟到全部阶段完成后（已写入 AGENTS.md）

## 已知问题 / 踩过的坑
- ROADMAP.md 与 PROJECT-PLAN.md 内容重叠，权威以 PROJECT-PLAN.md 为准（去留待用户决定）
- PROJECT-PLAN / AI-COLLABORATION 曾在会话间被更新：阶段0 新增 tests/、backend/.env.example、pytest 要求，协作规则新增第9条"命令带讲解"——已按新版本执行

## 下一步
- 阶段0 剩余：见"当前进行"
- 阶段1 开工前：准备 5 份固定测试简历（PROJECT-PLAN §6）、申请大模型 API Key
