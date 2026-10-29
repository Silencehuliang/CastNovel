"""全部提示词模板(中文)。分阶段:
设定 → 角色 → 大纲 → 章纲(节拍)→ 正文 → 润色 → 审校 → 修订 → 摘要 → 状态抽取。
"""


def _sys(project, extra=""):
    style = project.get("style") or "沉浸感强、细节扎实的成熟网文风格"
    return (
        "你是一位资深小说主编兼职业作家,擅长长篇结构设计、人物塑造与类型化写作。"
        f"本书类型:{project.get('genre') or '未指定'}。文风:{style}。"
        "你的输出只会被程序或作者本人使用:直接给出要求的内容,不要客套,不要解释你将怎么做。"
        + (("\n" + extra) if extra else "")
    )


def worldview_messages(project):
    premise = project.get("premise") or "(作者尚未写梗概,请依据类型自行设定一个有张力的核心冲突)"
    user = f"""请为这部小说设计世界观设定。要求:
1. 时代与舞台、力量/技术/规则体系、主要势力与冲突格局、社会常识与禁忌;
2. 核心冲突要能支撑 30 章以上的长篇推进,并留有可扩展空间;
3. 设定要具体可写:给出名词、数字、规则细节,而不是空洞形容词;
4. 使用 markdown 分节,总长 1500 字以内。

【书名】{project.get('title')}
【类型】{project.get('genre') or '未指定'}
【作者梗概】{premise}"""
    return [
        {"role": "system", "content": _sys(project)},
        {"role": "user", "content": user},
    ]


def characters_messages(project):
    user = f"""基于以下世界观与梗概,设计 4~8 个核心角色(含主角)。输出 JSON:
{{"characters": [{{"name": "", "role": "主角/女主/反派/配角/导师 等", "profile": "80~150字:身份、性格、欲望、伤痕、口头禅或标志物", "status": "开篇时的处境,20字内"}}]}}

【世界观】
{project.get('worldview') or '(暂无)'}

【梗概】{project.get('premise') or '(暂无)'}"""
    return [
        {"role": "system", "content": _sys(project, "只输出 JSON。")},
        {"role": "user", "content": user},
    ]


def outline_messages(project, count):
    user = f"""请设计全书大纲,共 {count} 章。输出 JSON:
{{"volumes": [{{"title": "卷名", "chapters": [起始章号, 结束章号], "summary": "本卷主线与高潮,60字内"}}],
"chapters": [{{"idx": 1, "title": "章节题(4~8字,有画面感)", "summary": "本章发生什么:目标-阻碍-转折-结果,60~90字", "hook": "章末钩子,15字内"}}]}}

硬性要求:
1. {count} 章逐章列出,不缺号不重复;每章都要有不可替代的推进,不能水章;
2. 采用「小高潮-大高潮」波浪节奏,每卷末为一个大高潮;
3. 主角要有清晰的欲望线与成长弧;伏笔至少埋 2 处(在 summary 中注明「伏笔:」);
4. 严格遵守世界观设定,不得与之冲突。

【书名】{project.get('title')}
【类型】{project.get('genre') or '未指定'}
【梗概】{project.get('premise') or '(暂无)'}

【世界观】
{project.get('worldview') or '(暂无)'}

【角色】
{project.get('_characters_brief') or '(暂无)'}"""
    return [
        {"role": "system", "content": _sys(project, "只输出 JSON。")},
        {"role": "user", "content": user},
    ]


def beats_messages(project, chapter, context, total):
    user = f"""请把第 {chapter['idx']}/共{total} 章《{chapter.get('title', '')}》展开为 3~6 个写作节拍(beats)。输出 JSON:
{{"beats": ["场景/节拍描述:地点、出场人物、事件推进、情绪转折,40~70字一条"]}}

要求:节拍覆盖「进入-推进-高潮-收束+钩子」;信息量递进;符合前情与人物动机。

【本章概要】{chapter.get('summary') or '(暂无)'}

【上下文】
{context}"""
    return [
        {"role": "system", "content": _sys(project, "只输出 JSON。")},
        {"role": "user", "content": user},
    ]


