# 质量审计报告 —— AIResume v4.2.1 三维度审计（v5）

- 审计时间：2026-09-30
- 审计人：quality-engineer（只读审计，未修改任何业务代码）
- 审计范围：v4.1~v4.2.1 新增面为主——认证改造（auth / auth_deps / gate / login_throttle / username_service）、个人信息（me / schemas.auth / models.user / ProfileDialog）、新模块（interview_graph / question_bank / asr / audio_review / audio / worker.tasks）、前端（App / AdminPanel / AudioView / QuestionGenView / InterviewView / LoginPanel / LoginView / Icon / api.js / ProfileDialog）、脚本（seed_admin / seed_demo / eval_interview_graph），以及支撑文件（core/config / security / logging / main / deps / admin / interviews）
- 基线独立复核结果：
  - `ruff check .` → All checks passed ✔；`ruff format --check .` → 141 files already formatted ✔
  - `alembic check` → No new upgrade operations detected ✔（head 与声明一致）
  - `backend/.env` 存在且**未被 git 跟踪**（`git ls-files` 仅含 `.env.example` / `.env.docker.example`），`.gitignore` 覆盖 `.env` / `backend/.env` / `backend/.env.docker` ✔（按要求未读取 .env 内容）
  - pytest 363 全绿：按分派要求未重跑，以基线声明为准

---

## 一、安全（security-audit）

### 置顶提醒
未发现任何真实密钥/token 泄露（无云厂商 AccessKey、无 `sk-` 形态长随机串硬编码）。无需密钥轮换。

### 🔴 高危（0 处）

无。

### 🟡 中危（2 处）

**S-1 [P2] `backend/scripts/seed_admin.py:28-30,13` 内置管理员默认弱口令 `123456`，且该脚本是管理员唯一来源**
```python
DEFAULT_PASSWORD = (
    "123456"  # 演示/引导口令；登录 schema 拆分后 6 位可登录（注册仍 ≥8 位）
)
```
为什么是问题：`auto_promote_first_user` 默认关后，管理员唯一来源就是本脚本；默认口令 6 位纯数字且已提交 git，`schemas/auth.py` 还特意放行 6 位口令登录（`LoginCredentials.password` min_length=1，docstring 明说为它开口子）。生产环境执行 `python -m scripts.seed_admin` 后若忘记改密，admin 可被 `admin/123456` 直接登入（脚本 L89 仅打印文字提醒，不强制）。
修复：改为不传 `--password` 时拒绝执行（退出码 2 并提示），或自动生成密码学安全随机口令一次性打印；`app_env == "prod"` 时对弱口令直接拒绝创建。

**S-2 [P2] `backend/app/api/audio.py:52-70` 音频上传大小校验可被绕过 + 全量读入内存无二次限额**
```python
if file.size is not None and file.size > settings.audio_upload_max_size:
    raise HTTPException(413, ...)
...
data = await file.read()
```
为什么是问题：大小校验依赖 `file.size`——Starlette 多数场景会填，但该值不是协议保证（为 None 时整个限制被跳过）；随后 `await file.read()` 把请求体无上限读进内存，也没有读后 `len(data)` 复查。50MB 限制在 `file.size is None` 时形同虚设，登录用户可借并发上传放大内存占用。已有登录闸门 + 每日 20 次限额，风险被压低，故定中危。
修复：`data = await file.read()` 后补 `if len(data) > settings.audio_upload_max_size: raise HTTPException(413)`；更优做法是分块读盘、累计超限即中止。

### 🟢 低危 / 建议（6 处）

**S-3 [P3] `backend/app/api/auth.py:102` email 为 None 时 IntegrityError 分支误判冲突类型**
```python
if db.scalar(select(User).where(User.email == email)) is not None:
    raise HTTPException(409, "该邮箱已注册") from None
```
v4.2.1 起 email 可空，`email=None` 时查询变成 `email IS NULL`——库中只要存在任何 email 为空的账号，注册用户名冲突就会被误报成「该邮箱已注册」。修复：先判 `email is not None` 再查邮箱，None 直接跳到用户名分支。

**S-4 [P3] `backend/app/api/auth.py:74` 注册派生用户名时全表拉取**
`set(db.scalars(select(User.username)).all())` 把全表 username 载入内存。当前规模无碍，用户量大后注册路径线性变慢。建议：改为按候选名逐个存在性查询（派生候选数通常 ≤2）。

