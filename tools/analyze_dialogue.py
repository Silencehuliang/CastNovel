"""统计各章对话密度，用于检查"连续对话"是否影响阅读。

判定：按空行切段。含「」或“”引号的段落算作对白段。
输出：每章对白段占比、最长连续对白段、单段最长字数。
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from app import db

QUOTE = re.compile(r'["「]')
NARR = re.compile(r'["」]')


def analyze(text):
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    if not paras:
        return None
    dial = [bool(QUOTE.search(p)) for p in paras]
    total = len(paras)
    d = sum(dial)
    # 最长连续对白段
    best = cur = 0
    for x in dial:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    # 对白段中，引号之间的纯台词字数占全文字数比
    spoken = sum(len(m) for p in paras for m in re.findall(r'["「](.*?)["」]', p, re.S))
    chars = len(re.sub(r"\s", "", text or ""))
    return {
        "paras": total,
        "dial": d,
        "ratio": d / total,
        "run": best,
        "spoken_ratio": spoken / chars if chars else 0,
        "chars": chars,
    }


def main():
    db.init()
    rows = db.list_chapters(18)
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    print(f"{'章':>3} {'字数':>5} {'对白段占比':>10} {'最长连对白':>10} {'台词字数占比':>12}")
    bad = []
    for c in rows:
        if not (lo <= c["idx"] <= hi) or not c["content"]:
            continue
        a = analyze(c["content"])
        if not a:
            continue
        flag = ""
        if a["ratio"] > 0.55 or a["run"] >= 6:
            flag = "  <== 高"
            bad.append(c["idx"])
        print(f"{c['idx']:>3} {a['chars']:>5} {a['ratio']*100:>9.1f}% {a['run']:>10} "
              f"{a['spoken_ratio']*100:>11.1f}%{flag}")
    print("\n需要处理的章:", bad)


if __name__ == "__main__":
    main()