def draft_messages(project, chapter, beats, context, words):
    beats_txt = "\n".join(f"{i+1}. {b}" for i, b in enumerate(beats)) if beats else "(无,按概要自由发挥)"
    style_ref = (project.get("style_ref") or "").strip()
    style_block = f"""

【文风样章(只模仿其叙述节奏、句式与用词密度,严禁抄袭其内容与人名)】
{style_ref[:900]}""" if style_ref else ""
    user = f"""请撰写第 {chapter['idx']} 章《{chapter.get('title', '')}》正文。

{context}
{style_block}

【本章概要】{chapter.get('summary') or '(暂无)'}

【本章节拍】
{beats_txt}

【写作要求】
1. 正文约 {words} 字;节拍中应有主次,高潮处放慢镜头多给细节,过渡处一笔带过;
2. 开头自然承接上文,严禁「上一章说到」式复述;严禁与【前情脉络】重复已知信息;
3. 对话要有性格与信息量;多用动作、感官细节写情绪,少用形容词堆砌;
4. 视角贴主视角人物,信息释放与人物认知一致(人物不知道的不要写出来);
5. 结尾落在钩子上(悬念/危机/转折);
6. 直接输出正文:不要章节号、不要标题、不要任何解释或元话语。"""
    return [
        {"role": "system", "content": _sys(project)},
        {"role": "user", "content": user},
    ]


def polish_messages(project, chapter, content):
    user = f"""请润色下面这一章正文:保持情节、信息、人物动机完全不变,只提升文笔与节奏。
除通用要求外,重点「去 AI 味」,以下表达一律改写或删除:
- 空洞套话:「仿佛在诉说着什么」「空气中弥漫着……的气息」「一丝不易察觉的……」「阳光透过树叶洒下斑驳的光影」;
- 滥用三段式排比、每段结尾强行升华;
- 过度使用「眸中闪过一丝」「嘴角勾起一抹弧度」等模板动作;
- 形容词堆砌而无一手细节。
代之以具体动作、五感细节、符合人物的口语化对话;短句与长句交错;修正病句与错别字。直接输出润色后的全文。

【正文】
{content}"""
    return [
        {"role": "system", "content": _sys(project)},
        {"role": "user", "content": user},
    ]


def review_messages(project, chapter, context, content):
    user = f"""你是严格的网文审校编辑。请审校第 {chapter['idx']} 章《{chapter.get('title', '')}》,输出 JSON:
{{"overall": 0-100总分,
"dimensions": {{"情节推进": 0-100, "人物一致性": 0-100, "文笔表现": 0-100, "节奏张力": 0-100}},
"issues": [{{"type": "逻辑/重复/人设崩坏/节奏/文笔/衔接", "severity": "高/中/低", "desc": "具体指出位置与问题"}}],
"suggestions": ["可执行的修改建议"]}}

重点检查:与前情是否矛盾;人物言行是否符合档案;是否复述已知信息;结尾钩子是否有力;字数是否达标(约{project.get('_words', 3000)}字)。

【上下文(前情与设定)】
{context}

【本章正文】
{content}"""
    return [
        {"role": "system", "content": _sys(project, "只输出 JSON。")},
        {"role": "user", "content": user},
    ]


def revise_messages(project, chapter, content, review_text):
    user = f"""请按审校意见修订本章正文。要求:逐条落实 issues 与 suggestions;未涉及部分尽量保留原文;不得新增情节线;直接输出修订后的全文。

【审校意见】
{review_text}

【原文】
{content}"""
    return [
        {"role": "system", "content": _sys(project)},
        {"role": "user", "content": user},
    ]


def summary_messages(project, chapter, prev_summaries, content):
    prev = prev_summaries[-8:]
    prev_txt = "\n".join(f"第{ch['idx']}章:{ch['summary']}" for ch in prev if ch.get("summary")) or "(无)"
    user = f"""请用 120 字以内概括第 {chapter['idx']} 章《{chapter.get('title', '')}》的内容,供后续章节写作时做前情参考。
必须包含:关键事件及结果、人物处境变化、埋下或回收的伏笔。只输出摘要本身。

【前文摘要】
{prev_txt}

【本章正文】
{content[:6000]}"""
    return [
        {"role": "system", "content": _sys(project, "只输出摘要,不要任何前后缀。")},
        {"role": "user", "content": user},
    ]


