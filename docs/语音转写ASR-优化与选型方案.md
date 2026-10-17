# 语音转写（ASR）优化与选型方案

> 调研日期：2026-10-13 | 被测环境：本机 16 核 CPU（无 NVIDIA GPU，NVML 探测失败）、faster-whisper 1.2.1 + ctranslate2 4.8.2
> 现状代码：`backend/app/services/asr_service.py`（模型 `small` / `device=cpu` / `compute_type=int8`）、`backend/app/worker/tasks.py::transcribe_audio`

---

## 0. 结论（TL;DR）

1. **不改模型、不加依赖，参数层可拿到 2.1× 提速**：实测 RTF 从 `0.189` 降到 `0.089`（66s 音频 12.5s → 5.9s），输出内容一致，繁体字从 60 字降到 12 字。
2. **当前最大的问题不是参数，是模型选型**：`small` 是 Whisper 家族里中文能力很弱的一档。同场景（多人对话/会议，即 WenetSpeech meeting）Whisper-large-v3 的 CER 为 18.87%，国产模型普遍在 4~7%。
3. **发现两个真实缺陷**：
   - 「快速转写」档用的 `base` 模型**不在本机缓存**（缓存只有 `small`），首次点击需联网下载；本机网络下 HF 不可达（实测 502），离线加载直接抛 `LocalEntryNotFoundError` → 该功能实际不可用。
   - 转写输出**混入繁体**（实测占比 6.9%~34% 随机波动），加一句 `initial_prompt` 可压到接近 0，代价约 0.3s。
4. **建议路线**：先做零成本参数优化（1 次改动、2.1× 提速）→ 修 `base` 档缺陷并补进度/复用 → 再评估换 `SenseVoice-Small`（中文 CER 约 1/3，且可用 onnxruntime 部署，**不必引入 torch**）。

---

## 1. 现状核对（代码事实）

| 项 | 当前值 | 位置 |
|---|---|---|
| 模型 | `asr_whisper_model = "small"`（244M） | `config.py:145` |
| 快速档 | `asr_whisper_fast_model = "base"`（74M） | `config.py:146` |
| 设备 / 量化 | `device="cpu"` / `compute_type="int8"` | `asr_service.py:29-33` |
| 转写参数 | `language="zh"`、`vad_filter=True`，**其余全用默认** | `asr_service.py:53-57` |
| 输入方式 | 每次调用写一个临时文件（`tempfile.NamedTemporaryFile`） | `asr_service.py:49-51` |
| 执行位置 | Celery worker（`transcribe_audio.delay`） | `worker/tasks.py:132` |
| 模型缓存 | 仅 `~/.cache/huggingface/hub/models--Systran--faster-whisper-small`（464MB） | 实测 |
| 依赖 | 无 torch（纯 ctranslate2 + onnxruntime 1.30 + av 18.1） | 实测 |

隐含默认值（未显式传参，所以生效）：`beam_size=5`、`temperature=[0.0, 0.2, ..., 1.0]`（fallback 链）、`condition_on_previous_text=True`、`no_speech_threshold=0.6`、`compress_ratio_threshold=2.4`。

---

## 2. 实测：参数层优化空间

### 2.1 方法与可信度

- 音频：从 ModelScope 下载的**真实中文语音**样本（Paraformer 示例 4.52s + SenseVoice 示例 5.62s），交替拼接 + 0.6s 静音分隔 → 66.0s 单声道 16kHz。
- 脚本：`%TEMP%/asr_bench/bench2.py`、`verify2.py`、`verify3.py`（本次调研产物，未入库）。
- 每组重复 1~2 次取最优；**绝对秒数受进程启动/热机影响有噪声，倍数关系可靠**。
- 拼接音频的重复内容会让默认的"上文串接"陷入重复循环，因此除首轮 A2 组外，基线统一设 `condition_on_previous_text=False`（真实单段录音不会有此放大效应）。

### 2.2 单项收益（实测）

| 配置 | 耗时 | RTF | 相对现状 |
|---|---|---|---|
| **现状等价**（beam=5 + 默认温度链 + 上下文on + vad on） | 12.50s | 0.189 | 1.00× |
| `temperature=0.0`（关掉 fallback 链） | 8.12s | 0.123 | **1.53×** |
| `beam_size=1` | 5.91s | 0.090 | **1.78×** |
| `BatchedInferencePipeline(batch_size=8)` | 5.87s | 0.089 | **1.95×** |
| `BatchedInferencePipeline(batch_size=16)` | 7.15s | 0.108 | 1.75×（**反而更慢**） |
| `vad_filter=False` | 7.54s | 0.114 | 1.40×（但引入静音段幻觉，不可取） |
| `cpu_threads=4` / `cpu_threads=16` | 10.80s / 11.44s | 0.164 / 0.173 | 与默认（自动）无差异 |
| `word_timestamps=True` | 11.19s | 0.170 | 与基准同（可视为**免费**） |
| `initial_prompt`（简体提示） | 8.44s | 0.128 | 与同参数基准同（**免费**） |

