"""把某章正文与审校结果写入并定稿（不调用模型 API）。

用法：python tools/finalize_chapter.py 1
正文：data/rewrite/chNN.md    审校与摘要：data/rewrite/chNN.meta.json
评分低于 85 分拒绝定稿。
"""
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import db, stages
from app.config import load_settings

PID = 18
idx = int(sys.argv[1])
root = BASE_DIR / "data" / "rewrite"

body = re.sub(r"^#[^\n]*\n+", "", (root / f"ch{idx:02d}.md").read_text(encoding="utf-8")).strip()
meta = json.loads((root / f"ch{idx:02d}.meta.json").read_text(encoding="utf-8"))

ch = [c for c in db.list_chapters(PID) if c["idx"] == idx][0]

settings = load_settings(db)
gate = stages.run_gate(settings, db.get_project(PID), db.list_chapters(PID),
                       db.list_characters(PID), {**ch, "content": body})

score = meta["review"]["overall"]
if score < 85:
    print(f"审校 {score} 分 < 85，拒绝定稿")
    sys.exit(1)

db.add_version(ch["id"], "定稿前自动快照（人工撰写版）")
db.update_chapter(ch["id"], {
    "content": body,
    "summary": meta["summary"],
    "review": json.dumps(meta["review"], ensure_ascii=False),
    "gate": json.dumps(gate, ensure_ascii=False),
    "status": "final",
})
for c in meta.get("characters", []):
    db.upsert_character(PID, c["name"], status=c["status"])
db.touch_project(PID)

print(f"第{idx}章 定稿 | {gate['words']} 字 | 审校 {score} 分 | 门禁问题 {len(gate['issues'])} 项")
for k, v in meta["review"]["dimensions"].items():
    print(f"  {k}: {v}")
