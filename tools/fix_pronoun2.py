"""全书代词统一（跨段版）：把指主角的第三人称「他」改为「她」。

与段落版的区别：追踪「最近出现的人名」，跨段落也能判断「他」指谁。
用法：
  python tools/fix_pronoun2.py          # 只统计
  python tools/fix_pronoun2.py --write  # 写入数据库与源稿
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from app import db

PID = 18
WRITE = "--write" in sys.argv

# 主角及女性角色（代词指向这些名字时，段落内的「他」视为主角）
# 陆铮为女性（缇骑司指挥使），全书统一用「她」
FEMALE = ("赵铁柱", "柳如烟", "青黛", "素心", "阿棠", "绿珠", "杏儿", "花九娘", "小满", "陆铮")
# 明确男性角色
MALE = ("萧景琰", "沈墨", "周承业", "周崇", "刘全", "刘公公", "包三爷",
        "冯某", "魏忠", "全公公", "沈大人", "王爷", "尚书", "首辅",
        "孙武", "赵四", "张大人", "师父", "兄长")


def convert(text):
    prot = text.replace("他们", "\x01").replace("他人", "\x02").replace("其他", "\x03")
    out = []
    pos = 0
    last_f = -9999
    last_m = -9999
    changed = 0
    for m in re.finditer(r"他", prot):
        i = m.start()
        seg = prot[pos:i]
        # 更新最近人名
        for k in FEMALE:
            j = seg.rfind(k)
            if j >= 0:
                last_f = pos + j + len(k)
        for k in MALE:
            j = seg.rfind(k)
            if j >= 0:
                last_m = pos + j + len(k)
        if last_f > last_m:
            out.append(seg)
            out.append("她")
            changed += 1
            pos = i + 1
        else:
            out.append(seg)
            out.append("他")
            pos = i + 1
    out.append(prot[pos:])
    new = "".join(out).replace("\x01", "他们").replace("\x02", "他人").replace("\x03", "其他")
    return new, changed


def main():
    db.init()
    total = 0
    for c in db.list_chapters(PID):
        if not c["content"]:
            continue
        new, ch = convert(c["content"])
        if ch:
            total += ch
            print(f"第{c['idx']:>3}章 改 {ch} 处")
            if WRITE:
                db.update_chapter(c["id"], {"content": new})
                p = BASE / "data" / "rewrite" / f"ch{c['idx']:02d}.md"
                if p.exists():
                    t = p.read_text(encoding="utf-8")
                    head, sep, rest = t.partition("\n")
                    nt, _ = convert(rest)
                    p.write_text(head + sep + nt, encoding="utf-8")
    print(f"\n共改 {total} 处" + ("（已写入）" if WRITE else "（仅统计）"))


if __name__ == "__main__":
    main()