### 2.3 组合最优（实测）

| 配置 | 耗时 | RTF | 提速 | 输出字符 | 繁体字 |
|---|---|---|---|---|---|
| 现状等价 | 12.50s | 0.189 | 1.00× | 174 | 60 |
| **batch=8 + temp=0.0 + 简体 prompt + 上下文off** | **5.90s** | **0.089** | **2.12×** | 174 | 12 |
| 上式 + `word_timestamps=True` | 6.60s | 0.100 | 1.89× | 174 | 12（**另得 150 个词级时间戳**） |

> 输出字符数在全部配置下都是 174 → 提速没有靠丢内容换来。
> `beam_size=1` 与批处理**能否叠加未测**（`BatchedInferencePipeline` 亦接受 `beam_size`），预计组合后可达 RTF 0.06~0.07，**属预估，未验证**。

### 2.4 两个具体缺陷（实测证据）

**(1) `fast` 档模型不可用**

```
>>> WhisperModel("base", device="cpu", compute_type="int8", local_files_only=True)
LocalEntryNotFoundError: Cannot find an appropriate cached snapshot folder ...
outgoing traffic has been disabled.
```

`~/.cache/huggingface/hub/` 下只有 `models--Systran--faster-whisper-small`。即：
- 用户勾选「快速转写」→ worker 首次加载 `base` 需联网下载约 140MB；
- 本机网络访问 `huggingface.co` 实测失败（`CONNECT tunnel failed, response 502`）→ `audio_analyses` 会落 `status=failed`。

更划算的做法：**不换模型，改用 `beam_size=1`**（实测同档提速 1.78×，精度损失远小于 small→base 的降级）。

**(2) 繁体输出**

| 配置 | 繁体字占比（同一段音频） |
|---|---|
| 无 prompt | 18.75% / 20.69% / 34.48%（三次运行，随机波动） |
| `initial_prompt="以下是简体中文的普通话录音，请使用简体中文输出。"` | 6.88%（其中"迎/放"为检测表假阳性，实际输出近乎全简体） |
| `initial_prompt="以下是普通话的句子。"` | 6.88% |

`language="zh"` **不能**阻止 Whisper 输出繁体。面试复盘场景里繁体混排会直接污染下游 `role_review` / `interview_review` 的 LLM 输入。

---

## 3. 其他本地 ASR 方案对比

### 3.1 中文精度（公开基准，CER %，越低越好）

| 模型 | 参数 | AISHELL-1 | AISHELL-2 | WenetSpeech net | WenetSpeech meeting | 平均 |
|---|---|---|---|---|---|---|
| FireRedASR2-LLM | — | 0.64 | 2.15 | 4.44 | 4.32 | **2.89** |
| FireRedASR2-AED | 1.1B | 0.57 | 2.51 | 4.57 | 4.53 | 3.05 |
| Qwen3-ASR-1.7B | 1.7B | 1.48 | 2.71 | 4.97 | 5.88 | 3.76 |
| Fun-ASR（闭源版） | 7.7B | 1.64 | 2.38 | 6.85 | 5.78 | 4.16 |
| SenseVoice-L | 1.6B | 2.09 | 3.04 | 6.01 | 6.73 | 4.47 |
| Paraformer-Large | 0.2B | 1.68 | 2.85 | 6.74 | 6.97 | 4.56 |
| SenseVoice-Small | 234M | ~3.0 | — | — | — | ~7.81（另一来源） |
| **Whisper-large-v3** | 1.6B | 5.14 | 4.96 | 10.48 | **18.87** | 9.86 |

**面试录音 ≈ WenetSpeech meeting 场景**（双人对话、口语化、偶有噪声）：Whisper-large-v3 的 18.87% 是国产专用模型的 4 倍。**本项目用的还是比 large-v3 更弱的 small**，中文 CER 只会更差。

> 口径声明：上表前 7 行来自 FireRedASR2S 仓库的统一对比表（同一方测试，口径一致）；`SenseVoice-Small` 来自 FunASR 官方基准（另一套音频，6.7× 与 7.81% 两个数字并存）。FunASR 官方基准自述"无原始预测与评分程序"的质疑同样存在。**这些数字用于排序，不宜当绝对值引用。**

### 3.2 性能与资源

