"""抽查某章中「她」的用法，人工确认没有把男性角色的代词误改。

用法：python tools/inspect_she.py <章号> [条数]
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
idx = int(sys.argv[1])
lim = int(sys.argv[2]) if len(sys.argv) > 2 else 20
t = (BASE / "data" / "rewrite" / f"ch{idx:02d}.md").read_text(encoding="utf-8")
out = []
for i, l in enumerate(t.split("\n"), 1):
    if "她" in l:
        out.append(f"{i:>4} | {l.strip()[:96]}")
print("\n".join(out[:lim]))
print(f"… 共 {len(out)} 行含「她」")
