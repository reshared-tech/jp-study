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

# ---------- 内置「基础卡」：数字/月份/日期/星期（侧栏独立条目，可浏览+练习）----------
# 每项 (prompt 提示, reading 读法, written 书写形可空)；prompt 不暴露读法，逼提取
NUMS = [("1","いち",""),("2","に",""),("3","さん",""),("4","よん",""),("5","ご",""),
        ("6","ろく",""),("7","なな",""),("8","はち",""),("9","きゅう",""),("10","じゅう","")]
MONTHS = [(f"{i}月", r, f"{i}月") for i,r in zip(range(1,13),
        ["いちがつ","にがつ","さんがつ","しがつ","ごがつ","ろくがつ","しちがつ",
         "はちがつ","くがつ","じゅうがつ","じゅういちがつ","じゅうにがつ"])]
DAYS = [(f"{i}日", r, f"{i}日") for i,r in zip(range(1,32),
        ["ついたち","ふつか","みっか","よっか","いつか","むいか","なのか","ようか","ここのか","とおか",
         "じゅういちにち","じゅうににち","じゅうさんにち","じゅうよっか","じゅうごにち","じゅうろくにち",
         "じゅうしちにち","じゅうはちにち","じゅうくにち","はつか","にじゅういちにち","にじゅうににち",
         "にじゅうさんにち","にじゅうよっか","にじゅうごにち","にじゅうろくにち","にじゅうしちにち",
         "にじゅうはちにち","にじゅうくにち","さんじゅうにち","さんじゅういちにち"])]
WEEK = [("周一","げつようび","月曜日"),("周二","かようび","火曜日"),("周三","すいようび","水曜日"),
        ("周四","もくようび","木曜日"),("周五","きんようび","金曜日"),("周六","どようび","土曜日"),
        ("周日","にちようび","日曜日")]

BASIC_DECKS = [
    {"slug":"num",   "label":"🔢 数字 1–10",  "intro":"看数字，限时说出日语读法。4/7/9 各有两读，这里用最安全的 よん／なな／きゅう。", "items":NUMS},
    {"slug":"month", "label":"📅 月份 1–12月", "intro":"看「N月」，说读法。★ 4月=しがつ、7月=しちがつ、9月=くがつ（不用 よん/なな/きゅう）。", "items":MONTHS},
    {"slug":"day",   "label":"📆 日期 1–31日", "intro":"日期读法大量不规则。重点：1–10日 + 14日(じゅうよっか)·20日(はつか)·24日(にじゅうよっか)。", "items":DAYS},
    {"slug":"week",  "label":"🗓 星期",        "intro":"看中文星期，说日语。钩子：月火水木金土日。", "items":WEEK},
]

def build_basic_days():
    days = []
    for deck in BASIC_DECKS:
        sents = []
        for i,(prompt, reading, written) in enumerate(deck["items"], 1):
            sents.append({"n": i, "cat": deck["label"], "jp": reading, "kana": written, "cn": prompt})
        days.append({
            "date": deck["label"], "slug": deck["slug"], "kind": "basic",
            "logs": [{"title": deck["label"], "md": "# "+deck["label"]+"\n\n"+deck["intro"]}],
            "sentences": sents,
        })
    return days


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
            d.setdefault("kind", "log")
            d["logs"].append({"title": title, "md": md})
    dated = [days[k] for k in sorted(days.keys(), reverse=True)]
    for d in dated:
        d.setdefault("kind", "log")
    return dated + build_basic_days()


async def synth(text, path):
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)


async def gen_audio(days):
    tasks = []
    for d in days:
        slug = d.get("slug", d["date"])
        for s in d["sentences"]:
            fname = f"{slug}-s{s['n']:02d}.mp3"
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
