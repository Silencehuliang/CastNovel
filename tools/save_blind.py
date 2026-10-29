"""把 task 盲评的完整输出解析成每章的 data/rewrite/chNN.blind.json。

用法：python tools/save_blind.py [起始章]
从 CodeBuddy history 目录里找最新的 task-call_*.txt，解析其中的 JSON 数组。
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HIST = Path(r"C:\Users\silence\AppData\Local\CodeBuddyExtension\Data")

cands = sorted(HIST.rglob("tool-results/task-call_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
if not cands:
    print("找不到 task 输出文件")
    raise SystemExit(1)

raw = None
for p in cands[:6]:
    t = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(\[\s*\{\s*\"chapter\".*)", t, re.S)
    if m:
        raw = m.group(1)
        # 截到最后一个完整的 ]
        end = raw.rfind("]")
        raw = raw[: end + 1]
        break

if not raw:
    print("未解析到 JSON")
    raise SystemExit(1)

data = json.loads(raw)
print("解析到", len(data), "章：")
for c in data:
    idx = c["chapter"]
    hi = [i for i in (c.get("issues") or []) if i.get("severity") == "高"]
    mid = [i for i in (c.get("issues") or []) if i.get("severity") == "中"]
    (BASE / "data" / "rewrite" / f"ch{idx:02d}.blind.json").write_text(
        json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  第{idx}章 {c.get('title','')} {c['overall']}分 | 高危 {len(hi)} 中 {len(mid)}")
    for i in hi:
        print(f"     [高] {i.get('desc','')[:100]}")
