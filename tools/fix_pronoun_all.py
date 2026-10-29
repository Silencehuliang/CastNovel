"""全书代词统一：把指主角（花魁柳如烟／兵王赵铁柱）的第三人称「他」改为「她」。

判定规则（段落级启发式）：
1. 段落里出现「赵铁柱」「柳如烟」「姑娘」→ 该段内的「他」视为主角代词，改为「她」。
2. 段落里出现明确男性角色名且不出现主角名 → 段内「他」保留。
3. 「他们」「他人」等词先保护再还原。

用法：
  python tools/fix_pronoun_all.py          # 只统计，不写入
  python tools/fix_pronoun_all.py --write  # 写入数据库与源稿
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

FEMALE = ("赵铁柱", "柳如烟", "姑娘", "青黛", "素心", "阿棠", "绿珠", "杏儿", "花九娘", "陆夫人")
MALE = ("萧景琰", "沈墨", "陆铮", "周承业", "周崇", "刘全", "刘公公", "包三爷", "小满", "冯某")


def convert_paragraph(p):
    """返回 (新段落, 是否改动, 主角他次数, 男性他次数)"""
    if "他" not in p:
        return p, False, 0, 0
    has_f = any(k in p for k in FEMALE)
    has_m = any(k in p for k in MALE)
    if not has_f:
        return p, False, 0, p.count("他")
    # 段落内有女性名但也有男性名：逐个「他」按最近人名判断
    out = []
    idx = 0
    changed = False
    prot = p.replace("他们", "\x01").replace("他人", "\x02").replace("其他", "\x03")
    pieces = re.split(r"(他)", prot)
    last_f = -999
    last_m = -999
    for k in ("赵铁柱", "柳如烟", "姑娘", "青黛", "素心", "阿棠", "绿珠", "杏儿", "花九娘"):
        i = p.rfind(k)
        if i > last_f:
            last_f = i
    for k in MALE:
        i = p.rfind(k)
        if i > last_m:
            last_m = i
    pos = 0
    for piece in pieces:
        if piece == "他":
            if last_f > last_m:
                out.append("她")
                changed = True
            else:
                out.append(piece)
        else:
            out.append(piece)
    new = "".join(out).replace("\x01", "他们").replace("\x02", "他人").replace("\x03", "其他")
    return new, changed, new.count("她") - p.count("她"), p.count("他") - new.count("他")


def convert_text(text):
    paras = text.split("\n")
    out = []
    changed = 0
    remain_male = 0
    for p in paras:
        np_, ch, _, male = convert_paragraph(p)
        out.append(np_)
        if ch:
            changed += 1
        remain_male += male
    return "\n".join(out), changed, remain_male


def main():
    db.init()
    total_ch = 0
    for c in db.list_chapters(PID):
        if not c["content"]:
            continue
        new, changed, male = convert_text(c["content"])
        if changed:
            total_ch += changed
            print(f"第{c['idx']:>3}章 改 {changed} 段 | 该段残留「他」(男性) {male} 处")
            if WRITE:
                db.update_chapter(c["id"], {"content": new})
                p = BASE / "data" / "rewrite" / f"ch{c['idx']:02d}.md"
                if p.exists():
                    t = p.read_text(encoding="utf-8")
                    head, _, rest = t.partition("\n")
                    nt, _, _ = convert_text(rest)
                    p.write_text(head + "\n" + nt, encoding="utf-8")
    print(f"\n共改动 {total_ch} 段" + ("（已写入）" if WRITE else "（仅统计，加 --write 写入）"))


if __name__ == "__main__":
    main()
