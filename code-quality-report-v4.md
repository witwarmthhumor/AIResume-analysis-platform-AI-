# 质量审计报告 —— AIResume v4.0 LangGraph 试点（backend + frontend 新增代码）

> 审计时间：2026-09-26 · 审计方式：quality-engineer agent 三维度（安全/注释/通用质量）
> 抽检范围：v4.0 全部 13 个新增/改动文件全量审读，交叉核对 deps.py、usage_service、models/base.py、.gitignore 与 git 跟踪状态；ruff check 全过。

## 一、安全（security-audit）

**🔴 高危：0 处。无真实泄露的密钥，无需轮换。**

### ✅ 已正确处理（重点核过）
- **SQL 注入**：graph.py 全部 text() 原生 SQL（RunRecorder.status L104-110、finish L144-160）均为 :s/:n/:e/:id 绑定参数，无一处拼接；eval_agent_e2e.py、tests/test_agent_v2.py 的 DELETE/INSERT/UPDATE 全部参数化；admin_list_runs 的 status 过滤走 ORM 表达式。
- **归属/越权**：_get_owned_run→matches_owner（读/审批/重试/放弃/续听全覆盖）、list_runs→owner_clause、匿名口径为 user_id IS NULL AND anonymous_id 相等，与项目铁律一致；admin 接口全走 _admin_only；e2e 的 owner_isolation 任务实测 B 读/审批 A 均 404。
- **.env**：git ls-files 确认未跟踪（仅 .env.example），.gitignore 覆盖 .env、backend/.env、backend/.env.docker。
- **SSE 泄露**：对外 fatal 用固定话术；异常仅 type(exc).__name__ 落 DB error 字段（仅 admin 可见）；_dsn() 重写连接串但从不打印；前端全部 Vue 插值渲染（自动转义），无 v-html。
- **依赖注入面**：graph.py 节点只接 (state, config)，无用户输入进 eval/exec/路径拼接。

### 🟢 低危 / 建议
- backend/app/api/agent_v2.py:163 / :474 —— body.session_id 未经存在性/归属校验直接入库。当前泄露面≈0（纯关联数字），但属脏数据入口。修法：传入时走 get_owned_chat_session 校验或置空。
- backend/tests/test_agent_v2.py:30 等处测试密码 correct-horse-123 —— 测试假数据，不构成泄露。

## 二、注释质量（comments-check）—— 总评 8.5/10

| 文件 | 覆盖率 | 评分 |
|---|---|---|
| services/agent_v2/graph.py | 达标（docstring+关键"为什么"齐备） | 9/10 |
| services/agent_v2/event_bus.py | 达标（单 worker 假设/缓冲语义讲清） | 9/10 |
| api/agent_v2.py | 达标，三处 event_stream 零注释 | 7/10 |
| models/agent_v2.py | 达标（枚举值逐列注明） | 9/10 |
| services/agent_capabilities.py | 超标（解释"为何返回话术不抛异常"） | 10/10 |
| services/interview_service.py | 达标 | 9/10 |
| scripts/eval_agent_e2e.py | 达标，两处与事实不符 | 7/10 |
| alembic/versions/08c3d31e023c_….py | 超标（死锁四方互等的故事，教科书级） | 10/10 |
| tests/test_agent_v2.py | 达标 | 9/10 |
| components/agent/AgentRunView.vue | 达标 | 8/10 |
| api.js / AgentChatView.vue / AdminPanel.vue | 达标 | 9/10 |

### ✅ 做得好的地方
- graph.py:604-606/:660「setup() 必须在 try 内」、:538-539 条件边键位错位警告、:388 invoke 返回值非迭代器——三条实战教训全部沉淀为行内注释。
- agent_v2.py:171-174 双 commit 的 idle-in-transaction 死锁说明与迁移文件互相印证。

### 不符项（并入 P2/P3）
- eval_agent_e2e.py:7/:18 docstring 写"10 个任务"，实际 catalog 为 11 个。
- eval_agent_e2e.py:184 _drain_events 的 until 参数收下后从未使用（自 tests 版本复制时丢了判断）。
- AgentRunView.vue:119 注释"服务端 30s 防御性断流"已过时（实际 90s）；tests/test_agent_v2.py:197 同。
- AgentRunView.vue:123-124/143-145 snapshot 注释与实际恢复路径不符。

## 三、通用质量

### P0（阻断/安全漏洞）
无。

