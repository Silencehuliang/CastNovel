"""把独立盲审结果写入章节（与作者自评分并存），并打印分差。

用法：python tools/apply_blind_review.py <章号> [盲审json文件]
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from app import db

PID = 18


def main():
    idx = int(sys.argv[1])
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE / "data" / "rewrite" / f"ch{idx:02d}.blind.json"
    blind = json.loads(path.read_text(encoding="utf-8"))
    ch = [c for c in db.list_chapters(PID) if c["idx"] == idx][0]
    try:
        cur = json.loads(ch.get("review") or "{}")
    except Exception:
        cur = {}
    cur["independent"] = blind
    hist = cur.get("independent_history") or []
    hist.append({"reviewer": blind.get("reviewer"), "overall": blind.get("overall"),
                 "dimensions": blind.get("dimensions"),
                 "issue_count": len(blind.get("issues") or []),
                 "high_count": sum(1 for i in (blind.get("issues") or []) if i.get("severity") == "高")})
    cur["independent_history"] = hist
    db.update_chapter(ch["id"], {"review": json.dumps(cur, ensure_ascii=False)})

    mine = cur.get("overall")
    ind = blind.get("overall")
    gap = mine - ind if isinstance(mine, (int, float)) and isinstance(ind, (int, float)) else None
    hi = [i for i in (blind.get("issues") or []) if i.get("severity") == "高"]
    print(f"第{idx}章  自评 {mine} / 盲审 {ind}" + (f"  分差 {gap:+d}" if gap is not None else ""))
    print("盲审四维:", blind.get("dimensions"))
    print("高危问题:", len(hi))
    for it in hi:
        print("  -", it.get("desc", "")[:120])
    print("定稿门槛：以盲审分为准（>=85）")


if __name__ == "__main__":
    main()