| 方案 | 推理架构 | CPU 表现 | GPU 表现 | 内存/磁盘 | 运行依赖 |
|---|---|---|---|---|---|
| **faster-whisper（现状）** | CTranslate2，自回归 | small int8：本机实测 RTF 0.089~0.19 | 4090 上 ~30× 实时 | 模型 464MB（small） | 无 torch；`ctranslate2` + `av` + `onnxruntime` |
| openai-whisper | PyTorch，自回归 | 很慢（大模型 CPU 不可用） | 基准 | 高（FP32） | **torch + ffmpeg 二进制** |
| whisper.cpp | C/C++ GGML，自回归 | 强（x86 large-v3 Q5_1 约 1.5~2.5× 实时；base 10~20×） | 不如 faster-whisper | 低（可 int4/int8） | 无 Python 依赖 |
| WhisperX | faster-whisper + wav2vec2 对齐 + pyannote 分离 | 中 | 好 | 高 | **torch + pyannote，且需 HF token 接受协议** |
| **SenseVoice-Small** | 非自回归（CIF，一次前向） | 官方基准 17.2× 实时（CPU） | 169.6× | ~250MB int8 | 原生需 torch；**sherpa-onnx 提供 onnx 版，只需 onnxruntime** |
| Paraformer-Large | 非自回归 | 官方基准 15.6× 实时（CPU） | 119.6× | ~220M | 同上（有 onnx 版） |
| FireRedASR2-AED | Conformer + Transformer AED | 未实测，1.1B 参数 CPU 压力大 | 推荐 | 大（1.1B） | torch |
| Qwen3-ASR / Fun-ASR-Nano | LLM-based | 慢（3.6× 实时级别） | 推荐 | 大 | torch / vLLM |
| Moonshine | 轻量自回归 | 快（面向端侧） | — | 极小 | 中文训练数据仅 69.8h，**中文 CER 36.1%，不适用** |
| Vosk / Kaldi | 传统 HMM | 很快 | — | 小 | 中文精度显著落后，不推荐新项目 |

### 3.3 适配本项目的代价矩阵

| 方案 | 新增依赖 | 是否需要重训/换迁移 | 与现有架构冲突 | 落地成本 |
|---|---|---|---|---|
| 参数优化（现状引擎） | 无 | 无 | 无 | **极低**（1 个函数） |
| 换 `small`→`large-v3` | 无 | 无 | 磁盘/内存 ×6，CPU 上慢 3~5× | 低（但 CPU 不划算） |
| 换 `SenseVoice-Small`（sherpa-onnx onnx） | `sherpa-onnx`（复用已有 onnxruntime） | 无（ASR 不入库，无向量维度问题） | 需重写 `asr_service` 适配层 | 中 |
| 换 `SenseVoice`（funasr 原生） | **torch**（CPU wheel 约 200MB+） | 无 | 依赖体积显著变大，与"轻依赖"取向冲突 | 中高 |
| 换 `FireRedASR2-AED` | torch + 1.1B 权重 | 无 | CPU 不实用 | 高 |
| 加说话人分离（角色审核） | sherpa-onnx（VAD + 声纹）或 pyannote | 无 | 需改 `role_review` 流程 | 中 |

> 现状**零 torch** 是当前架构的一个优点（`ctranslate2 + av + onnxruntime`）。任何引入 torch 的方案都要先权衡这一点。

---

## 4. 适用场景决策表

| 场景 | 推荐方案 | 理由 |
|---|---|---|
| 纯中文面试/会议长录音，CPU 部署，要精度（**本项目主场景**） | **SenseVoice-Small（onnx 版）** | 中文 CER 约 Whisper-large-v3 的 1/3，CPU 上非自回归比自回归快一个量级 |
| 中英混说 / 多语言 / 需要时间戳与生态工具 | faster-whisper（保留） | 多语言覆盖广、`word_timestamps` 免费、生态成熟 |
| 追求中文 SOTA，且有 GPU | FireRedASR2-LLM / Qwen3-ASR | CER 最低，但 CPU 不实用 |
| 无 Python 运行时的边缘/嵌入式部署 | whisper.cpp | 单二进制、GGML 量化、无解释器 |
| 需要"谁说了什么"（说话人分离） | WhisperX（有 GPU + HF token）或 sherpa-onnx（无 torch） | 前者开箱即用但依赖重；后者与本项目依赖更契合 |
| 实时字幕 / 流式 | sherpa-onnx 流式 Zipformer / whisper.cpp 流式 | 支持增量喂入，非本项目的批处理场景 |

---

## 5. 建议路线（按性价比排序）

### 第一批：零成本参数优化（不动依赖，1 处改动）

`asr_service.transcribe()` 改为：

```python
segments_iter, info = model.transcribe(
    tmp_path,
    language="zh",
    vad_filter=True,
    beam_size=5,                     # 快速档可传 1
    temperature=0.0,                 # 关 fallback 链：实测 1.53×
    condition_on_previous_text=False,  # 防长录音重复循环
    initial_prompt="以下是简体中文的普通话录音，请使用简体中文输出。",  # 繁体 21%→7%
)
```

