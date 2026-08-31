# 项目：AI 简历分析 + AI 模拟面试

> **本文件是干嘛的**：项目"户口本"——技术栈、启动命令、代码约定、铁律都记在这里。AI 每次会话开工必读；只在阶段切换或约定变更时更新。

## 当前状态
- 当前阶段：阶段0（施工中）
- 项目根目录：E:\AIDevelop\AIProject
- 权威计划：PROJECT-PLAN.md（改需求先改它；阶段0 目录结构已在其 §7 定稿）
- 协作约定：AI-COLLABORATION.md（每次会话先读本文件和 PROGRESS.md）

## 技术栈
- 后端：FastAPI + Postgres 16 + SQLAlchemy 2 + Alembic（阶段4 加 Celery/Redis）
- 前端：Vue 3 + Vite（JavaScript 起步，配置文件用 vite.config.ts）
- AI：OpenAI 兼容协议（DeepSeek / 通义），Key 只放 backend/.env（阶段2 接入）
- 后台任务：V1 用 FastAPI BackgroundTasks，阶段4 换 Celery
- 测试：pytest（阶段0 起，冒烟测试）

## 启动命令
（阶段0 施工完成后本节补全）

## 代码约定
- commit message：conventional commits（feat: / fix: / docs: / test: / chore:）
- 数据库改动必须走 Alembic 迁移，禁止手动改表
- Python 代码带类型注解，格式化用 ruff
- 目录：后端代码在 backend/app；业务路由挂 /api 前缀（/health 例外，供基础设施检查）；阶段1 再加 app/api、app/schemas
- 环境变量只放 backend/.env；前端本地开发走 Vite 代理调 /api，天然同源，不加 CORS

## Git / GitHub 策略（用户 2026-08-31 指示，覆盖原计划的逐阶段推送）
- 现在：本地 git commit + 每阶段验收后打 tag，**不推送远端**
- 全部阶段（v1.0）完成后：一次性建 GitHub 仓库并推送全部提交与 tag

## 铁律
1. API Key 只放 backend/.env，.env 永不入库
2. 只做当前阶段的事，不顺手重构、不提前引入后续技术
3. 改动超过 3 个文件先出方案
4. 测试简历集（PROJECT-PLAN.md §6）是回归基准，改解析/提示词必须重跑
5. 执行任何命令前，先用一句大白话解释它做什么、动什么、有没有风险，解释完再执行
6. 写每个文件前，先说明这个文件是干嘛的、为什么这样命名；约定俗成的固定文件名（如 .gitignore、README.md）要指出"这是约定名，不能改"

## 环境现状（2026-08-31 复核）
- git 2.52.0 ✓（身份已配置：十四 / 2578415251@qq.com）
- Python 3.13.9 ✓
- Node.js 24.12.0 ✓
- Docker Desktop 29.7.2 + Compose v5.4.0 ✓（守护进程运行中）
- 端口 5432 / 8000 / 5173 空闲 ✓
