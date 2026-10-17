# RAG 检索评测报告

- 时间：2026-10-13 18:01
- embedding：nomic-embed-text（dim 768）
- 切块：约 600 字/块，重叠 60
- 黄金问答集：qa.json（49 题）
- 检索模式：vector（纯向量，v3.0 基线）/ hybrid（向量 + BM25，RRF 融合，生产路径）/ hybrid_rerank（hybrid 之上叠加 cross-encoder 重排序）
- rerank：provider=onnx · model=Xenova/bge-reranker-base · 候选 10 条 · 超时 3000ms · 实际调用 49 次 / 降级 0 次 / 单次均耗时 993ms

## 汇总对比

| 指标 | vector（基线） | hybrid（当前） | hybrid_rerank | Δ(hybrid→rerank) |
| --- | --- | --- | --- | --- |
| hit@1（首块即来自预期文档） | 35/49 (71.4%) | 43/49 (87.8%) | 48/49 (98.0%) | +5 |
| hit@5（前5块含预期文档） | 45/49 (91.8%) | 49/49 (100.0%) | 49/49 (100.0%) | +0 |
| MRR@5（首个正确块位次均值） | 0.8010 | 0.9354 | 0.9898 | +0.0544 |
| nDCG@5（排序质量） | 0.6045 | 0.7317 | 0.7686 | +0.0369 |
| 平均 top1 相似度 | 0.7328 | 0.7182 | 0.6893 | — |

> hit@1 71.4% → 87.8% → 98.0%；hit@5 91.8% → 100.0% → 100.0%。

## 翻转明细

### hybrid 相对 vector（v3.5 混合检索的收益）

| 题目 | 变化 | vector top1 | hybrid top1 |
| --- | --- | --- | --- |
| 垃圾回收有哪些算法，新生代和老年代怎么回收 | ✅ 救回 | job_descriptions | java_interview |
| 线程池的核心参数和执行流程 | ✅ 救回 | system_design | java_interview |
| 什么是死锁，怎么避免死锁 | ✅ 救回 | job_descriptions | java_interview |
| 什么是缓存穿透、缓存击穿、缓存雪崩 | ✅ 救回 | job_descriptions | system_design |
| 如何设计一个实时排行榜 | ✅ 救回 | ai_application_development | system_design |
| 为什么用消息队列 | ✅ 救回 | job_descriptions | java_interview |
| 进程和线程的区别 | ✅ 救回 | algorithm_interview | network_interview |
| 哈希冲突怎么处理 | ✅ 救回 | job_descriptions | algorithm_interview |

### hybrid_rerank 相对 hybrid（v4.6 重排序的收益）

| 题目 | 变化 | 首个正确块位次 | hybrid top1 | rerank top1 |
| --- | --- | --- | --- | --- |
| 类的加载过程包含哪些阶段 | ✅ 提到首位 | 2 → 1 | job_descriptions | java_interview |
| 聚簇索引和非聚簇索引的区别 | ✅ 提到首位 | 2 → 1 | algorithm_interview | database_interview |
| 乐观锁和悲观锁有什么区别 | ✅ 提到首位 | 3 → 1 | network_interview | database_interview |
| 怎么用 EXPLAIN 分析慢查询 | ❌ 挤出首位 | 1 → 2 | database_interview | java_interview |
| 分布式系统怎么保证最终一致性 | ✅ 提到首位 | 2 → 1 | database_interview | java_interview |
| 动态规划适合解决什么问题 | ✅ 提到首位 | 2 → 1 | java_interview | algorithm_interview |
| 怎么实现一个 LRU 缓存 | ✅ 提到首位 | 2 → 1 | java_interview | algorithm_interview |

## 逐题明细