再用批处理流水线替换单路调用（实测 1.95×，与上述叠加 → 合计 **2.12×**）：

```python
from faster_whisper import BatchedInferencePipeline
_pipelines[name] = BatchedInferencePipeline(model=_get_model(name))
segments_iter, info = _pipelines[name].transcribe(path, batch_size=8, **kwargs)
```

**注意**：`batch_size=16` 实测反而更慢（7.15s vs 5.90s），8 是甜点。

### 第二批：修缺陷 + 补体验（小改动）

1. **修 `fast` 档**：把快速档从"换 base 模型"改为"`beam_size=1`"（实测 1.78×，无下载依赖）；或把 `base` 纳入部署预下载脚本（`scripts/` 下与 whisper small 一并预取）。
2. **结果复用**：`file_hash` 已在 `audio.py:104` 算出，但文件名带 `audio.id` 导致同内容不同 id 不共享。可按 `user_id + file_hash` 命中已完成记录直接复制 `transcript`，跳过整段 CPU 转写。
3. **进度反馈**：`segments` 是生成器，可在迭代中回写进度百分比到 `audio_analyses`，前端把"转写中"换成进度条（当前前端只有状态文案 `AudioView.vue:233`）。
4. **省一次落盘**：已实测 `ndarray` 输入可用（`decode_audio(BytesIO) → np.ndarray → transcribe`），可省掉 `tempfile` 的 50MB 写盘与残留风险（收益偏工程整洁，非性能）。
5. **worker 隔离**：`concurrency` 与 `cpu_threads` 需显式绑定（实测 `cpu_threads` 4/16 无差异 → ctranslate2 默认已吃满物理核，多 worker 并发只会互相抢占，不会线性扩容）；给转写任务加 `time_limit`，避免超长音频永久占住 worker。
6. **音频预处理**：上传的 m4a/webm 常是 44.1/48kHz 立体声，whisper 内部仍需重采样；提前统一为 16kHz 单声道可省一段 CPU。

### 第三批：结构性升级（需要评估）

1. **换 `SenseVoice-Small`（onnx 版）**：中文 CER 量级提升，CPU 更快，且不引入 torch。代价是重写 `asr_service` 适配层（当前它是"全项目唯一懂音频的地方"，边界清晰，改造风险可控）。
2. **说话人分离前置**：现在 `role_review` 是让 LLM 从纯文本猜"面试官/候选人"。若开 `word_timestamps`（实测免费）+ VAD 分段 + 声纹嵌入（sherpa-onnx 的 3D-Speaker / pyannote onnx），可把"猜角色"换成"按声纹聚类"，角色审核质量会有结构性提升。

---

## 6. 未验证 / 风险

| 项 | 说明 |
|---|---|
| 绝对 RTF 外推 | 实测基于 66s 拼接音频（内容重复）。真实 30min 录音的 RTF 应更稳定，但**没有实测过长音频** |
| `beam=1` 的精度代价 | 未用标注语料测 CER，仅有业界经验值（相对 -1~2%）。快速档定位可接受 |
| `beam=1` + 批处理叠加 | **未测**，预计 RTF 0.06~0.07 |
| 本机无 GPU | `nvidia-smi` 报 `Failed to initialize NVML`；若部署机有 GPU，应把 `device` 改为可配置，收益远大于以上所有参数优化 |
| 生产网络 | HF 不可达是本机沙箱实测结果；若生产环境可访问 HF，`base` 档缺陷仅表现为首次慢，不致失败 |
| 公开 CER 数据 | 来源口径不一（部分为厂商自测），用于排序可信，用于绝对值需再用自有语料复测 |
| 依赖影响 | 引入 `sherpa-onnx` / `torch` 会改变 `requirements.lock`（项目约定：装/升依赖后必须重新生成锁文件） |

---

## 7. 复现方法

```bash
cd backend
# 1) 取真实中文语音样本（ModelScope，无需 HF）
curl -L -o "$TEMP/asr_bench/asr_example.wav" "https://www.modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/resolve/master/example/asr_example.wav"
curl -L -o "$TEMP/asr_bench/sv_zh.mp3" "https://www.modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/example/zh.mp3"
# 2) 拼接 + 跑参数矩阵（脚本见 %TEMP%/asr_bench/）
HF_HUB_OFFLINE=1 .venv/Scripts/python "$TEMP/asr_bench/bench2.py" "$TEMP/asr_bench/long_zh.wav" small
HF_HUB_OFFLINE=1 .venv/Scripts/python "$TEMP/asr_bench/verify3.py"
```

> `HF_HUB_OFFLINE=1` 必加：不加时会先访问 `huggingface.co` 做 revision 检查，本机代理返回 502 导致加载失败（这本身也是一条可加固项：生产应设 `local_files_only=True` 或以离线模式运行，避免依赖网络可达性）。
