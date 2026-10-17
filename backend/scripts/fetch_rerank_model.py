"""下载 rerank 模型（v4.6）：拉取 ONNX 量化模型与 tokenizer 到本地目录。

为什么用 ONNX：本机 **无可用 GPU**（CPU-only），且项目已因 faster-whisper 装了
`onnxruntime` —— 走 ONNX 只需再装一个 ~3MB 的 `tokenizers`，**不必引入 torch**
（CPU 版 torch 及其依赖会把 venv 体积翻数倍）。

为什么用镜像：本机 `huggingface.co` 不可达（2026-10-13 实测返回 000），
`hf-mirror.com` 可达（200）。可用 `--base` 覆盖。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe -m scripts.fetch_rerank_model           # 缺失才下载
    ./.venv/Scripts/python.exe -m scripts.fetch_rerank_model --force   # 强制重下
    ./.venv/Scripts/python.exe -m scripts.fetch_rerank_model --base https://huggingface.co

下载完成后把 `kb_rerank_enabled=True` 打开即生效（provider 默认 onnx）。
"""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings

DEFAULT_BASE = "https://hf-mirror.com"
# 文件 → 必要性说明（model.onnx fp32 约 1.1GB，量化版约 280MB，优先量化版）
FILES = (
    ("tokenizer.json", "分词器配置（必需）"),
    ("onnx/model_quantized.onnx", "int8 量化模型（CPU 上首选）"),
)


def _download(client: httpx.Client, url: str, target: Path) -> int:
    """流式下载，返回字节数；失败抛异常由调用方汇总。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    written = 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1024 * 256):
                handle.write(chunk)
                written += len(chunk)
                if total:
                    pct = written / total * 100
                    print(f"\r    {target.name}: {pct:5.1f}%", end="", flush=True)
    print()
    tmp.replace(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 rerank ONNX 模型")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    parser.add_argument("--base", default=DEFAULT_BASE, help="模型仓库镜像地址")
    args = parser.parse_args()

    repo = settings.kb_rerank_model
    target_dir = Path(settings.kb_rerank_model_dir)
    print(
        f"== 下载 rerank 模型 ==\n  仓库：{repo}\n  镜像：{args.base}\n  目录：{target_dir}\n"
    )

    failed: list[str] = []
    with httpx.Client(
        timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True
    ) as client:
        for name, note in FILES:
            target = target_dir / name
            if target.is_file() and not args.force:
                print(f"  [跳过] {name} 已存在（{target.stat().st_size / 1e6:.1f} MB）")
                continue
            url = f"{args.base}/{repo}/resolve/main/{name}"
            print(f"  [下载] {name} —— {note}")
            try:
                size = _download(client, url, target)
                print(f"    完成：{size / 1e6:.1f} MB")
            except Exception as exc:  # noqa: BLE001  单个文件失败不阻断其余文件
                print(f"    [失败] {exc}")
                failed.append(name)

    if failed:
        print(f"\n[中止] 以下文件下载失败：{', '.join(failed)}")
        print("       可重试，或换镜像：--base https://huggingface.co")
        sys.exit(1)

    missing = [n for n, _ in FILES if not (target_dir / n).is_file()]
    if missing:
        print(f"\n[中止] 仍缺文件：{missing}")
        sys.exit(1)

    print(
        "\n[完成] 现在把 backend/.env 里的 kb_rerank_enabled 设为 true（或在 config 里改默认），"
        "\n       再跑 ./.venv/Scripts/python.exe -m scripts.eval_rag 对比三路指标。"
    )


if __name__ == "__main__":
    main()
