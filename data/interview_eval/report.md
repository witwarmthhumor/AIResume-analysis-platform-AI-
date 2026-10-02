# 面试图编排黄金场景评测报告

- 时间：2026-10-02T15:49:55+00:00
- 结果：**8/8 通过（100.0%）**，门槛 ≥100%
- 说明：LLM 全部替身化（离线确定性）；DB 与 PostgresSaver 为真实组件

| 场景 | 结果 | 失败明细 |
|---|---|---|
| full_flow_3turns（完整三轮问答后正常出报告） | ✅ | - |
| finish_after_single_answer（一答即结：作答一轮后立即结束出报告） | ✅ | - |
| max_turns_finish_fallback（达上限后 send 被 400 拦、finish 兜底出报告（守卫优先于路由）） | ✅ | - |
| checkpoint_recovery（每轮请求重建图对象：续跑状态全部来自 PostgresSaver（崩溃恢复语义）） | ✅ | - |
| opening_idempotent（开场白幂等：完整流程跑完开场白只有一条） | ✅ | - |
| follow_up_on_weak_answer（弱回答触发面试官智能追问：追问不占轮次、追问后再答进入下一题（v4.5 面试官 Agent）） | ✅ | - |
| follow_up_budget_zero_disables（追问预算归零：同样弱回答直接进入下一题（追问可关）） | ✅ | - |
| usage_accounting（记账闭环：开局+作答的 LLM 消耗都写入 usage_logs） | ✅ | - |