def state_extract_messages(project, chapter, content, known_chars, open_fores):
    user = f"""阅读第 {chapter['idx']} 章《{chapter.get('title', '')}》正文,抽取叙事状态变化与读者认知,输出 JSON:
{{"characters": [{{"name": "人物名(必须是已知人物或新出场人物)", "role": "主角/配角/反派/新角色 等", "profile": "仅新角色填写:60字内介绍;已知人物填空字符串", "status": "本章后的最新状态:伤势/修为/位置/关系变化等,30字内"}}],
"foreshadowing": [{{"title": "伏笔名(10字内)", "description": "伏笔内容,40字内", "action": "plant 或 resolve"}}],
"plotlines": [{{"title": "情节线名(10字内,如:复仇线/感情线/寻宝线)", "description": "该线当前进展,40字内", "action": "start、advance 或 end"}}],
"reader_state": [{{"kind": "known 或 expect 或 mislead", "content": "读者视角的一句话,30字内", "action": "add 或 resolve"}}]}}

规则:
1. characters 只列本章有实质状态变化或新出场的人物;已知人物:{known_chars};
2. foreshadowing:本章埋下新的钩子/悬念 → action=plant;本章回收了已有伏笔 → action=resolve(title 需与已有伏笔名一致,已有:{open_fores});
3. plotlines:本章开启新故事线 → start;既有故事线有推进 → advance;故事线终结 → end;
4. reader_state 从「读者」视角记录(读者知道什么,而非角色知道什么):
   - known:本章向读者揭示的关键事实(真相、秘密、身世、规则等)→ add;
   - expect:本章结尾让读者产生的期待或悬念承诺 → add;
   - mislead:本章刻意引导的误认(读者被引导相信、但与作者真相不符)→ add;
   - 本章兑现了此前的期待,或揭穿了此前的误导 → action=resolve,content 填原条目内容;
   - 与前文重复的认知不再记录。各数组无变化时为空。
5. 所有 action 只能取上述英文值;同一数组内不要出现重复的 name/title/content;
6. 人物状态涉及死亡/复活时必须写明「死亡」「复活」「诈死」等明确标记,不要用模糊状态制造回退。

【正文】
{content[:8000]}"""
    return [
        {"role": "system", "content": _sys(project, "只输出 JSON。")},
        {"role": "user", "content": user},
    ]


def deai_messages(project, chapter, content, spans):
    """两步去 AI 味管线第二步:span 级定位清单 + 带例改写方向(arXiv:2409.14509)。"""
    span_txt = "\n".join(
        f"{i + 1}. 第{s['line']}行「{s['span']}」→ {s['msg']};改写方向:{s['suggestion']}"
        for i, s in enumerate(spans)
    )
    user = f"""下面的正文经程序化检测,定位出 {len(spans)} 处「AI 味」表达(含行号与原句)。请逐条改写:
1. 只改这些片段及其前后衔接句,其余部分尽量一字不动,保持总字数不明显缩水;
2. 每处按给出的「改写方向」重写:用具体动作、五感细节、符合人物的口语替代模板表达;
3. 情节、信息、人物动机、叙述顺序完全不变;
4. 直接输出修改后的全文,不要章节号、不要任何解释。

【检测清单】
{span_txt}

【第 {chapter['idx']} 章《{chapter.get('title', '')}》正文】
{content}"""
    return [
        {"role": "system", "content": _sys(project)},
        {"role": "user", "content": user},
    ]


def rank_messages(project, chapter, candidates, words):
    """候选拉样+重排(Re3 的 Sample-Rank 思路):多稿对比,主编选出最优。"""
    labels = [c["label"] for c in candidates]
    blocks = []
    for c in candidates:
        g = c.get("gate") or {}
        warns = sum(1 for i in (g.get("issues") or []) if i.get("level") in ("warn", "advisory"))
        blocks.append(
            f"━━━ 候选{c['label']} ━━━(字数 {g.get('words', '?')},门禁告警 {warns} 项)\n{c['text'][:4200]}")
    beats_txt = chapter.get("beats") or "(无)"
    user = f"""以下是为第 {chapter['idx']} 章《{chapter.get('title', '')}》生成的 {len(candidates)} 个正文候选。请以主编身份逐一评审,选出最适合采用的版本。输出 JSON:
{{"best": "候选编号之一({'/'.join(labels)})", "scores": {{{', '.join(f'"{lb}": 0-100' for lb in labels)}}}, "reason": "60字内说明选择理由与主要取舍"}}

评判标准:
1. 节拍完成度:是否覆盖概要与节拍的每个要点,有无遗漏或跑偏;
2. 人物一致性:言行是否符合档案与当前状态;
3. 文笔与画面:细节是否具体、有无 AI 味套话;
4. 结尾钩子是否有力;门禁告警越少越好。

【本章概要】{chapter.get('summary') or '(暂无)'}
【本章节拍】
{beats_txt}

{chr(10).join(blocks)}"""
    return [
        {"role": "system", "content": _sys(project, "只输出 JSON。")},
        {"role": "user", "content": user},
    ]