**S-5 [P3·疑似] `backend/app/services/interview_graph/graph.py:352-354` `_dsn()` 假设 DATABASE_URL 不带查询参数**
```python
base = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
return f"{base}?options=-csearch_path%3Dlanggraph%2Cpublic"
```
若 `.env` 的 `DATABASE_URL` 带 `?sslmode=...` 等参数会拼出双 `?` 的非法 DSN，PostgresSaver 直接连不上。确认方法：检查部署环境 `DATABASE_URL` 是否含 `?`。修复：用 `urllib.parse.urlparse/split` 区分，已有 query 用 `&` 追加。

**S-6 [P3] `backend/app/api/audio.py:69,263` 内容哈希寻址存储跨用户共享，删除有竞态**
`storage_path = f"{file_hash}{suffix}"`：两个用户上传同一音频共用同一磁盘文件；一方删除（L263 `unlink`）后，另一方若还在 `transcribing`，worker 读文件将失败（与 G-2 叠加成永久卡态）。建议：文件名加入记录 id（`{file_hash}-{audio.id}{suffix}`），或删除前查引用计数。

**S-7 [P3] `backend/scripts/seed_admin.py:63-68` `--password` 走命令行参数**
口令会进 shell history 与进程列表（`ps`）。建议：支持 `--password-stdin` 或交互式 `getpass`。

**S-8 [P3] `backend/app/api/admin.py:107` `/api/admin/users` 返回无 `username` 字段**
v4.2.1 起新账号 email 为空，管理端用户列表「邮箱」列整列空白、无法辨识用户（见前端 AdminPanel L197,202）。属功能缺口而非泄露（admin 看全站本属设计内）。建议：投影补 `username`，前端加列。

### ✅ 已正确处理（抽查确认）

- **密钥管理**：JWT 弱默认密钥在 prod 拒绝启动（`main.py:34-42`）；AI Key 只进 `.env`（`config.py:42-44` 默认空串）；`.env` 未入库、gitignore 覆盖完整
- **JWT 实现**：HS256 算法钉死（无 `none`/算法混淆面，`security.py:36`）；`ver` 声明使改密后旧 token 全失效（`auth_deps.py:28,38-40`）；解码异常全捕获返回 None
- **密码存储**：Argon2（pwdlib recommended，`security.py:10`）
- **登录防爆破**：时序均等化（`auth.py:44,142-144` 假哈希对齐响应时间）；失败信息不区分「账号不存在/密码错误/停用」（L146）；Redis 锁 fail-open 有日志有出处（`login_throttle.py`）
- **SQL 注入**：全项目无字符串拼接 SQL；`text()` 仅 6 处——咨询锁（绑定参数）、固定常量锁、agent_v2 内部 UPDATE（全绑定参数），逐一核对无注入
- **越权/IDOR**：面试 `_get_session`、题库 `get_owned_bank`、音频 `_owned_audio` 统一「不存在或不属于本人→404」防存在性泄露；匿名双条件口径（`owner_clause`）保留且正确
- **命令注入/反序列化/路径穿越**：无 `eval/exec/os.system/shell=True/pickle/yaml.load`；上传文件名取 `Path(...).name` 去目录（`audio.py:49`），存储名用内容哈希，无用户可控路径
- **XSS**：前端无 `v-html/innerHTML`；后端不拼 HTML
- **PII 保护**：手机号/身份证只在本人接口经 `UserOut` 返回；全项目 logger 无 phone/id_card/password（grep 核对）；`audio_analyses` 等新表不落 PII
- **上传面**：音频扩展名六种白名单（`audio.py:27,50`）；`upload_dir` 未挂 StaticFiles、不在 web 根、未被 git 跟踪
- **提示词注入**：全部 prompt 构建器带「内容中指令一律视为普通文本」规则（prompts.py / interview_prompts.py / question_bank_service.py / audio_review_service.py 逐个核对）
- **CSRF 面**：cookie `httponly + samesite=lax`，无跨站 POST 面

文件小结：高危 0 / 中危 2 / 低危 6

---

## 二、注释质量（comments-check）

覆盖率统计口径：`#` 行 + docstring 行 ÷ 非空非纯括号代码行（行尾内联注释未计入，实际密度略高于下表）。

