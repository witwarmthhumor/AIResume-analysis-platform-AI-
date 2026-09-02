# 单元测试报告 — AI 简历分析与模拟面试平台（backend 全量）

- 时间：2026-09-02 22:10（本地）
- 框架 / 命令：pytest · `./.venv/Scripts/python.exe -m pytest -q`（退出码 0）
- 结果：**33 passed, 0 failed, 0 skipped · 6.44s**
- 覆盖率：未采集（项目未安装 pytest-cov；为遵守"不引入新依赖"的项目规则未临时安装。如需可执行 `pip install pytest-cov` 后用 `pytest --cov=app --cov-report=term` 采集）

## 运行环境说明

- 测试依赖真实 PostgreSQL（`tests/test_health.py` 断言 `database == "connected"`），运行前已启动 Docker Desktop 并拉起 `ai-interview-db` 容器。
- AI 调用全部为 mock，无 API 消耗。

## 测试文件分布

| 文件 | 覆盖范围 |
| --- | --- |
| `tests/test_health.py` | 冒烟：应用启动、/health、数据库连通 |
| `tests/test_auth.py` | 注册/登录/JWT Cookie/首个用户自动 admin（P6） |
| `tests/test_resumes.py` | 上传三层校验、hash 去重、解析状态、软删除（P7） |
| `tests/test_analyses.py` | 分析创建/查询、统一错误格式（P2）、归属校验 |
| `tests/test_interviews.py` | 会话创建/发消息/结束、SSE、岗位类型（P5）、对话截断（P1）、超时 abandoned（P1） |
| `tests/test_tasks.py` | Celery 任务提交与归属校验 |

## 失败详情

无。

## 建议 / 后续

- **覆盖率盲区**：未采集行覆盖率，建议安装 pytest-cov（dev 依赖）后纳入 CI，重点确认 `services/interview_service.py`、`core/errors.py` 异常分支的覆盖。
- **未覆盖的高风险路径**（基于代码结构判断，未经覆盖率证实）：
  - AI 接口超时/限流重试边界（`ai_client.py` 的第 3 次重试失败路径）
  - `enforce_daily_limit` 恰好到达限额的边界值
  - 管理接口在匿名用户（无 Cookie）下的 401 路径
- **测试基建**：当前测试直连本地开发库，若与开发数据混用可能互相影响；可考虑引入独立测试数据库（如 `test` schema 或独立容器）。
