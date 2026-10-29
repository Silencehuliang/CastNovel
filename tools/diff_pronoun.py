"""对比备份与当前数据库，找出代词统一改动的段落，供人工抽查。

用法：python tools/diff_pronoun.py <章号> [显示条数]
"""
import glob
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
idx = int(sys.argv[1])
lim = int(sys.argv[2]) if len(sys.argv) > 2 else 15

backups = sorted(glob.glob(str(BASE / "data" / "novelforge.backup-*.db")))
if not backups:
    print("没有备份")
    raise SystemExit
old_db = sqlite3.connect(backups[-1])
new_db = sqlite3.connect(str(BASE / "data" / "novelforge.db"))

q = "SELECT content FROM chapters WHERE project_id=18 AND idx=?"
old = old_db.execute(q, (idx,)).fetchone()
new = new_db.execute(q, (idx,)).fetchone()
if not old or not new:
    print("无数据")
    raise SystemExit

op = [p.strip() for p in (old[0] or "").split("\n")]
np_ = [p.strip() for p in (new[0] or "").split("\n")]

lines = []
n = 0
for i, (a, b) in enumerate(zip(op, np_), 1):
    if a != b:
        n += 1
        if n <= lim:
            lines.append(f"--- 段{i}")
            lines.append(f"  旧: {a[:110]}")
            lines.append(f"  新: {b[:110]}")
lines.append(f"共 {n} 段被改")
out = BASE / "tools" / "diff_out.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"written -> {out}")
