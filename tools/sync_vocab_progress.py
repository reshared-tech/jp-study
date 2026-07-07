#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 web/vocab.html 导出的 vocab-progress-*.json 合并回 ledger.json。

网页背单词的进度存在浏览器 localStorage，点"⬇ 导出进度"会下载一个
vocab-progress-YYYY-MM-DD.json。把它放到仓库根目录（或用 --file 指定），
跑这个脚本，就把每张卡的 mastery/due/复习历史写回 ledger.json——
让 /jp-chat 的"大脑"和网页背单词共用同一套 SRS 状态。

规则：
  · 按 jp（日语写法）匹配 ledger 里已有词条 → 更新 mastery/due/last_seen/exposures
    （网页是你最新一次的复习信号，以它为准；但保留 first_seen/connections/notes/scenes）
  · ledger 里没有的词 → 新建条目，source="vocab"，gloss 取中文释义
  · 默认 dry-run 只报告；确认无误后加 --apply 真正写入（会先备份 ledger.json）

用法：
  python3 tools/sync_vocab_progress.py                 # 找最新的 vocab-progress-*.json，dry-run
  python3 tools/sync_vocab_progress.py --apply         # 真正写入
  python3 tools/sync_vocab_progress.py --file 路径 --apply
"""
import os, sys, json, glob, shutil, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "ledger.json")


def find_export(explicit):
    if explicit:
        return explicit
    cands = sorted(glob.glob(os.path.join(ROOT, "vocab-progress-*.json")))
    if not cands:
        cands = sorted(glob.glob(os.path.join(ROOT, "**", "vocab-progress-*.json"), recursive=True))
    return cands[-1] if cands else None


def mk_id(key):
    # "v14#3" -> "vocab_v14_3"
    return "vocab_" + key.replace("#", "_")


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    explicit = None
    if "--file" in args:
        explicit = args[args.index("--file") + 1]

    exp_path = find_export(explicit)
    if not exp_path or not os.path.exists(exp_path):
        print("✗ 找不到 vocab-progress-*.json。先在网页点「⬇ 导出进度」，把文件放到仓库根目录。")
        sys.exit(1)

    with open(exp_path, encoding="utf-8") as f:
        exp = json.load(f)
    with open(LEDGER, encoding="utf-8") as f:
        led = json.load(f)

    items = led.setdefault("items", [])
    by_jp = {it.get("jp"): it for it in items if it.get("jp")}

    updated, created = 0, 0
    for key, rec in exp.get("items", {}).items():
        jp = rec.get("jp")
        if not jp:
            continue
        m = int(rec.get("mastery", 0))
        due = rec.get("due", "")
        last = rec.get("last", "") or exp.get("exported", "")
        seen = int(rec.get("seen", 0))

        it = by_jp.get(jp)
        if it:
            it["mastery"] = m
            it["due"] = due or it.get("due", "")
            if last:
                it["last_seen"] = last
            it["exposures"] = max(int(it.get("exposures", 0)), seen)
            src = it.setdefault("scenes", [])
            if "背单词" not in src:
                src.append("背单词")
            updated += 1
        else:
            new = {
                "id": mk_id(key),
                "type": "word",
                "source": "vocab",
                "jp": jp,
                "kana": rec.get("kana", ""),
                "gloss": rec.get("cn", ""),
                "example": "",
                "first_seen": last or exp.get("exported", ""),
                "last_seen": last or exp.get("exported", ""),
                "exposures": seen,
                "active_output": 0,
                "scenes": ["背单词"],
                "connections": [],
                "mastery": m,
                "due": due,
                "notes": f"网页背单词导入（{rec.get('level','')}·{rec.get('deck','')}）",
            }
            items.append(new)
            by_jp[jp] = new
            created += 1

    print(f"来源：{os.path.relpath(exp_path, ROOT)}")
    print(f"匹配更新 {updated} 条 · 新建 {created} 条 · ledger 现共 {len(items)} 条")

    if not apply:
        print("\n(dry-run) 以上未写入。确认无误后加 --apply 真正写入。")
        return

    bak = LEDGER + "." + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + ".bak"
    shutil.copy2(LEDGER, bak)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 已写入 ledger.json（备份：{os.path.relpath(bak, ROOT)}）")
    print("提示：可再跑 python3 tools/build_ledger_js.py 刷新网页里的 SRS 状态。")


if __name__ == "__main__":
    main()
