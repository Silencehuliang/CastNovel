"""把正文源稿首行标题同步为数据库中的章名。"""
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import db

db.init()
n = 0
for c in db.list_chapters(18):
    p = BASE_DIR / "data" / "rewrite" / f"ch{c['idx']:02d}.md"
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8")
    new = re.sub(r"^#[^\n]*", f"# 第{c['idx']}章 {c['title']}", text, count=1)
    if new != text:
        p.write_text(new, encoding="utf-8")
        n += 1
print("synced:", n)
