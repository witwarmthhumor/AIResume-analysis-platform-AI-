# Agent v2 端到端黄金任务评测报告

- 时间：2026-09-26T00:00:43+08:00
- 结果：**11/11 通过（100%）**，门槛 ≥90%
- 结论：✅ 达标
- 执行方式：离线确定性（能力层打桩，不烧 LLM 额度）

| 任务 | 场景 | 结果 | 说明 |
|---|---|---|---|
| 正常链路 | `normal_full_flow` | ✅ | plan→…→approval_required→completed，产物齐全 |
| 审批通过副作用 | `approve_side_effect` | ✅ | 批准前 0 副作用，批准后场次 +1 + audit 留痕 |
| 审批拒绝 | `reject_no_session` | ✅ | 拒绝后照常交付，场次 +0，审批单 rejected |
| 审批超时 | `approval_expired` | ✅ | TTL 到点 approve 410，abort 后 run=aborted、审批单=expired |
| 无简历 | `no_resume_guidance` | ✅ | 无简历 fail 且话术引导上传 |
| 坏输出回环 | `bad_output_retry` | ✅ | 两次空产物触发回环，第三次恢复进审批 |
| 无报告补分析 | `no_report_reanalyze` | ✅ | need_analysis 命中补跑 analyzer，链路继续 |
| 已有分析跳过 | `skip_analysis` | ✅ | 已有有效分析跳过 analyzer，直达匹配出题 |
| 检索不可用降级 | `kb_degraded` | ✅ | kb 挂掉后照常出题且话术声明不来自知识库，sources 为空 |
| 限额命中 | `daily_limit_429` | ✅ | 限额命中 429 + 话术明确 + 无 run 落库 |
| 越权访问 | `owner_isolation` | ✅ | 他人 run 读与审批均 404 |
