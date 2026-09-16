# Agent 工具路由评测报告

- 时间：2026-09-16 12:24
- 模型：deepseek-chat（temperature 0）
- 用例集：routing.json（33 题 / 11 个工具）
- 口径：每题一次 LLM 调用做工具选择，工具执行体不运行，只统计选中的工具名

## 汇总

- top-1 准确率：33/33 (100.0%)
- 命中 33 题，误选 0 题
- 对比 8 工具口径基线（US-007）（24/24）：+0.0 个百分点

## 分工具命中率

| 期望工具 | 用例数 | 命中 | 命中率 |
| --- | --- | --- | --- |
| kb_search | 3 | 3 | 100% |
| resume_lookup | 3 | 3 | 100% |
| interview_history | 3 | 3 | 100% |
| score_trend | 3 | 3 | 100% |
| usage_stats | 3 | 3 | 100% |
| analysis_read | 3 | 3 | 100% |
| kb_list | 3 | 3 | 100% |
| platform_help | 3 | 3 | 100% |
| job_match | 3 | 3 | 100% |
| question_gen | 3 | 3 | 100% |
| answer_review | 3 | 3 | 100% |

## 混淆矩阵（行=期望工具，列=实际选中）

| 期望 \ 实际 | kb_search | resume_lookup | interview_history | score_trend | usage_stats | analysis_read | kb_list | platform_help | job_match | question_gen | answer_review | 合计 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| kb_search | 3 | - | - | - | - | - | - | - | - | - | - | 3 |
| resume_lookup | - | 3 | - | - | - | - | - | - | - | - | - | 3 |
| interview_history | - | - | 3 | - | - | - | - | - | - | - | - | 3 |
| score_trend | - | - | - | 3 | - | - | - | - | - | - | - | 3 |
| usage_stats | - | - | - | - | 3 | - | - | - | - | - | - | 3 |
| analysis_read | - | - | - | - | - | 3 | - | - | - | - | - | 3 |
| kb_list | - | - | - | - | - | - | 3 | - | - | - | - | 3 |
| platform_help | - | - | - | - | - | - | - | 3 | - | - | - | 3 |
| job_match | - | - | - | - | - | - | - | - | 3 | - | - | 3 |
| question_gen | - | - | - | - | - | - | - | - | - | 3 | - | 3 |
| answer_review | - | - | - | - | - | - | - | - | - | - | 3 | 3 |

## 逐题明细

| # | 问题 | 期望 | 实际 | 命中 |
| --- | --- | --- | --- | --- |
| 1 | Redis 缓存穿透和缓存雪崩有什么区别，一般怎么解决 | kb_search | kb_search | ✅ |
| 2 | Java 里 synchronized 和 ReentrantLock 该怎么选 | kb_search | kb_search | ✅ |
| 3 | TCP 三次握手为什么不能改成两次 | kb_search | kb_search | ✅ |
| 4 | 我简历里写过 Kubernetes 吗 | resume_lookup | resume_lookup | ✅ |
| 5 | 我一共上传了几份简历，分别叫什么名字 | resume_lookup | resume_lookup | ✅ |
| 6 | 帮我看看我简历里有没有提到消息队列相关的项目经历 | resume_lookup | resume_lookup | ✅ |
| 7 | 我上一场模拟面试拿了多少分 | interview_history | interview_history | ✅ |
| 8 | 我最近一次模拟面试的评价是怎么说的 | interview_history | interview_history | ✅ |
| 9 | 我一共练过几场模拟面试 | interview_history | interview_history | ✅ |
| 10 | 我最近的面试表现是进步了还是退步了 | score_trend | score_trend | ✅ |
| 11 | 我这几场模拟面试的分数有没有提升 | score_trend | score_trend | ✅ |
| 12 | 跟前几次比，我这次面试表现变好了吗 | score_trend | score_trend | ✅ |
| 13 | 我最近一周用了多少次 AI 分析 | usage_stats | usage_stats | ✅ |
| 14 | 我这个月在平台上消耗了多少 token | usage_stats | usage_stats | ✅ |
| 15 | 我最近平台用得多不多，帮我统计一下用量 | usage_stats | usage_stats | ✅ |
| 16 | 我的简历分析报告说我有哪些短板 | analysis_read | analysis_read | ✅ |
| 17 | 上次 AI 分析给我的结论是什么 | analysis_read | analysis_read | ✅ |
| 18 | 我简历缺哪些关键词，分析报告里怎么说的 | analysis_read | analysis_read | ✅ |
| 19 | 知识库里都有哪些资料 | kb_list | kb_list | ✅ |
| 20 | 平台知识库收录了哪些文档 | kb_list | kb_list | ✅ |
| 21 | 我能在这个平台上问哪些方向的技术问题 | kb_list | kb_list | ✅ |
| 22 | 怎么上传简历 | platform_help | platform_help | ✅ |
| 23 | 模拟面试在哪里开始 | platform_help | platform_help | ✅ |
| 24 | 使用日志在哪看 | platform_help | platform_help | ✅ |
| 25 | 这家公司要求熟悉 MySQL 和 K8s，我匹配吗 | job_match | job_match | ✅ |
| 26 | 招聘要求：熟悉 MySQL 和 Redis，三年以上后端经验。帮我看看我简历还缺哪些关键词 | job_match | job_match | ✅ |
| 27 | 岗位要求熟悉 Spring Cloud 和 K8s，有高并发项目经验，按这个要求我的简历该怎么改 | job_match | job_match | ✅ |
| 28 | 给我出几道 MySQL 索引的面试题 | question_gen | question_gen | ✅ |
| 29 | 帮我练一下 Redis 缓存，出几道题考考我 | question_gen | question_gen | ✅ |
| 30 | 来一组校招难度的 Java 并发面试题 | question_gen | question_gen | ✅ |
| 31 | 我这么答行不行：索引失效主要是因为用了函数或者隐式类型转换，所以要避免在列上做运算 | answer_review | answer_review | ✅ |
| 32 | 帮我看看我这段回答能得几分：Redis 缓存穿透我一般用布隆过滤器挡一层，空值也缓存一下 | answer_review | answer_review | ✅ |
| 33 | 刚才那道题（Redis 缓存穿透怎么解决）我是这样答的：先用布隆过滤器挡一层，空值也缓存一下。帮我点评一下哪里还能改 | answer_review | answer_review | ✅ |

## 误选清单

- 无（全部命中）