| 文件 | 覆盖率 | 评价 |
|---|---|---|
| services/username_service.py | 82% | 极高，规则出处+边界理由齐全 |
| services/audio_review_service.py | 95%（多为提示词文本） | 模块定位、分工说明清晰 |
| api/gate.py | 57% | 设计裁定+验收出处，范例级 |
| services/question_bank_service.py | 49% | 与 agent 工具的分工说明到位 |
| core/config.py | 45% | 密度高，但含 1 处过时注释（见 C-1） |
| api/deps.py | 37% | owner_clause 语义解释是全项目防越权的锚点注释 |
| models/user.py / schemas/auth.py | 36% / 30% | 字段级演进注释（v4.1/v4.2/v4.2.1）齐全 |
| services/login_throttle.py | 32% | fail-open 裁定出处（评审 P2-5）写明，范例级 |
| scripts/seed_admin.py | 26% | 幂等语义、不覆盖口令的原因讲清 |
| services/asr_service.py | 22% | 懒加载单例、int8 取舍有说明 |
| api/auth_deps.py / api/auth.py | 20% / 18% | ver 机制、时序均等化的「为什么」到位 |
| services/interview_graph/graph.py | 14% | 模块头注释对齐 AGENTS.md 三坑，节点 docstring 完整 |
| scripts/seed_demo.py / worker/tasks.py / api/interviews.py | 12% / 9% / 9% | 头注释+关键行注释，达标 |
| api/auth_deps.py 之外的 api 薄文件：api/audio.py 4% / api/question_banks.py 4% / api/admin.py 5% | — | 端点有 docstring，但路由体内注释偏少（🟡 C-4） |

### 🔴 严重（2 处，均为注释与代码不匹配/过时）

**C-1 [P2] `backend/app/core/config.py:118-119` 登录锁定注释停留在 v4.1 之前的内存实现**
```python
# 登录防爆破：按 IP+邮箱计失败次数，超限锁定。内存实现（进程重启即解锁），
# 多 worker 部署需换 Redis 集中计数——当前单 worker 部署够用
login_max_failures: int = 10
```
代码实际：v4.1 已迁 Redis（`app/services/login_throttle.py`，多 worker 正确 + fail-open）。注释描述的实现已不存在，且这是安全机制——读配置的人会误以为「多 worker 部署时锁定失效」，可能据此做出错误的部署决策；也会误导后人「以为还没做 Redis」而重复实现。
修复：改为「登录防爆破参数；计数在 Redis（见 services/login_throttle.py，fail-open），窗口秒数 = login_lockout_minutes * 60」。

**C-2 [P2] `frontend/src/components/LoginPanel.vue:44` 注册校验注释描述已删除的「邮箱含 @」检查**
```js
// 注册：用户名过前端正则 + 邮箱含 @ + 手机号合法 + 密码 ≥8 且两次一致（与后端校验同口径）
const registerReady = () =>
  USERNAME_RE.test(...) && PHONE_RE.test(...) && password.value.length >= 8 && password.value === regConfirm.value
```
代码实际：v4.2.1 注册不再收集邮箱，`registerReady` 里没有任何 email 检查，表单也没有 email 输入框。注释列出的校验项不存在——小白会以为「邮箱也有校验」，或在恢复 email 字段时误信此注释「同口径已就绪」。
修复：删掉「邮箱含 @」一项；同时把 L2-3 头注释「登录用『用户名或邮箱』标识」更新为三态（用户名/邮箱/手机号）。

### 🟡 建议改进（4 处）

- **C-3 [P3] `frontend/src/components/AudioView.vue:152-155`** 注释说「先保存用户在角色审核页的编辑（若有），再跑面试审核」，但 if 块体为空（只有一行注释）——注释描述了一个从未发生的动作（与通用质量 G-1 同源，属该 bug 的注释面）。
- **C-4 [P3] `backend/app/api/audio.py`、`question_banks.py`、`admin.py`** 路由体内注释率 <5%：端点 docstring 有，但如 `upload_audio` 的哈希落盘、双 commit、审核作废语义等步骤缺行内注释，与 services 层的注释水准落差明显。
- **C-5 [P3] `backend/app/services/question_bank_service.py:60`** docstring 写「路由转 404/400」，实际 `question_banks.py:45-46` 把所有 ValueError 一律转 404（含「未解析成功」这种应为 400 的语义）。
- **C-6 [P3] `frontend/src/components/LoginPanel.vue:58`** 界面标签「用户名 / 手机号」与后端能力（还支持邮箱登录，存量账号依赖）不一致——能力说明类注释/文案落后于实现。

