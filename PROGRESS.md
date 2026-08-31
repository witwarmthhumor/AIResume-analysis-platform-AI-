# 进度日志

> **本文件是干嘛的**：项目进度日志——当前进行、已完成、踩过的坑、下一步。AI 每次新会话先读它接续上下文；每次会话结束前更新本文件。
> 最后更新：2026-09-01（阶段2 实现完成会话）。

## 当前进行
- 阶段2（AI 简历分析）实现完成，**待用户在浏览器验收 → 打 tag v0.3（仅本地，不推送）**
- 后端与前端已真机联通：DeepSeek 真实调用出报告、缓存去重、usage 记账、失败留痕均实测通过
- 当前模型已从通义切到 **DeepSeek**（backend/.env 三行）；分支 stage-2-ai-analysis

## 已完成
- 项目规划定稿 / 记忆文件落成 / 阶段0 封版 v0.1 / 阶段1 封版 v0.2
- 阶段2 后端（上次会话）：分析接口 + AIReport 六块校验 + 失败重试(最多2次) + 每日限流 + usage 记账 + 失败留痕；18 个 pytest 全过，ruff 全绿
- 阶段2 前端（本次）：AnalysisReport.vue 报告页（六块内容 + 模型/token/耗时元信息 + cached 徽标 + 429/502/网络错误友好提示）；App.vue 集成；npm build 通过
- 测试简历集：test-resumes/ 5 份 PDF（应届生/社招3-5年/转行/英文/双栏），生成脚本 scripts/generate_test_resumes.py，pypdf 实测提取无乱码
- 真机 E2E（DeepSeek）：上传应届简历 → 分析返回 6 块完整报告（tokens 478+544，耗时 6.2s）→ 二次分析 cached=true 不重复计费 → analyses/usage_logs 记账正确
- 失败路径 E2E（通义 qwen-plus 欠费时）：真实 4xx → 502 友好话术（新 ai_client 分类生效）→ 失败留痕 valid_json=false + usage 记 0 token
- 换模型验证：通义(qwen-plus) 失败路径 + DeepSeek(deepseek-chat) 成功路径 均通过，封装层适配 OpenAI 兼容协议无误

## 已知问题 / 踩过的坑
- **通义账户欠费**（Arrearage）：DashScope 拒绝计费请求（400）。已临时切 DeepSeek；如需回通义需先充值
- **ezlinkapi.top 不可达**：80/443 握手均被重置（DNS 可解析），从本机网络无法连通该境外代理服务
- **fpdf2 生成中文 PDF 两个坑**：① simhei(GB2312) 缺 •(U+2022) 字形，bullet 改用 `- `；② multi_cell 结束后 x 停在右边距需手动归位，否则下个块宽度为 0 报错
- **Windows curl 传中文文件名乱码**：curl 用系统 GBK 编码发 multipart filename，浏览器上传是 UTF-8 无此问题（属测试工具现象，非后端 bug）
- Vite 代理 404 坑（已修）/ 残留进程占端口 / Docker 手动启动 / Vite 只绑 IPv6（沿用历史记录）

## 下一步
- 用户浏览器验收（报告页六块 + token 可见 + 错误提示）→ 通过后打 tag v0.3
- 验收时可用 test-resumes/ 5 份 PDF 逐份上传分析，抽查质量（改提示词需递增 PROMPT_VERSION 并重跑对比）
- 阶段3 开工前置：无（SSE 流式、interview 表结构已在阶段0 定稿）
