# 单元测试报告 — AI 简历分析与模拟面试平台（backend 全量）

- 时间：2026-09-03 15:55（本地）
- 框架 / 命令：pytest · `./.venv/Scripts/python.exe -m pytest -v`（退出码 0）
- 结果：**55 passed, 0 failed, 0 skipped · 4.91s**
- 覆盖率：未采集（项目未安装 pytest-cov；为遵守"不引入新依赖"的项目规则未临时安装。如需可执行 `pip install pytest-cov` 后用 `pytest --cov=app --cov-report=term` 采集）

## 运行环境说明

- 测试依赖真实 PostgreSQL + Redis（`tests/test_health.py` 断言 `database == "connected"`），运行前 Docker Desktop 与 `ai-interview-db` 容器已在运行。
- AI 与 embedding 调用全部为 mock（`stream_chat` / `embed_texts` monkeypatch），无 API 消耗。
- **测试副作用与恢复**：kb 相关 fixture 会清空 `kb_documents` / `kb_chunks`，跑完已用 `scripts/seed_kb_preset.py` 重新播种（6 文档 / 83 块，已核对）。
- **安全护栏（本轮新增）**：`tests/conftest.py` 在会话启动前检查 `DATABASE_URL` 的 host，非 localhost/127.0.0.1/::1 直接终止，防止测试清掉远程库。

## 测试文件分布（55 例）

| 文件 | 用例数 | 覆盖范围 |
| --- | --- | --- |
| `tests/test_health.py` | 1 | 冒烟：应用启动、/health、数据库连通 |
| `tests/test_auth.py` | 3 | 注册/登录/JWT Cookie；**登录失败锁定（429 + 解锁）** |
| `tests/test_resumes.py` | 9 | 上传三层校验、hash 去重、解析状态、软删除 |
| `tests/test_analyses.py` | 9 | 分析创建/查询/缓存、AI 重试与墓碑、限流 429、统一错误格式 |
| `tests/test_interviews.py` | 10 | 会话创建/发消息/结束、SSE、岗位类型提示词、轮次上限、abandoned |
| `tests/test_tasks.py` | 2 | Celery 任务提交与登录保护 |
| `tests/test_kb_chunker.py` | 7 | 切块：无内容丢失、overlap、超长段落硬切、token 估算 |
| `tests/test_rag.py` | 8 | KB 服务：owner 隔离、检索阈值、软删排除；**维度不符置 failed（P1 回归）** |
| `tests/test_playground.py` | 3 | SSE 问答：引用来源、无命中提示、空内容 422 |
| `tests/test_kb_upload.py` | 3 | **匿名去重隔离、同用户去重复用、每日上传上限 429（本轮新增）** |

## 失败详情

无。

## 建议 / 后续

- **覆盖率**：仍未采集行覆盖率；上轮报告建议的 `pytest-cov` 纳入 CI 维持有效。
- **已补齐的盲区**：上轮列出的"登录防爆破""限流边界"已随质量修复补上回归测试；`core/errors.py` 异常类仍未被实际 raise（死代码，见质量审计 P3，待决定删除或启用）。
- **未覆盖的高风险路径**：Celery `ingest_kb` 任务的意外异常兜底分支（需真实 broker，单测只覆盖了服务层等价路径）；`admin.py` 的统计接口（当前仅有 403 隔离语义在手工验收中验证过）。
- **测试基建**：仍直连本地开发库（护栏已挡远程库）；如后续多人协作可引入独立测试数据库。
