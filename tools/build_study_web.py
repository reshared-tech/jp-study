#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描 日志/ 下的学习日志，生成 web/data.js（供 web/index.html 读取），
并把"跟读例句"文件里的每一句用 Edge TTS 生成单独的 mp3（一句一个），
放到 音频/句子/ 下。学完一次跑一下即可刷新网页数据。

用法： python3 tools/build_study_web.py
"""
import os, re, json, glob, asyncio, datetime
import edge_tts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "日志")
AUDIO_DIR = os.path.join(ROOT, "音频", "句子")
WEB_DIR = os.path.join(ROOT, "web")
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(WEB_DIR, exist_ok=True)

VOICE = "ja-JP-NanamiNeural"
RATE = "-10%"

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-(.+)\.md$")
SENT_RE = re.compile(r"^\s*(\d+)\.\s+(.*\S)\s*$")
H2_RE = re.compile(r"^##\s+(.*\S)\s*$")


def parse_sentences(path):
    """从'跟读例句'文件解析： N. 日文 / 假名 / 中文（注）"""
    out = []
    cat = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            mh = H2_RE.match(line)
            if mh:
                cat = mh.group(1)
                continue
            m = SENT_RE.match(line)
            if not m:
                continue
            n = int(m.group(1))
            # 分隔符是 "/"（两侧空格不定），稳妥地按 斜杠 切，最多切 3 段
            parts = [p.strip() for p in re.split(r"\s*/\s*", m.group(2))]
            jp = parts[0] if len(parts) > 0 else ""
            kana = parts[1] if len(parts) > 1 else ""
            cn = " / ".join(parts[2:]) if len(parts) > 2 else ""
            cn = re.sub(r"\*\*", "", cn)  # 去掉中文里的 markdown 粗体符号
            kana = re.sub(r"\*\*", "", kana)
            # 去掉日文里可能残留的 markdown 粗体
            jp_clean = re.sub(r"\*\*", "", jp)
            out.append({"n": n, "cat": cat, "jp": jp_clean, "kana": kana, "cn": cn})
    return out


def collect():
    """按日期聚合：content 日志 + 例句"""
    days = {}
    for path in sorted(glob.glob(os.path.join(LOG_DIR, "*.md"))):
        fn = os.path.basename(path)
        m = DATE_RE.search(fn)
        if not m:
            continue
        date, title = m.group(1), m.group(2)
        d = days.setdefault(date, {"date": date, "logs": [], "sentences": []})
        with open(path, encoding="utf-8") as f:
            md = f.read()
        if "跟读例句" in title:
            d["sentences"] = parse_sentences(path)
        else:
            d["logs"].append({"title": title, "md": md})
    return [days[k] for k in sorted(days.keys(), reverse=True)]


async def synth(text, path):
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)


async def gen_audio(days):
    tasks = []
    for d in days:
        for s in d["sentences"]:
            fname = f"{d['date']}-s{s['n']:02d}.mp3"
            fpath = os.path.join(AUDIO_DIR, fname)
            rel = os.path.join("..", "音频", "句子", fname)
            s["audio"] = rel.replace(os.sep, "/")
            if not os.path.exists(fpath):  # 已存在就跳过，省时
                tasks.append((s["jp"], fpath))
    print(f"需要生成 {len(tasks)} 句新音频（已存在的跳过）...")
    for jp, fpath in tasks:
        await synth(jp, fpath)
        print("  ✓", os.path.basename(fpath), jp)


def write_data(days):
    data = {
        "generated": datetime.date.today().isoformat(),
        "days": days,
    }
    js = "window.STUDY_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n"
    with open(os.path.join(WEB_DIR, "data.js"), "w", encoding="utf-8") as f:
        f.write(js)
    total_s = sum(len(d["sentences"]) for d in days)
    print(f"写入 web/data.js：{len(days)} 天，{total_s} 句例句。")


def main():
    days = collect()
    asyncio.run(gen_audio(days))
    write_data(days)
    print("完成。打开 web/index.html 即可。")


if __name__ == "__main__":
    main()