### P1（应尽快修，3 处）
1. **event_bus.py:18-26,38-43 + agent_v2.py:221,375,512 —— 多订阅者共享同一事件 dict，消费端 pop("type") 破坏性修改，双连接并存时第二消费者 KeyError 崩流。** publish 把同一个 dict 引用 append 进每个订阅者的 deque，replay（buf.extend(existing[0])）也复制引用；消费端 event.pop("type") 会改掉共享对象。触发条件：同一 run 两个订阅者并存（双标签页恢复同一任务、或旧 SSE 连接尚未 unsubscribe 时客户端重连）。修法：publish/replay 时按订阅者 append dict(event) 拷贝（最小改动），或消费端改 event.get("type") 不修改 dict。
2. **agent_v2.py:329-358 —— stream_run 的 event_stream 在流式响应期间使用请求作用域 db（db.get、_pending_approval 内可能 commit），90s 流期间事务保持 idle in transaction。** checkpoint 表预建后 CONCURRENTLY 死锁不再触发，但 a) 长事务阻碍 vacuum；b) 未来任何 DDL 与流并发会复现死锁；c) 与"请求事务在流式化前显式结束"的新约定不一致。修法：在 return StreamingResponse 之前把快照与 pending approval 查好、显式 db.commit()，流内只用纯内存数据。
3. **agent_v2.py:387-436 vs :524-551 —— approve 与 abort 的状态竞态。** 两接口都以 run.status == "waiting_approval" 为前置检查，无锁。并发时 approve 先置 approved 并启动 resume 线程，abort 仍能把 run 置 aborted；resume 线程随后 recorder.finish("completed") 覆盖 aborted——审批留痕与 run 终态矛盾。修法：条件更新乐观锁（UPDATE agent_runs SET status=... WHERE id=:id AND status='waiting_approval' 检查影响行数）或 SELECT ... FOR UPDATE。

### P2（建议修，6 处）
4. agent_v2.py:249-252,564-569,657-675 —— 全表加载后内存分页/聚合（list_runs/admin_list_runs/admin_run_stats）。run 表增长后 O(n) 内存与延迟。修法：count() + LIMIT/OFFSET 下推；统计改 SQL 聚合 + created_at 窗口条件。
5. AgentRunView.vue:143-145 —— applySnapshot 引用 snap.approval，后端 snapshot payload（agent_v2.py:331-342）无此字段，分支永不生效。修法：删死分支，或后端 snapshot 补 approval 字段。
6. graph.py:343-348 —— hitl_gate 幂等复用审批单时不校验状态/过期。TTL 到点后 approve 端 410、图端仍挂起，任务卡 waiting_approval 只能 abort。修法：复用前检查 expires_at，过期则建新单或走 fail。
7. eval_agent_e2e.py:7,18 docstring"10 个任务"与实际 11 个不符。
8. eval_agent_e2e.py:183-192 _drain_events 的 until 死参数 + docstring 不符。
9. interview_service.py:26 与调用链的自述缺口 —— docstring 说"调用方负责简历归属校验"，graph.deliver 未显式二次校验（实际链路安全，属防御纵深缺失）。修法：create_session 内补归属检查。

### P3（风格与打磨）
10. agent_v2.py 三处 event_stream 重复实现 idle 轮询/断流；import time 函数体内 3 处；魔法数字 1800 出现 3 次。修法：提取公共生成器 + _MAX_IDLE_TICKS 常量。
11. graph.py:483 —— _wrap_node 每次节点执行都 _make_nodes(deps) 重建全部 10 个闭包；构建一次挂到 deps 即可。
12. graph.py:565-566 —— _make_deps 内动态定义 class _Deps；用 SimpleNamespace/dataclass 更直白。
13. api.js streamChat/streamGet 除 method 外完全重复，可合并。
14. models/agent_v2.py:37-39 三列未写 mapped_column，同文件风格不一致。
15. 服务重启后 status='running' 的 run 成为孤儿（无 watchdog），可考虑启动时标记 failed 或超时回收。
16. AgentRunView.vue:119、tests/test_agent_v2.py:197 的"30s"过时文案。

## 汇总
- **风险总览**：安全 高/中/低 = 0/0/2（低危均为防御性建议）；注释评分 8.5/10（严重 0）；通用质量 P1×3、P2×6、P3×4。
- **最优先处理 3 件事**：① event_bus 事件对象共享 + pop 破坏性修改（会崩 SSE 流）；② stream_run 流内长事务（死锁模式残留）；③ approve/abort 加条件更新乐观锁（审批审计一致性）。
- **一句话总评**：v4.0 试点代码安全面干净（SQL 全参数化、归属口径统一、无泄露、ruff 全绿、注释沉淀三条实战教训），可以继续交付；P1 三项属并发边角的健壮性问题，建议下个迭代首批修复，不阻断本次门禁。

门禁通行证：.quality-gate/quality-engineer.marker（verdict=PASS，安全高危 0、注释严重 0）
