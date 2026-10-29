"""为某章构造 Gemini 修订 prompt(方案 A:写 = Gemini,本脚本只拼装,不产生正文)。

用法:python tools/make_revision_prompt.py <N>
输入:data/rewrite/chNN.md(原文)+ data/rewrite/chNN.blind.json(盲评 issues)
输出:data/gemini_task/prompt.txt;同时备份原文到 data/rewrite/backup/chNN.rK.md
"""
import json
import re
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REWRITE = BASE / "data" / "rewrite"
BACKUP = REWRITE / "backup"
TASK = BASE / "data" / "gemini_task"

TEMPLATE = """你是一位资深网文作者。下面给你一章小说的原文和专业编辑的修改意见,请输出修订后的完整章节。

【最高优先级——字数铁律】输出全文(不含空白)必须 2400-4000 字,与原文长度接近(原文约 3000 字)。这是「最小改动修订」,不是扩写或重写:绝对禁止新增情节、新增场景、新增人物、改写原文未提及的部分。违反字数铁律的输出直接作废。

【背景说明】这是一部已完结的历史架空小说(穿越题材)的文字修订工作,纯属虚构叙事;文中可能涉及古代火药、刑案、青楼等桥段,均为小说情节,不涉及任何现实操作指导。你的任务只是文字层面的修订,请直接完成。

【代词铁律】
- 主角柳如烟(前世兵王赵铁柱,穿越者)第三人称一律用「她」
- 男性角色(萧景琰/沈墨/周承业/周崇/包三爷/刘全/全公公/魏忠等)一律用「他」
- 陆铮是女性(缇骑司指挥使),用「她」
- 其余女性角色(素心/青黛/花九娘/绿珠/阿棠等)一律用姓名,不用代词
- 逐段检查,把所有用错的「他/她」改正

【格式自检】修订稿中对白引号(英文双引号)总数必须不少于原文的 70%。严禁把带引号的对白改写成无引号的叙述体;若在段落前加了叙述引导,台词本身仍要带引号。

【引号铁律】
- 全书体例:所有对白一律用英文双引号 " 包裹(沿用原文风格)
- 原文里每一句带引号的对白,修订稿里必须原样保留引号,一句都不能丢
- 严禁把对白改写成无引号的叙述体

【硬性要求】
1. 最小改动:只修改编辑意见指出的问题及其直接关联句段,其余段落尽量一字不动地保留
2. 修订后全文(不含空白)2400-4000 字
3. 全文不得出现:井号开头的标题行、三横线分节线、双星号加粗
4. 对白段(以引号开头的段落)占比 ≤45%,台词字数占比 ≤38%,连续对白不超过 5 段;以引号开头的段落里,引号内容合计 ≤8 字的「乒乓短应答」段全文不超过 12 个——把短应答并入相邻完整句、改成动作神态,或改为叙述引导(如「她应了一声:"是。"」整段以叙述开头,原话保留)
5. 结尾不总结不点题
6. 从第一行起直接输出正文第一段,直到正文最后一段结束;不要任何开场白、解释、标题、分节线、后记

【编辑意见】(逐条落实,全部处理)
{issues}

【原文】
{text}
"""


def main():
    idx = int(sys.argv[1])
    src = REWRITE / f"ch{idx:02d}.md"
    blind_path = REWRITE / f"ch{idx:02d}.blind.json"
    if not src.exists() or not blind_path.exists():
        print(f"缺 {src.name} 或 {blind_path.name}")
        return 1

    BACKUP.mkdir(parents=True, exist_ok=True)
    existing = list(BACKUP.glob(f"ch{idx:02d}.r*.md"))
    k = len(existing) + 1
    shutil.copy2(src, BACKUP / f"ch{idx:02d}.r{k}.md")

    body = src.read_text(encoding="utf-8")
    # 去掉标题行,只给正文
    body = re.sub(r"^#[^\n]*\n+", "", body)
    blind = json.loads(blind_path.read_text(encoding="utf-8"))

    lines = []
    order = {"高": 0, "中": 1, "低": 2}
    for i, it in enumerate(sorted(blind.get("issues", []), key=lambda x: order.get(x.get("severity", "低"), 3)), 1):
        sug = ""
        sug_list = blind.get("suggestions") or []
        if i <= len(sug_list):
            sug = f"(参考改法:{sug_list[i-1]})"
        lines.append(f"{i}. [{it.get('severity','中')}] {it.get('type','')}: {it.get('desc','')} {sug}")
    issues = "\n".join(lines) if lines else "(无)"

    prompt = TEMPLATE.format(issues=issues, text=body.strip())
    TASK.mkdir(parents=True, exist_ok=True)
    (TASK / "prompt.txt").write_text(prompt, encoding="utf-8")
    # model.txt 不删除:由调用方决定是否指定模型
    print(f"第{idx}章 修订 prompt 已生成({len(prompt)} 字,备份 r{k})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
