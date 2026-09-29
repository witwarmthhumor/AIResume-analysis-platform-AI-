"""语音转写服务（v4.2 B5）：faster-whisper 本地模型，全项目唯一懂音频的地方。

- 模型懒加载单例：首次调用时加载（模型文件已在 B0 预下载到 HF 缓存）；
- faster-whisper 经 PyAV 解码音频（pip 依赖自带二进制），无需系统 ffmpeg；
- 只在 Celery worker 进程里跑（CPU 转写分钟级，绝不在请求线程同步跑）。
"""

import logging
import threading

from app.core.config import settings

logger = logging.getLogger(__name__)

_MODEL_LOCK = threading.Lock()
_model = None  # WhisperModel 单例（worker 进程内共享）


def _get_model():
    """懒加载 faster-whisper 模型（线程安全的单例）。"""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        with _MODEL_LOCK:
            if _model is None:
                logger.info("加载 whisper 模型：%s（首次可能需要下载数百 MB）", settings.asr_whisper_model)
                _model = WhisperModel(
                    settings.asr_whisper_model,
                    device="cpu",
                    compute_type="int8",  # CPU 上 int8 比 float16 快且省内存
                )
    return _model


def transcribe(data: bytes, filename: str) -> dict:
    """转写音频字节流：返回 {text, segments, duration_seconds}。

    segments 为 [{start, end, text}]（供角色审核对齐说话人）；任何异常向上抛，
    由 Celery 任务记入 audio_analyses.error（状态 failed 的语义复用 error 字段）。
    """
    import tempfile
    from pathlib import Path

    model = _get_model()
    # faster-whisper 接收文件路径：落临时文件（按扩展名保留后缀供 PyAV 识别容器格式）
    suffix = Path(filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        segments_iter, info = model.transcribe(
            tmp_path,
            language="zh",
            vad_filter=True,  # 过滤静音段，短音频显著提速
        )
        segments = [
            {"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()}
            for s in segments_iter
        ]
        text = "".join(s["text"] for s in segments).strip()
        return {
            "text": text,
            "segments": segments,
            "duration_seconds": int(info.duration or 0),
        }
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:  # 临时文件清理失败不影响主流程
            logger.warning("临时音频文件清理失败：%s", tmp_path, exc_info=True)
