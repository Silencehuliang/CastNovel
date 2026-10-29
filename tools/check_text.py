"""正文校对：检测重复段落、代词混用、年份序列等低级硬伤。

用法：python tools/check_text.py [章号范围]
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from app import db

PID = 18
LO = int(sys.argv[1]) if len(sys.argv) > 1 else 1
HI = int(sys.argv[2]) if len(sys.argv) > 2 else 60


def paras(text):
    return [p.strip() for p in (text or "").split("\n") if p.strip()]


def check(idx, title, text):
    ps = paras(text)
    problems = []

    # 1) 相邻或近距重复段落
    seen = {}
    for i, p in enumerate(ps):
        if len(p) < 12:
            continue
        key = p[:40]
        if key in seen:
            problems.append(f"[重复] 段{i+1} 与 段{seen[key]+1} 几乎相同: {p[:36]}")
        else:
            seen[key] = i

    # 2) 代词混用：同一章内"他/她"都大量出现
    ta, na = text.count("他"), text.count("她")
    if ta >= 5 and na >= 5:
        problems.append(f"[人称] 本章「他」{ta} 次、「她」{na} 次，主角第三人称代词混用")

    # 3) 年份序列：景和N年 应连续
    yrs = re.findall(r"景和([一二三四五六七八九十]+)年", text)
    trans = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
             "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14,
             "十五": 15, "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20}
    nums = [trans.get(y, 0) for y in yrs if y in trans]
    for a, b in zip(nums, nums[1:]):
        if a and b and b < a:
            problems.append(f"[年份] 景和{a}年之后又出现景和{b}年，序列倒退")

    # 4) 量词/称谓混用
    if "半张咸鱼" in text and "半条咸鱼" in text:
        problems.append("[称谓] 「半张咸鱼」与「半条咸鱼」混用")
    if "库房" in text and "兵部库房" in text:
        problems.append("[称谓] 「库房」与「兵部库房」混用")

    return problems


def main():
    db.init()
    total = 0
    for c in db.list_chapters(PID):
        if not (LO <= c["idx"] <= HI) or not c["content"]:
            continue
        probs = check(c["idx"], c["title"], c["content"])
        if probs:
            print(f"第{c['idx']}章《{c['title']}》")
            for p in probs:
                print("   ", p)
            total += len(probs)
    print(f"\n合计问题 {total} 处")


if __name__ == "__main__":
    main()