### ✅ 做得好的地方

- `api/gate.py`、`services/login_throttle.py`、`api/deps.py`、`services/username_service.py`：每条注释都回答「为什么这么做+裁定出处」（如「评审 P2-5 裁定」「方案 §3.4」「坑 #3」），是接手者最需要的信息
- `App.vue:190-192`：KeepAlive 移除的原因与代价写进注释，防止后人当 bug 改回去——这正是「看起来奇怪但有意为之」注释的教科书场景
- `api/auth.py:42-44`：时序均等化的目的（防用户名枚举）与实现代价（import 时算一次哈希）讲清
- `interview_graph/graph.py` 模块头：图结构 ASCII 图 + 三条实战坑逐条对齐 AGENTS.md
- `interviews.py:280-282`：JSONB null 与 SQL NULL 的坑位注释，解释了为什么二道过滤

**总体评分：7 / 10**——注释密度与「为什么」质量整体在水准之上（多数文件 20%+ 且有裁定出处），扣分点集中：2 处过时注释恰好都在安全/认证相关的语义上，误导性高于普通注释，属必须修的严重项。

---

## 三、通用质量

### 🔴 严重（会导致 bug / 功能失效）

**G-1 [P1] `frontend/src/components/AudioView.vue:147-164` + `backend/app/api/audio.py:208-252` 「面试审核」的可编辑送审文本从未发送到后端——功能链路断裂**
```js
// 先保存用户在角色审核页的编辑（若有），再跑面试审核
if (current.value.role_review && roleText.value && roleText.value !== roleMarked.value) {
  // 角色标注是 LLM 结构化结果，前端编辑只影响送审文本，不回写结构化字段
}
const body = await post(`/api/audio/analyses/${current.value.id}/interview-review`)   // 无 body！
```
后端 `run_interview_review` 不接收任何文本参数，固定用 `render_role_marked(audio.role_review_json)`（无标注时用原文）送审。而 UI 明确承诺：「送审对话（预填角色标注结果，**可编辑**）」（L261-262）、动态提示「将使用上方**编辑后的文本**送审」（L270）。用户在文本框里的任何修改对审核结果零影响——功能与界面承诺不符，用户完全无法察觉（不报错、有结果）。
修复：前端 `post(url, { text: roleText.value })`；后端 `run_interview_review` 增加可选 body 参数，传入时优先用请求文本、落库前同步到记录（或明确产品决策砍掉编辑框并同步改 UI 文案）。

### 🟡 建议（可维护性 / 健壮性）

**G-2 [P2] `backend/app/worker/tasks.py:142` 转写任务读文件在 try 块之外，文件缺失会让记录永久卡在 transcribing**
```python
data = Path(audio.storage_path).read_bytes()  # 在 try 外
try:
    result = transcribe(data, audio.filename)
except Exception as exc:
    ...  # 置 failed
```
`read_bytes()` 抛 FileNotFoundError（文件被并发删除/磁盘清理/S-6 竞态）时异常直接炸出任务，`audio.status` 永远停在 `transcribing`，前端 3 秒轮询永不停止。修复：把读文件挪进 try，except 中同口径置 `failed` + 写 `error`。
（对照：`parse_resume` L36 同样在 try 外，但那是存量模块且失败语义不同，本轮按范围仅报新模块。）

**G-3 [P2] `frontend/src/components/AdminPanel.vue:118-122` `load()` 被 onMounted 注册两次，每次挂载所有看板接口请求两遍**
```js
onMounted(load)
onMounted(() => {
  load()
  if (isAdmin.value) loadV2()
})
```
Vue 允许多个 onMounted 回调、依次全执行：stats/users/usage（或 me/stats+me/usage）每次进看板都请求两遍，双倍后端压力且数据闪变。修复：删除 L118 的 `onMounted(load)`，把 load 收进第二个回调（现在就是这么写的）。

**G-4 [P3] `frontend/src/App.vue:120-121` 嵌套重复的 `.shell` 容器**
```html
<div v-else class="shell">
<div class="shell">
```
外层（v4.2.1 加 v-else 时包上）与内层同 class：两层 `height:100vh; display:flex` 容器互相嵌套，属重构残留。当前视觉未崩（ProfileDialog 是 fixed 定位），但滚动容器语义翻倍、后续布局易踩坑。修复：去掉一层，ProfileDialog 挪进保留下来的 shell 内。

