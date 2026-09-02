# RAG 检索评测报告

- 时间：2026-09-03 01:33
- embedding：nomic-embed-text（dim 768）
- 切块：约 600 字/块，重叠 60
- 黄金问答集：qa.json（49 题）

## 汇总

| 指标 | 结果 |
| --- | --- |
| hit@1（首块即来自预期文档） | 36/49 (73.5%) |
| hit@5（前5块含预期文档） | 46/49 (93.9%) |
| 平均 top1 相似度 | 0.7320 |

## 逐题明细

| 题目 | 预期文档 | 命中 | top1 |
| --- | --- | --- | --- |
| HashMap 的底层实现原理是什么 | java_interview | ✅ | java_interview (0.795) |
| synchronized 和 ReentrantLock 有什么区别 | java_interview | ✅ | java_interview (0.827) |
| volatile 关键字的作用是什么，能保证原子性吗 | java_interview | ✅ | java_interview (0.659) |
| ConcurrentHashMap 是怎么保证线程安全的 | java_interview | ✅ | java_interview (0.717) |
| JVM 的内存区域怎么划分 | java_interview | ✅ | java_interview (0.788) |
| 垃圾回收有哪些算法，新生代和老年代怎么回收 | java_interview | ✅ | job_descriptions (0.692) |
| 类的加载过程包含哪些阶段 | java_interview | ✅ | job_descriptions (0.662) |
| Spring Bean 的生命周期是怎样的 | java_interview | ✅ | java_interview (0.775) |
| Spring 事务传播行为有哪些，REQUIRED 和 REQUIRES_NEW 区别 | java_interview | ✅ | java_interview (0.703) |
| Spring 怎么解决循环依赖 | java_interview | ✅ | java_interview (0.698) |
| Spring AOP 的实现原理是什么 | java_interview | ✅ | java_interview (0.768) |
| 线程池的核心参数和执行流程 | java_interview | ✅ | system_design (0.697) |
| 什么是死锁，怎么避免死锁 | java_interview | ✅ | job_descriptions (0.679) |
| ThreadLocal 有什么使用隐患 | java_interview | ✅ | java_interview (0.822) |
| String 为什么是不可变的 | java_interview | ✅ | java_interview (0.715) |
| MySQL 为什么要用 B+ 树做索引 | database_interview,java_interview | ✅ | java_interview (0.781) |
| 聚簇索引和非聚簇索引的区别 | database_interview,java_interview | ❌ | algorithm_interview (0.704) |
| MySQL 的四种隔离级别是什么，InnoDB 默认哪个 | java_interview,database_interview | ✅ | java_interview (0.875) |
| 什么是 MVCC，它是怎么实现的 | java_interview,database_interview | ✅ | java_interview (0.788) |
| 什么是 SQL 注入，怎么防范 | database_interview | ✅ | database_interview (0.793) |
| 数据库事务的 ACID 特性是什么 | database_interview | ✅ | database_interview (0.714) |
| 为什么要分库分表，有哪些策略 | database_interview | ✅ | database_interview (0.698) |
| 怎么优化大分页查询 | database_interview | ✅ | database_interview (0.707) |
| 乐观锁和悲观锁有什么区别 | database_interview | ❌ | network_interview (0.650) |
| 怎么用 EXPLAIN 分析慢查询 | database_interview | ✅ | database_interview (0.672) |
| 什么是缓存穿透、缓存击穿、缓存雪崩 | system_design,java_interview | ✅ | job_descriptions (0.670) |
| 如何设计一个短链接系统 | system_design | ✅ | system_design (0.685) |
| 如何设计一个秒杀系统 | java_interview,system_design | ✅ | system_design (0.684) |
| 如何设计一个实时排行榜 | system_design | ✅ | system_design (0.681) |
| 如何设计分布式 ID 生成器 | system_design | ✅ | system_design (0.772) |
| 分布式系统怎么保证最终一致性 | java_interview,system_design | ✅ | database_interview (0.769) |
| CAP 定理和 BASE 理论是什么 | java_interview | ✅ | java_interview (0.693) |
| 为什么用消息队列 | java_interview | ✅ | job_descriptions (0.705) |
| TCP 三次握手为什么是三次 | network_interview | ✅ | network_interview (0.870) |
| TCP 四次挥手为什么要 TIME_WAIT | network_interview | ✅ | network_interview (0.849) |
| TCP 和 UDP 有什么区别 | network_interview,java_interview | ✅ | network_interview (0.705) |
| HTTP 和 HTTPS 的区别是什么 | network_interview,java_interview | ✅ | java_interview (0.721) |
| 进程和线程的区别 | network_interview | ✅ | algorithm_interview (0.673) |
| 阻塞 IO 和非阻塞 IO 的区别 | network_interview | ✅ | network_interview (0.748) |
| cookie、session、token 的区别 | network_interview | ✅ | network_interview (0.790) |
| 数组和链表的区别与适用场景 | algorithm_interview | ✅ | algorithm_interview (0.688) |
| 哈希冲突怎么处理 | algorithm_interview | ✅ | job_descriptions (0.705) |
| 动态规划适合解决什么问题 | algorithm_interview | ❌ | java_interview (0.672) |
| 怎么实现一个 LRU 缓存 | algorithm_interview | ✅ | network_interview (0.765) |
| 什么是微服务架构 | java_interview,system_design,job_descriptions | ✅ | job_descriptions (0.705) |
| Java 后端开发岗位的任职要求是什么 | job_descriptions | ✅ | job_descriptions (0.772) |
| 全栈工程师需要什么能力 | job_descriptions | ✅ | job_descriptions (0.669) |
| 如何在简历中突出技术亮点 | job_descriptions | ✅ | job_descriptions (0.718) |
| 技术面试通常包含哪些环节 | job_descriptions | ✅ | job_descriptions (0.780) |