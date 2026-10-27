"""确定性去 AI 味 lint(零 token)。

依据:"Can AI Writing Be Salvaged?"(arXiv:2409.14509)的结论——抽象风格指令无效,
有效的是 span 级检测 + 具体改写方向;以及中文生态的实践(Humanizer-zh、oh-story lint、
inkflow 硬规则)沉淀的高频 AI 腔模板。

输出 advisory 级问题清单(规则命中 + 行号位置 + 改写方向),并入一致性门禁报告。
规则集刻意保持数据化,便于用户自行增删(v1.0 可插拔 Skill 的雏形)。
"""

RULES = [
    {"id": "cliche_air", "pattern": r"空气中弥漫着[^。]{0,12}的?气息",
     "msg": "AI 高频套话「空气中弥漫着……的气息」", "suggestion": "改为具体的嗅觉或体感细节", "limit": 0},
    {"id": "cliche_telling", "pattern": r"仿佛在诉说[^。]{0,8}",
     "msg": "AI 高频套话「仿佛在诉说着……」", "suggestion": "删掉,直接写那个东西本身", "limit": 0},
    {"id": "cliche_subtle", "pattern": r"[一二]丝不易察觉的",
     "msg": "AI 高频套话「一丝不易察觉的……」", "suggestion": "写具体动作,不要写『不易察觉』", "limit": 0},
    {"id": "cliche_light", "pattern": r"(洒下斑驳的|斑驳的光影|透过[树叶枝叶缝隙]+洒下)",
     "msg": "AI 高频写景模板(树叶光影)", "suggestion": "换一个非默认的景物细节", "limit": 0},
    {"id": "template_eye", "pattern": r"(眸中|眼底|眼中)闪过一丝",
     "msg": "模板动作「眸中闪过一丝……」", "suggestion": "用可观察的动作或台词代替内心特写", "limit": 1},
    {"id": "template_mouth", "pattern": r"嘴角(勾起|扬起)(一抹|一丝)",
     "msg": "模板动作「嘴角勾起一抹弧度」", "suggestion": "写笑的具体形态与声音", "limit": 1},
    {"id": "template_fist", "pattern": r"(握紧|攥紧)了拳头",
     "msg": "高频重复动作「握紧了拳头」", "suggestion": "全章至多一次;换别的紧张动作", "limit": 1},
    {"id": "template_heart", "pattern": r"(心(脏)?猛地一(缩|沉|揪)|如遭雷击|瞳孔(骤然)?一缩)",
     "msg": "高频震惊反应模板", "suggestion": "同类反应全章至多一次,其余用行为延迟表达", "limit": 2},
    {"id": "scaffold_correlation", "pattern": r"不是[^。]{1,20}而是",
     "msg": "「不是……而是……」解释腔(全章超过 2 次即 AI 腔)", "suggestion": "过半改为直接陈述", "limit": 2},
    {"id": "scaffold_summary", "pattern": r"(值得注意的是|总而言之|综上所述|不得不说|要知道)",
     "msg": "论述文衔接词混入叙事", "suggestion": "删除,叙事不需要议论文路标", "limit": 0},
    {"id": "scaffold_double_simile", "pattern": r"像[^,。]{1,15},?又像",
     "msg": "「像……又像……」双联比喻", "suggestion": "留一个更准的喻体", "limit": 1},
    {"id": "scaffold_emotion_label", "pattern": r"(感到一阵)?(深深的|强烈的)?(无力感|挫败感|成就感|释然感?|危机感)",
     "msg": "情绪直接贴标签", "suggestion": "改为身体反应或动作外化(oh-story:身体反应外化)", "limit": 1},
]

_DASH = "——"
_DASH_LIMIT_PER_3K = 4  # inkflow 硬规则:破折号每章上限


def lint_text(text):
    """返回 [{rule, level, count, msg, suggestion, sample}]。level: advisory / warn。"""
    import re

    text = text or ""
    issues = []
    for rule in RULES:
        hits = re.findall(rule["pattern"], text)
        count = len(hits)
        if count <= rule["limit"]:
            continue
        issues.append({
            "rule": rule["id"],
            "level": "warn" if count > rule["limit"] * 2 else "advisory",
            "count": count,
            "msg": rule["msg"],
            "suggestion": rule["suggestion"],
            "sample": (hits[0] if isinstance(hits[0], str) else hits[0][0])[:30],
        })
    dash = text.count(_DASH)
    budget = max(1, round(len(text) / 3000)) * _DASH_LIMIT_PER_3K
    if dash > budget:
        issues.append({
            "rule": "dash_overuse", "level": "advisory", "count": dash,
            "msg": f"破折号 {dash} 处,超出本章预算 {budget}",
            "suggestion": "破折号是 AI 味重灾区,改用逗号或句号断句", "sample": "",
        })
    return issues
