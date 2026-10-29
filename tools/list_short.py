"""列出某章所有短应答（整段台词 ≤8 字），用于降低乒乓碎拍。"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
idx = int(sys.argv[1])
t = (BASE / "data" / "rewrite" / f"ch{idx:02d}.md").read_text(encoding="utf-8")
ps = [p.strip() for p in t.split("\n") if p.strip()]
n = 0
for i, p in enumerate(ps):
    if not p.startswith('"') and not p.startswith("「"):
        continue
    c = "".join(re.findall(r'"(.*?)"', p, re.S)) + "".join(re.findall(r"「(.*?)」", p, re.S))
    if len(c.strip()) <= 8:
        n += 1
        print(f"{n:>3} | {c.strip()}")
print("短应答合计:", n)
