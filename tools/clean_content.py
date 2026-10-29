"""清理数据库中所有章节正文里的 Markdown 分节线（---）与残留格式。

用法：python tools/clean_content.py
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from app import db

PID = 18


def clean(t):
    t = re.sub(r"\n\s*---\s*\n", "\n", t or "")
    t = re.sub(r"^\s*---\s*\n", "", t)
    t = re.sub(r"\n\s*---\s*$", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)   # 去掉正文里的 Markdown 加粗
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def main():
    db.init()
    n = 0
    for c in db.list_chapters(PID):
        if not c["content"]:
            continue
        new = clean(c["content"])
        if new != c["content"]:
            db.update_chapter(c["id"], {"content": new})
            n += 1
    # 同步清理源稿
    m = 0
    for p in sorted((BASE / "data" / "rewrite").glob("ch*.md")):
        t = p.read_text(encoding="utf-8")
        nt = re.sub(r"\n\s*---\s*\n", "\n", t)
        nt = re.sub(r"^\s*---\s*\n", "", nt)
        nt = re.sub(r"\n\s*---\s*$", "", nt)
        if nt != t:
            p.write_text(nt, encoding="utf-8")
            m += 1
    print(f"已清理数据库章节 {n} 章，源稿 {m} 个")


if __name__ == "__main__":
    main()
