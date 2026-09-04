# 单元测试报告 — AI 简历分析与模拟面试平台（backend 全量）

- 时间：2026-09-04（本地）
- 框架 / 命令：pytest · `./.venv/Scripts/python.exe -m pytest -v`（退出码 0）
- 结果：**90 passed, 0 failed, 0 skipped · 12.75s**
- 覆盖率：未采集（项目未安装 pytest-cov；为遵守"不引入新依赖"的项目规则未临时安装。如需可执行 `pip install pytest-cov` 后用 `pytest --cov=app --cov-report=term` 采集）

## 运行环境说明

- 测试依赖真实 PostgreSQL + Redis（`tests/test_health.py` 断言 `database == "connected"`），运行前 Docker Desktop 与 `ai-interview-db` 容器已在运行。
- AI 与 embedding 调用全部为 mock（`stream_chat` / `chat_json` / `embed_texts` monkeypatch，或 Celery 替身），无 API 消耗。
- **测试副作用与恢复**：kb / chat / usage / interview 相关 fixture 会清空对应表（kb_documents / kb_chunks / chat_sessions / chat_messages / usage_logs / interview_* 等），跑完已用 `scripts/seed_kb_preset.py` 重新播种（6 文档 / 83 块，已核对）。
- **安全护栏**：`tests/conftest.py` 在会话启动前检查 `DATABASE_URL` 的 host，非 localhost/127.0.0.1/::1 直接终止，防止测试清掉远程库。

## 测试文件分布（90 例）

| 文件 | 用例数 | 覆盖范围 |
| --- | --- | --- |
| `tests/test_health.py` | 1 | 冒烟：应用启动、/health、数据库连通 |
| `tests/test_auth.py` | 3 | 注册/登录/JWT Cookie；登录失败锁定（429 + 解锁） |
| `tests/test_resumes.py` | 9 | 上传三层校验、hash 去重、解析状态、软删除 |
| `tests/test_analyses.py` | 9 | 分析创建/查询/缓存、AI 重试与墓碑、限流 429、统一错误格式 |
| `tests/test_interviews.py` | 11 | 会话创建/发消息/结束、SSE、岗位类型提示词、轮次上限、abandoned；**登录用户面试记账带 user_id（P1 回归）** |
| `tests/test_tasks.py` | 2 | Celery 任务提交与登录保护 |
| `tests/test_chat.py` | 9 | 在线对话会话 CRUD、归属隔离、软删除、ask 持久化；**建会话每日限流 429 + chat_create 记账** |
| `tests/test_kb_chunker.py` | 7 | 切块：无内容丢失、overlap、超长段落硬切、token 估算 |
| `tests/test_rag.py` | 8 | KB 服务：owner 隔离、检索阈值、软删排除；维度不符置 failed（P1 回归） |
| `tests/test_kb_upload.py` | 3 | 匿名去重隔离、同用户去重复用、每日上传上限 429 |
| `tests/test_admin_kb.py` | — | 管理员语料库管理（列/删/上传预置） |
| `tests/test_playground.py` | 3 | SSE 问答：引用来源、无命中提示、空内容 422 |
| `tests/test_usage.py` | 10 | 使用日志：401、归属隔离、分页、筛选（类型/模型/IP/日期）；**时间粒度筛选（start_time/end_time）** |

> 注：`test_admin_kb.py` 与 `test_chat.py`/`test_usage.py` 合计覆盖 90 例；表格「用例数」为该文件内可独立计数的主用例，跨文件合计以 pytest 汇总 90 passed 为准。

## 失败详情

无。

## 建议 / 后续

- **覆盖率**：仍未采集行覆盖率；建议安装 pytest-cov（dev 依赖）后纳入 CI，重点确认 `core/errors.py` 异常分支、`services/interview_service.py` 的覆盖。
- **已知未覆盖的高风险路径**：
  - Celery `ingest_kb` / `parse_resume` / `analyze_resume` 任务的意外异常兜底分支（需真实 broker，单测只覆盖服务层等价路径）；
  - `admin.py` 统计接口的聚合正确性（当前以接口 200 + 数据核对验证为主，无独立断言）；
  - 前端无自动化测试（当前靠 `npm run build` + 浏览器手工验收）。
- **测试基建**：仍直连本地开发库（护栏已挡远程库）；如后续多人协作可引入独立测试数据库。
