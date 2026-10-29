"""把已定稿章节导出成纯文本（每章一个 txt），用于在网页编辑器里粘贴。

用法：python tools/export_chapters.py [起始章] [结束章]
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import db

db.init()
start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
end = int(sys.argv[2]) if len(sys.argv) > 2 else 12

out_dir = BASE_DIR / "data" / "upload"
out_dir.mkdir(parents=True, exist_ok=True)

n = 0
for c in db.list_chapters(18):
    if not (start <= c["idx"] <= end):
        continue
    body = (c["content"] or "").strip()
    if not body:
        continue
    # 段落之间保留一个空行，开头不带标题
    lines = [ln.strip() for ln in body.splitlines()]
    text = "\n\n".join([ln for ln in lines if ln])
    (out_dir / f"{c['idx']:02d}.txt").write_text(text, encoding="utf-8")
    n += 1
    print(f"{c['idx']:02d} {c['title']} {len(text)} 字")
print("exported:", n)
