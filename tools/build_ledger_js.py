#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 ledger.json 导出成网页可直接 <script> 读取的 web/ledger.js。

为什么要这一步：浏览器用 file:// 打开时 fetch 本地 json 会被 CORS 挡，
现有 web/index.html 就是用 <script src=data.js> 的方式喂数据。这里沿用同一套路，
让 web/vocab.html 能读到你真实的 SRS 状态（mastery / due），
新学的词第一次出现时就能从 ledger 里"认领"已有的掌握度，而不是从零开始。

用法： python3 tools/build_ledger_js.py
（学完一次、或 /jp-chat 更新过账本后跑一下即可刷新网页里的 SRS 状态。）
"""
import os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "ledger.json")
OUT = os.path.join(ROOT, "web", "ledger.js")


def main():
    with open(LEDGER, encoding="utf-8") as f:
        d = json.load(f)

    # 只把网页用得到的字段带出去，瘦身
    slim_items = []
    for it in d.get("items", []):
        slim_items.append({
            "id": it.get("id"),
            "jp": it.get("jp", ""),
            "kana": it.get("kana", ""),
            "gloss": it.get("gloss", ""),
            "mastery": it.get("mastery", 0),
            "due": it.get("due", ""),
            "last_seen": it.get("last_seen", ""),
            "exposures": it.get("exposures", 0),
        })

    payload = {
        "meta": {
            "srs_intervals_days": d.get("meta", {}).get("srs_intervals_days", [1, 1, 3, 7, 16, 35]),
            "daily_new_quota": d.get("meta", {}).get("daily_new_quota", 5),
            "last_session": d.get("meta", {}).get("last_session", ""),
        },
        "items": slim_items,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.LEDGER_DATA = ")
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"✓ 写出 {OUT}（{len(slim_items)} 项 ledger 词条）")


if __name__ == "__main__":
    main()
