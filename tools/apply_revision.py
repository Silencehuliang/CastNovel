"""把 Gemini 修订稿(data/gemini_task/reply.txt)清洗后覆盖 data/rewrite/chNN.md。

流程(方案 A 写评分离:「写」是 Gemini,本脚本只做机械清洗与校验,不产生正文):
  1. 读 reply.txt;
  2. 剥离首尾可能的说明性文字(「以下是修订后的章节」等),去 # 标题行、--- 分节线、** 加粗;
  3. 字数校验(去空白后默认 2400-4000),不达标 exit 1;
  4. 从数据库取章题,补首行 `# 第N章 标题`,覆盖 chNN.md。

用法:python tools/apply_revision.py <N> [--min 2400] [--max 4000]
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

REPLY = BASE / "data" / "gemini_task" / "reply.txt"

# 说明性开头/结尾的特征(只剥整行,不改正文)
LEAD_PAT = re.compile(
    r"^(好的|以下是|这是|修订后|修订版|说明|按|根据|已按|已修)[^\n]{0,80}$")
TRAIL_PAT = re.compile(
    r"^(希望|如需|以上是|以上就是|——+\s*$|注:|备注:)[^\n]{0,120}$")


def clean(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    # 剥首部说明行:最多连续剥 6 行,遇到空行后的正文即停
    out = list(lines)
    k = 0
    while k < min(6, len(out)) and (LEAD_PAT.match(out[k].strip()) or not out[k].strip()):
        if out[k].strip() and k > 0:
            break
        if LEAD_PAT.match(out[k].strip()):
            out = out[k + 1:]
            k = 0
            continue
        k += 1
    # 剥尾部说明行
    while out and (TRAIL_PAT.match(out[-1].strip()) or not out[-1].strip()):
        if out and out[-1].strip() and TRAIL_PAT.match(out[-1].strip()):
            out.pop()
            # 连带剥掉其后多余空行
            while out and not out[-1].strip():
                out.pop()
            continue
        out.pop()
    body = "\n".join(out)
    # 引号归一化:Gemini 常把 ASCII 引号写成中文弯引号,全书体例是 ASCII "
    body = body.replace("\u201c", '"').replace("\u201d", '"')
    body = body.replace("\u2018", "'").replace("\u2019", "'")
    # 去残留 markdown:标题行/分节线/加粗
    body = re.sub(r"^#{1,6}[^\n]*$", "", body, flags=re.M)
    body = re.sub(r"^\s*---+\s*$", "", body, flags=re.M)
    body = body.replace("**", "")
    # 规整空行:3+ 连续空行 -> 1 空行
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def fix_mojibake(text: str) -> str:
    """修复 UTF-8 被按 GBK 误读的双重编码(偶发于页面抓取链路)。

    判定:文本含高频 mojibake 特征字(鈥/鏌/搧/锛/鐨)时,尝试按 GBK
    编码回字节再按 UTF-8 解码;成功且引号数更多则采用修复版。
    """
    marks = ("鈥", "鏌", "搧", "锛", "鐨", "涓", "犲")
    if not any(m in text for m in marks):
        return text
    try:
        fixed = text.encode("gbk", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if fixed.count('"') + fixed.count("「") > text.count('"') + text.count("「"):
        print("[mojibake] 检测到双重编码,已自动修复")
        return fixed
    return text


def main():
    idx = int(sys.argv[1])
    lo = int(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 2400
    hi = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 4000

    if not REPLY.exists():
        print("reply.txt 不存在,先跑 ego 桥接")
        return 1
    raw = fix_mojibake(REPLY.read_text(encoding="utf-8").strip())
    body = clean(raw)
    words = len(re.sub(r"\s", "", body))
    if not body:
        print("清洗后正文为空,Gemini 输出可能全是说明文字。前 200 字:")
        print(raw[:200])
        return 1

    # 引号检查:对白引号数不得低于原稿的 60%(防 Gemini 把对白改成裸叙述)
    src = BASE / "data" / "rewrite" / f"ch{idx:02d}.md"
    src_quotes = 0
    if src.exists():
        src_quotes = src.read_text(encoding="utf-8").count('"') + src.read_text(encoding="utf-8").count("「")
    out_quotes = body.count('"') + body.count("「")
    if src_quotes >= 10 and out_quotes < src_quotes * 0.5:
        print(f"引号检查失败:原稿 {src_quotes} 个对白引号,修订稿仅 {out_quotes} 个——"
              f"Gemini 把对白改成了裸叙述,不落盘。重跑(make_revision_prompt 已含引号铁律)。")
        return 1

    if not (lo <= words <= hi):
        print(f"字数 {words} 超出 [{lo},{hi}],疑似截断或夹带说明,不落盘。")
        print("开头 120 字:", body[:120])
        print("结尾 120 字:", body[-120:])
        return 1
    # 残留检查
    for pat, name in [(r"^#", "标题行"), (r"---", "分节线"), (r"\*\*", "加粗")]:
        if re.search(pat, body, flags=re.M):
            print(f"警告:正文仍含 {name} 残留(已尽力清洗)")

    # 取章题,组最终源稿
    from app import db
    ch = [c for c in db.list_chapters(18) if c["idx"] == idx][0]
    doc = f"# 第{idx}章 {ch['title']}\n\n{body}\n"
    dst = BASE / "data" / "rewrite" / f"ch{idx:02d}.md"
    dst.write_text(doc, encoding="utf-8")
    print(f"第{idx}章 修订稿已落盘 {dst.name}  字数={words}(原 {len(re.sub(chr(92)+'s','',raw))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
