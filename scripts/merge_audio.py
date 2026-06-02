#!/usr/bin/env python3
"""把每个文档子目录下的分段 mp3 合并为一个完整 mp3，放到 audio/ 根目录。"""

import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path("/Users/ruishuang/Documents/jp-study/audio")


def merge_dir(sub_dir: Path) -> tuple:
    mp3_files = sorted(sub_dir.glob("*.mp3"))
    if not mp3_files:
        return (False, "无 mp3 文件")

    output_path = BASE / f"{sub_dir.name}.mp3"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for mp3 in mp3_files:
            f.write(f"file '{mp3.resolve()}'\n")
        list_path = f.name

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path, "-c", "copy", str(output_path),
            ],
            check=True,
            capture_output=True,
        )
        size_mb = output_path.stat().st_size / 1024 / 1024
        return (True, f"{len(mp3_files)} 段 → {size_mb:.1f} MB")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace")[-400:]
        return (False, stderr)
    finally:
        Path(list_path).unlink(missing_ok=True)


def main():
    sub_dirs = sorted([d for d in BASE.iterdir() if d.is_dir()])
    print(f"=== 合并 {len(sub_dirs)} 份文档音频 ===\n")

    for sub in sub_dirs:
        ok, info = merge_dir(sub)
        flag = "✓" if ok else "✗"
        print(f"  {flag} {sub.name}.mp3 — {info}")


if __name__ == "__main__":
    main()
