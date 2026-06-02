#!/usr/bin/env python3
"""Edge TTS 双语生成脚本 - 中文部分用中文声音，日语部分用日语声音，最后合并。

切分规则：「」包围 + 含假名 = 日语段；其他 = 中文段。
合并：用 ffmpeg concat demuxer（不重新编码，快）。
"""

import asyncio
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts

ZH_VOICE = "zh-CN-YunjianNeural"  # 中文男声，Passion 性格，评书风
JA_VOICE = "ja-JP-KeitaNeural"     # 日语男声
RATE = "-9%"                        # 速度 0.91 = -9%

KANA_PATTERN = re.compile(r"[぀-ゟ゠-ヿ]")  # 平/片假名
PUNCT_ONLY = re.compile(r"^[\s。，、！？．,.!?…\-—:：;；\"'“”‘’『』（）()「」]*$")

BASE = Path("/Users/ruishuang/Documents/jp-study")

# 10 大讲解专项 → 对应 audio 子目录名（去掉编号前缀）
DOC_NAMES = [
    "01-词的分类体系.md",
    "02-自他动词配对规律.md",
    "03-动词扩展变形.md",
    "04b-副词配套例句讲解.md",
    "05b-形式名词例句讲解.md",
    "06b-敬语三层例句讲解.md",
    "07b-复合动词例句讲解.md",
    "08b-接续文末例句讲解.md",
    "09b-类义动词辨析例句讲解.md",
    "10b-外来语例句讲解.md",
]

TARGETS = []
for _name in DOC_NAMES:
    _md = BASE / _name
    if _md.exists():
        # 去掉 "01-" / "04b-" / "10b-" 这种前缀，作为子目录名
        _sub = re.sub(r"^\d+[a-z]?-", "", _md.stem)
        TARGETS.append((_md, BASE / "audio" / _sub))


def split_by_separator(text: str) -> list:
    sections = re.split(r"\n---+\n", text)
    return [s.strip() for s in sections if s.strip()]


def slug_for(idx: int, text: str) -> str:
    first_line = text.splitlines()[0].strip()
    keep = re.findall(r"[「『][^」』]+[」』]|[一-鿿]+", first_line)
    tag = ("".join(keep[:2])[:20]) if keep else "section"
    return f"{idx:02d}-{tag}"


def split_lang_segments(text: str) -> list:
    """切分文本为 [(lang, content), ...]，按「」+ 含假名识别日语。"""
    segments = []
    buf = []

    def flush_zh():
        nonlocal buf
        if buf:
            content = "".join(buf).strip()
            if content:
                segments.append(("zh", content))
            buf = []

    i = 0
    while i < len(text):
        if text[i] == "「":
            end = text.find("」", i + 1)
            if end == -1:
                buf.append(text[i])
                i += 1
                continue
            inner = text[i : end + 1]
            if KANA_PATTERN.search(inner):
                flush_zh()
                segments.append(("ja", inner))
            else:
                buf.append(inner)
            i = end + 1
        else:
            buf.append(text[i])
            i += 1

    flush_zh()
    # 对含假名的中文段，按句号 / 换行细分，每个小句独立判 lang
    refined = []
    for lang, content in segments:
        if lang == "ja" or not KANA_PATTERN.search(content):
            refined.append((lang, content))
            continue
        for sentence in re.split(r"\n|(?<=[。！？])", content):
            s = sentence.strip()
            if not s or PUNCT_ONLY.match(s):
                continue
            sub_lang = "ja" if KANA_PATTERN.search(s) else "zh"
            refined.append((sub_lang, s))
    # 过滤纯标点段（避免 edge-tts 拒绝单字符段）
    refined = [(l, c) for l, c in refined if not PUNCT_ONLY.match(c.strip())]
    # 合并相邻同语言段
    merged = []
    for lang, content in refined:
        if merged and merged[-1][0] == lang:
            merged[-1] = [lang, merged[-1][1] + " " + content]
        else:
            merged.append([lang, content])
    return [tuple(m) for m in merged]


async def gen_audio(content: str, voice: str, output_path: Path) -> None:
    communicate = edge_tts.Communicate(content, voice, rate=RATE)
    # 单段最多 30 秒，避免某段卡死整个流程
    await asyncio.wait_for(communicate.save(str(output_path)), timeout=30)


def merge_mp3s(parts: list, output_path: Path) -> None:
    """用 ffmpeg concat demuxer 合并 mp3。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for p in parts:
            f.write(f"file '{p}'\n")
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
    finally:
        Path(list_path).unlink(missing_ok=True)


async def process_section(text: str, output_path: Path, tmp_dir: Path) -> tuple:
    segments = split_lang_segments(text)
    ja_count = sum(1 for s in segments if s[0] == "ja")
    zh_count = sum(1 for s in segments if s[0] == "zh")

    if len(segments) == 1:
        lang, content = segments[0]
        voice = JA_VOICE if lang == "ja" else ZH_VOICE
        await gen_audio(content, voice, output_path)
        return zh_count, ja_count

    parts = []
    try:
        for idx, (lang, content) in enumerate(segments):
            voice = JA_VOICE if lang == "ja" else ZH_VOICE
            part = tmp_dir / f"{output_path.stem}_part_{idx:03d}.mp3"
            await gen_audio(content, voice, part)
            parts.append(part)
        merge_mp3s(parts, output_path)
    finally:
        for p in parts:
            p.unlink(missing_ok=True)

    return zh_count, ja_count


async def process_file(md_path: Path, output_dir: Path, tmp_dir: Path) -> None:
    content = md_path.read_text(encoding="utf-8")
    sections = split_by_separator(content)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== {md_path.name}: {len(sections)} 段 ===", flush=True)

    for i, section in enumerate(sections, start=1):
        out = output_dir / (slug_for(i, section) + ".mp3")
        if out.exists():
            print(f"  [{i:02d}] 已存在：{out.name}", flush=True)
            continue
        print(f"  [{i:02d}] 生成中：{out.name}", flush=True)
        try:
            zh, ja = await process_section(section, out, tmp_dir)
            print(f"  [{i:02d}] ✓ 完成 (zh:{zh}, ja:{ja})", flush=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace")[-400:]
            print(f"  [{i:02d}] ✗ ffmpeg 失败:\n{stderr}", flush=True)
        except Exception as e:
            print(f"  [{i:02d}] ✗ 失败: {e}", flush=True)
        await asyncio.sleep(0.2)


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        for md_path, output_dir in TARGETS:
            if not md_path.exists():
                print(f"WARNING: 找不到 {md_path}", flush=True)
                continue
            await process_file(md_path, output_dir, tmp_dir)
    print("\n全部完成。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
