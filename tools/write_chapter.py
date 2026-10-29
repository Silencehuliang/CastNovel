"""把某章正文写入数据库并跑程序化门禁（不调用模型 API）。

用法：python tools/write_chapter.py 1
正文来源：data/rewrite/chNN.md

门禁含对话密度硬指标（2026-10 新增，针对「连续对话过多」问题）：
  - 对白段占比 ≤ 45%
  - 台词字数占比 ≤ 38%
  - 最长连续对白段 ≤ 5 段
三项全部通过才算 gate 通过；不通过时 exit code 为 1。
"""
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import db, stages
from app.config import load_settings

PID = 18

# 对话密度阈值
DIAL_RATIO_MAX = 0.45   # 含对白的段落占比
SPOKEN_RATIO_MAX = 0.38  # 台词字数 / 全文字数
DIAL_RUN_MAX = 5         # 最长连续对白段（乒乓碎拍另由 SHORT_MAX 控制）
SHORT_MAX = 0.35         # 短应答（≤8 字）占对白段比例，防止乒乓碎拍

QUOTE = re.compile(r'["「]')
# 整段即台词：以引号开头（避免段内嵌套引号被误判）
IS_DIALOG = re.compile(r'^\s*["「]')


def dialogue_stats(text):
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    dial = [bool(IS_DIALOG.match(p)) for p in paras]
    best = cur = 0
    start = best_start = 0
    for i, x in enumerate(dial):
        if x:
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    spoken = sum(len(m) for p in paras for m in re.findall(r'["「](.*?)["」]', p, re.S))
    chars = len(re.sub(r"\s", "", text or ""))
    short = 0
    dial_paras = 0
    for p, is_d in zip(paras, dial):
        if not is_d:
            continue
        dial_paras += 1
        content = "".join(re.findall(r'["「](.*?)["」]', p, re.S))
        if len(content.strip()) <= 8:
            short += 1
    return {
        "ratio": (sum(dial) / len(paras)) if paras else 0,
        "run": best,
        "run_start": best_start,
        "run_text": paras[best_start:best_start + best] if best else [],
        "spoken_ratio": (spoken / chars) if chars else 0,
        "short_ratio": (short / dial_paras) if dial_paras else 0,
        "short_count": short,
        "dial_paras": dial_paras,
    }


idx = int(sys.argv[1])
body = re.sub(
    r"^#[^\n]*\n+",
    "",
    (BASE_DIR / "data" / "rewrite" / f"ch{idx:02d}.md").read_text(encoding="utf-8"),
)
# 去掉 Markdown 分节线（---），正文不保留，避免发布后出现横线
body = re.sub(r"\n\s*---\s*\n", "\n", body)
body = re.sub(r"^\s*---\s*\n", "", body)
body = re.sub(r"\n\s*---\s*$", "", body)
body = body.strip()

ch = [c for c in db.list_chapters(PID) if c["idx"] == idx][0]
db.update_chapter(ch["id"], {"content": body, "status": "draft"})

settings = load_settings(db)
gate = stages.run_gate(settings, db.get_project(PID), db.list_chapters(PID),
                       db.list_characters(PID), {**ch, "content": body})

print("words =", gate["words"], "/ target", gate["target"])
print("overlap =", gate["overlap"], "| char_hits =", gate["char_hits"])
issues = list(gate["issues"])

fail = False
if not issues:
    print("gate: clean")
for it in issues:
    print(f"- [{it['level']}] {it['type']}: {it['msg']}")
    if it.get("level") == "error":
        fail = True

# ---- 对话密度硬门禁 ----
d = dialogue_stats(body)
print(f"dialogue: 对白段 {d['ratio']*100:.1f}% (≤{DIAL_RATIO_MAX*100:.0f}%) | "
      f"台词字数 {d['spoken_ratio']*100:.1f}% (≤{SPOKEN_RATIO_MAX*100:.0f}%) | "
      f"最长连对白 {d['run']} 段 (≤{DIAL_RUN_MAX}) | "
      f"短应答 {d['short_ratio']*100:.0f}% (≤{SHORT_MAX*100:.0f}%)")
if d["ratio"] > DIAL_RATIO_MAX:
    print("- [error] 对话密度: 对白段占比超标，需要在连续对白之间插入动作、环境、心理或静默")
    fail = True
if d["spoken_ratio"] > SPOKEN_RATIO_MAX:
    print("- [error] 对话密度: 台词字数占比超标，需要压缩台词、扩充叙述")
    fail = True
if d["run"] > DIAL_RUN_MAX:
    print(f"- [error] 对话密度: 连续对白 {d['run']} 段，超过 {DIAL_RUN_MAX} 段上限，需用叙述打断")
    for i, seg in enumerate(d["run_text"]):
        print(f"    {i+1}. {seg[:46]}")
    fail = True
if d["short_ratio"] > SHORT_MAX and d["short_count"] > 15 and d["dial_paras"] >= 25:
    print(f"- [error] 对话密度: 短应答（≤8 字）{d['short_count']} 个，占对白段 {d['short_ratio']*100:.0f}%，"
          f"乒乓式碎拍，需合并成完整句或改成动作")
    fail = True

print("gate:", "PASS" if not fail else "FAIL")
sys.exit(1 if fail else 0)