| 题目 | 预期文档 | vector top1 | hybrid top1 | rerank top1 | rerank hit@5 |
| --- | --- | --- | --- | --- | --- |
| HashMap 的底层实现原理是什么 | java_interview | java_interview (0.795) | java_interview (0.795) | java_interview (0.795) | ✅ |
| synchronized 和 ReentrantLock 有什么区别 | java_interview | java_interview (0.827) | java_interview (0.827) | java_interview (0.827) | ✅ |
| volatile 关键字的作用是什么，能保证原子性吗 | java_interview | java_interview (0.659) | java_interview (0.633) | java_interview (0.633) | ✅ |
| ConcurrentHashMap 是怎么保证线程安全的 | java_interview | java_interview (0.717) | java_interview (0.705) | java_interview (0.717) | ✅ |
| JVM 的内存区域怎么划分 | java_interview | java_interview (0.788) | java_interview (0.669) | java_interview (0.669) | ✅ |
| 垃圾回收有哪些算法，新生代和老年代怎么回收 | java_interview | job_descriptions (0.692) | java_interview (0.669) | java_interview (0.669) | ✅ |
| 类的加载过程包含哪些阶段 | java_interview | job_descriptions (0.662) | job_descriptions (0.648) | java_interview (0.590) | ✅ |
| Spring Bean 的生命周期是怎样的 | java_interview | java_interview (0.775) | java_interview (0.775) | java_interview (0.775) | ✅ |
| Spring 事务传播行为有哪些，REQUIRED 和 REQUIRES_NEW 区别 | java_interview | java_interview (0.703) | java_interview (0.703) | java_interview (0.703) | ✅ |
| Spring 怎么解决循环依赖 | java_interview | java_interview (0.698) | java_interview (0.697) | java_interview (0.697) | ✅ |
| Spring AOP 的实现原理是什么 | java_interview | java_interview (0.768) | java_interview (0.765) | java_interview (0.765) | ✅ |
| 线程池的核心参数和执行流程 | java_interview | system_design (0.697) | java_interview (0.663) | java_interview (0.663) | ✅ |
| 什么是死锁，怎么避免死锁 | java_interview | job_descriptions (0.679) | java_interview (0.618) | java_interview (0.618) | ✅ |
| ThreadLocal 有什么使用隐患 | java_interview | java_interview (0.822) | java_interview (0.822) | java_interview (0.822) | ✅ |
| String 为什么是不可变的 | java_interview | java_interview (0.715) | java_interview (0.715) | java_interview (0.668) | ✅ |
| MySQL 为什么要用 B+ 树做索引 | database_interview,java_interview | java_interview (0.781) | java_interview (0.781) | java_interview (0.727) | ✅ |
| 聚簇索引和非聚簇索引的区别 | database_interview,java_interview | algorithm_interview (0.704) | algorithm_interview (0.704) | database_interview (0.552) | ✅ |
| MySQL 的四种隔离级别是什么，InnoDB 默认哪个 | java_interview,database_interview | java_interview (0.875) | java_interview (0.827) | java_interview (0.872) | ✅ |
| 什么是 MVCC，它是怎么实现的 | java_interview,database_interview | java_interview (0.788) | java_interview (0.748) | java_interview (0.748) | ✅ |
| 什么是 SQL 注入，怎么防范 | database_interview | database_interview (0.793) | database_interview (0.793) | database_interview (0.793) | ✅ |
| 数据库事务的 ACID 特性是什么 | database_interview | database_interview (0.714) | database_interview (0.714) | database_interview (0.714) | ✅ |
| 为什么要分库分表，有哪些策略 | database_interview | database_interview (0.698) | database_interview (0.698) | database_interview (0.698) | ✅ |
| 怎么优化大分页查询 | database_interview | database_interview (0.707) | database_interview (0.707) | database_interview (0.554) | ✅ |
| 乐观锁和悲观锁有什么区别 | database_interview | network_interview (0.650) | network_interview (0.650) | database_interview (0.475) | ✅ |
| 怎么用 EXPLAIN 分析慢查询 | database_interview | database_interview (0.672) | database_interview (0.672) | java_interview (0.561) | ✅ |
| 什么是缓存穿透、缓存击穿、缓存雪崩 | system_design,java_interview | job_descriptions (0.670) | system_design (0.603) | system_design (0.603) | ✅ |
| 如何设计一个短链接系统 | system_design | system_design (0.685) | system_design (0.675) | system_design (0.675) | ✅ |
| 如何设计一个秒杀系统 | java_interview,system_design | system_design (0.684) | system_design (0.671) | java_interview (0.587) | ✅ |
| 如何设计一个实时排行榜 | system_design | ai_application_development (0.720) | system_design (0.681) | system_design (0.530) | ✅ |
| 如何设计分布式 ID 生成器 | system_design | system_design (0.772) | system_design (0.772) | system_design (0.772) | ✅ |
| 分布式系统怎么保证最终一致性 | java_interview,system_design | database_interview (0.769) | database_interview (0.769) | java_interview (0.648) | ✅ |
| CAP 定理和 BASE 理论是什么 | java_interview | java_interview (0.693) | java_interview (0.693) | java_interview (0.693) | ✅ |
| 为什么用消息队列 | java_interview | job_descriptions (0.705) | java_interview (0.681) | java_interview (0.635) | ✅ |
| TCP 三次握手为什么是三次 | network_interview | network_interview (0.870) | network_interview (0.870) | network_interview (0.870) | ✅ |
| TCP 四次挥手为什么要 TIME_WAIT | network_interview | network_interview (0.849) | network_interview (0.849) | network_interview (0.849) | ✅ |
| TCP 和 UDP 有什么区别 | network_interview,java_interview | network_interview (0.705) | java_interview (0.697) | java_interview (0.697) | ✅ |
| HTTP 和 HTTPS 的区别是什么 | network_interview,java_interview | java_interview (0.721) | network_interview (0.703) | java_interview (0.595) | ✅ |
| 进程和线程的区别 | network_interview | algorithm_interview (0.673) | network_interview (0.672) | network_interview (0.672) | ✅ |
| 阻塞 IO 和非阻塞 IO 的区别 | network_interview | network_interview (0.748) | network_interview (0.748) | network_interview (0.748) | ✅ |
| cookie、session、token 的区别 | network_interview | network_interview (0.790) | network_interview (0.790) | network_interview (0.790) | ✅ |
| 数组和链表的区别与适用场景 | algorithm_interview | algorithm_interview (0.688) | algorithm_interview (0.688) | algorithm_interview (0.688) | ✅ |
| 哈希冲突怎么处理 | algorithm_interview | job_descriptions (0.705) | algorithm_interview (0.662) | algorithm_interview (0.662) | ✅ |
| 动态规划适合解决什么问题 | algorithm_interview | java_interview (0.672) | java_interview (0.672) | algorithm_interview (0.540) | ✅ |
| 怎么实现一个 LRU 缓存 | algorithm_interview | network_interview (0.765) | java_interview (0.668) | algorithm_interview (0.744) | ✅ |
| 什么是微服务架构 | java_interview,system_design,job_descriptions | job_descriptions (0.705) | system_design (0.692) | java_interview (0.604) | ✅ |
| Java 后端开发岗位的任职要求是什么 | job_descriptions | job_descriptions (0.772) | job_descriptions (0.772) | job_descriptions (0.772) | ✅ |
| 全栈工程师需要什么能力 | job_descriptions | job_descriptions (0.669) | job_descriptions (0.669) | job_descriptions (0.600) | ✅ |
| 如何在简历中突出技术亮点 | job_descriptions | job_descriptions (0.718) | job_descriptions (0.718) | job_descriptions (0.718) | ✅ |
| 技术面试通常包含哪些环节 | job_descriptions | job_descriptions (0.780) | job_descriptions (0.780) | job_descriptions (0.780) | ✅ |