**G-5 [P3] `frontend/src/components/AudioView.vue:60` 两分支相同的三元表达式**
```js
activeTab.value = current.value?.transcript ? 'transcribe' : 'transcribe'
```
疑似本意是「有文本进转写页、无文本进角色页」或直接写死。死代码，掩盖意图。修复：写成 `activeTab.value = 'transcribe'`，或补上真实分支。

**G-6 [P3] `backend/app/api/interviews.py:10-17` import 区夹杂可执行语句**
```python
import json
import time

...
from app.core.logging import get_logger

logger = get_logger(__name__)  # 语句夹在 import 中间
from datetime import datetime, timezone
```
ruff 未开 E402 所以没拦，但阅读时 import 依赖关系被打断。修复：`logger = ...` 挪到全部 import 之后。

**G-7 [P3] `frontend/src/components/ProfileDialog.vue:34-38` UI 上无法清空身份证**
后端支持「空串=清空」（me.py L143），但前端把空串转成 `null` 发送（`id_card: form.value.id_card || null`），后端 None=不改——「填错了想清掉」在界面上不可达。修复：前端对 id_card 原样发送空串（或加「清除」按钮）。

**G-8 [P3·疑似] `backend/app/services/login_throttle.py:40-45` INCR 与 EXPIRE 理论缝隙**
pipeline 中 INCR 成功、EXPIRE 未落（进程在两命令间崩溃）时，该键无 TTL 永久锁定对应 `ip:identifier`。pipeline 通常整体送达，概率极低；确认方法：Redis 里 `TTL login:fail:*` 出现 -1 的键。修复：EXPIRE 改在 INCR 返回 1 时设置（首败设窗），或加 `pipe.execute()` 后自愈检查。

---

## 汇总

### 风险总览
- **安全**：高 / 中 / 低 = **0 / 2 / 6**
- **注释**：评分 **7/10**；🔴 严重 **2** 处（过时注释）、🟡 建议 4 处
- **通用质量**：严重（P1）/ 建议（P2 / P3）= **1 / 2 / 5**（P3 含疑似 1 处）
- 基线复核：ruff 全绿 ✔ / alembic 无差异 ✔ / .env 未入库 ✔

### P0 / P1 清单
- P0：无
- P1：**G-1** AudioView 面试审核「可编辑送审文本」从未发送后端（前端 L147-164 空 if + 无 body 的 post；后端 L208-252 固定读存储标注）——用户可感知功能失效

### P2 / P3 数量统计
- P2 共 6 处：安全 S-1（seed_admin 弱默认口令）、S-2（音频上传大小校验绕过）；注释 C-1（config.py 过时注释）、C-2（LoginPanel 过时注释）；通用 G-2（transcribe_audio 读文件卡态）、G-3（AdminPanel 双载入）
- P3 共 11 处：安全 S-3~S-8（6 处，含疑似 1）；注释 C-3~C-6（4 处）；通用 G-4~G-8（5 处，含疑似 1；其中 C-3 与 G-1 同源、G-8 为疑似，去重后独立 P3 计 9~11 视口径）

### 最优先处理的 3 件事（跨维度排序）
1. **修 G-1 送审文本链路**（前端带 body 发送 + 后端接收覆盖，或砍掉编辑框改文案）——唯一 P1，功能失效且用户无感
2. **堵 S-1 / G-2 两个真实风险口**：seed_admin 默认口令改为强制传入；transcribe_audio 读文件挪进 try 消灭「永久转写中」
3. **清 C-1 / C-2 两处过时注释**（config.py 登录锁定实现描述、LoginPanel 注册校验描述）——门禁 FAIL 的直接原因，各一行改动，顺手把 LoginPanel 的登录标签补上「邮箱」

### 门禁结论

按门禁标准（安全🔴高危 0 且 注释🔴严重 0 = PASS）：安全高危 0 处，但注释🔴严重 2 处（C-1、C-2）→ **FAIL**。中危/低危/建议不阻断，仅供排期。

一句话总评：这套代码的安全基本功（注入防护、越权口径、密钥管理、PII 隔离）在同规模项目里属上乘，v4.1/v4.2 新增面的注释也普遍解释了「为什么」；真正的短板是「改了实现忘了改注释」的两处过时说明和一条前端功能链路断裂——都是小时级可清完的债，不伤架构。

VERDICT: FAIL
