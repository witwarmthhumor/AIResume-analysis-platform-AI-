# AI 简历分析 + AI 模拟面试

面向求职者的简历智能分析与模拟面试工具。**个人学习 + 求职作品集项目。**

## 技术栈

- 后端：Python + FastAPI + PostgreSQL + SQLAlchemy / Alembic
- 前端：Vue 3 + Vite
- 基础设施：Docker（阶段0 起 Postgres 容器化，阶段5 全套容器化）
- AI：国产大模型 API（OpenAI 兼容协议，DeepSeek / 通义，阶段2 接入）

## 快速启动（阶段0 骨架）

前置：本机已装 Docker Desktop、Python 3.11+、Node.js 20+。

```bash
# 1. 启动数据库（仅 Postgres 容器）
docker compose up -d db

# 2. 启动后端（首次需创建虚拟环境并安装依赖，见 backend/）
cd backend
.venv\Scripts\python -m uvicorn app.main:app --reload

# 3. 启动前端（首次需 npm install）
cd frontend
npm run dev
```

打开 http://localhost:5173 ，首页应显示后端服务与数据库的连接状态。

## 文档

| 文件 | 说明 |
|---|---|
| PROJECT-PLAN.md | 权威项目计划（阶段划分、表结构、验收标准） |
| AI-COLLABORATION.md | 人 + AI 协作约定 |
| AGENTS.md | 项目技术约定（AI 会话必读） |
| PROGRESS.md | 进度日志 |

## 隐私说明

简历属于敏感个人信息：仅用于用户主动发起的分析；上传时明确提示内容将发送给第三方大模型服务；提供简历删除入口。
