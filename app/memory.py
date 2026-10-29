"""三层记忆系统:为每章生成组装「结构化上下文」。

1. 静态层:世界观、文风、角色档案(故事圣经);另有 Lorebook:按关键词命中注入的设定条目;
2. 序列层:全书各章摘要(滚动压缩)+ 上一章结尾原文(口吻衔接);
3. 检索层:对旧章摘要做字符二元组 BM25 检索(或配置 embedding 档案后走向量余弦),
   把与本章最相关的旧情节拉回上下文;另附「待回收伏笔」清单,供正文呼应。

BM25 中文友好、零外部依赖;向量检索为可选增强,任何异常自动降级回 BM25。
"""
import math
import re
from collections import Counter


def _tokens(text):
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9]+", text)
    han = re.sub(r"[^\u4e00-\u9fff]+", " ", text)
    bigrams = []
    for seg in han.split():
        if len(seg) == 1:
            bigrams.append(seg)
        else:
            bigrams.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return words + bigrams


def topk_chunks(query, docs, k=3):
    """docs: [{"id", "text"}],返回最相关的 id 列表(简单 idf 加权)。"""
    if not docs:
        return []
    q = Counter(_tokens(query))
    doc_toks = [Counter(_tokens(d["text"])) for d in docs]
    df = Counter()
    for toks in doc_toks:
        df.update(toks.keys())
    n = len(docs)
    scores = []
    for idx, toks in enumerate(doc_toks):
        s = 0.0
        for t, c in q.items():
            if t in toks and df.get(t):
                s += c * math.log(1 + n / df[t])
        scores.append((s, idx))
    scores.sort(key=lambda x: -x[0])
    return [docs[i]["id"] for s, i in scores[:k] if s > 0]


def _clip(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def hit_lore(lore, *texts, max_entries=8, entry_limit=400):
    """Lorebook 命中:关键词(逗号/顿号/空格分隔)出现在任一给定文本中即注入。"""
    blob = "\n".join(t or "" for t in texts)
    if not blob.strip():
        return []
    hits = []
    for e in lore or []:
        if not e.get("enabled"):
            continue
        kws = [k.strip() for k in re.split(r"[,，、;；\s]+", e.get("keywords") or "") if k.strip()]
        matched = [k for k in kws if k in blob]
        if matched:
            hits.append((e, matched))
        if len(hits) >= max_entries:
            break
    lines = []
    for e, matched in hits:
        lines.append(f"- [{ '、'.join(matched[:3]) }] {_clip(e.get('content'), entry_limit)}")
    return lines


def build_context(project, characters, chapters, foreshadowing, target_idx, settings,
                  lore=None, query_vec=None, doc_vectors=None, reader=None):
    """组装写第 target_idx 章时所需的上下文文本。

    lore: Lorebook 条目(关键词命中注入);
    query_vec/doc_vectors: 可选向量语义检索(为空时自动用 BM25);
    reader: 读者认知状态(读者已知/期待/误认,与角色已知、作者真相分离)。
    """
    gen = settings.get("gen", {})
    world_limit = 2200
    char_limit = 280
    summ_limit = 240

    parts = []
    worldview = _clip(project.get("worldview"), world_limit)
    if worldview:
        parts.append("【世界观与既定事实】\n" + worldview)
    if project.get("style"):
        parts.append("【文风要求】" + _clip(project["style"], 300))

    beats_txt = ""
    for ch in chapters:
        if ch["idx"] == target_idx:
            beats_txt = ch.get("beats") or ""
    query = " ".join(
        x for x in [project.get("premise", ""), project.get("genre", ""), beats_txt] if x
    )

    # 角色档案:数量少全给,数量多按相关性挑选
    chars = list(characters)
    if len(chars) > 8 and query:
        docs = [{"id": c["name"], "text": f"{c['name']} {c['role']} {c['profile']} {query}"} for c in chars]
        keep = set(topk_chunks(query, docs, k=8))
        chars = [c for c in chars if c["name"] in keep]
    if chars:
        lines = [
            f"- {c['name']}({c['role'] or '角色'}):{_clip(c['profile'], char_limit)}"
            + (f"【当前状态:{_clip(c['status'], 80)}】" if c.get("status") else "")
            for c in chars
        ]
        parts.append("【主要角色档案】\n" + "\n".join(lines))

    prev = [ch for ch in chapters if ch["idx"] < target_idx and (ch.get("summary") or ch.get("content"))]
    if prev:
        window = int(gen.get("summary_window", 6))
        recent = prev[-window:]
        older = prev[:-window]
        lines = []
        for ch in older:
            lines.append(f"第{ch['idx']}章《{ch.get('title', '')}》")
        for ch in recent:
            s = ch.get("summary") or _clip(ch.get("content"), summ_limit)
            lines.append(f"第{ch['idx']}章《{ch.get('title', '')}》:{_clip(s, summ_limit)}")
        parts.append("【前情脉络】\n" + "\n".join(lines))

    last = next((ch for ch in chapters if ch["idx"] == target_idx - 1), None)
    if last and last.get("content"):
        tail = _clip(last["content"][-1400:], 1400)
        parts.append("【上一章结尾(衔接口吻,勿复述)】\n" + tail)

    # 检索层:旧章摘要 + 开头片段(向量可用则余弦相似,否则 BM25)
    if query and prev:
        docs = []
        for ch in prev:
            docs.append({
                "id": ch["idx"],
                "text": f"第{ch['idx']}章《{ch.get('title', '')}》 {(ch.get('summary') or '')} {(ch.get('content') or '')[:400]}",
            })
        topk = int(gen.get("retrieval_topk", 3))
        dvecs = doc_vectors or {}
        if query_vec and dvecs:
            usable = [d for d in docs if d["id"] in dvecs]
            scored = sorted(((cosine(query_vec, dvecs[d["id"]]), d) for d in usable),
                            key=lambda x: -x[0])
            ids = {d["id"] for s, d in scored[:topk] if s > 0.01}
        else:
            ids = set(topk_chunks(query, docs, k=topk))
        hits = [d for d in docs if d["id"] in ids and d["id"] != (last or {}).get("idx")]
        if hits:
            lines = []
            by_idx = {ch["idx"]: ch for ch in prev}
            for d in hits:
                ch = by_idx.get(d["id"], {})
                frag = _clip((ch.get("summary") or (ch.get("content") or "")[:300]), 300)
                lines.append(f"第{ch.get('idx')}章《{ch.get('title', '')}》:{frag}")
            parts.append("【与本章相关的旧情节(避免重复、注意呼应)】\n" + "\n".join(lines))

    # Lorebook:关键词命中的设定条目(优先级高于检索,直接注入)
    lore_lines = hit_lore(lore, beats_txt, *(ch.get("summary") or "" for ch in prev[-3:]),
                          (last or {}).get("content", "")[-2000:])
    if lore_lines:
        parts.append("【设定注入(Lorebook 关键词命中,必须遵守)】\n" + "\n".join(lore_lines))

    open_fs = [f for f in (foreshadowing or []) if f.get("status") == "open"]
    if open_fs:
        lines = [
            f"- {f['title']}:{_clip(f.get('description'), 100)}"
            for f in open_fs[:8]
        ]
        parts.append("【待回收伏笔(可在合适节点呼应,勿强行填坑)】\n" + "\n".join(lines))

    # 读者认知状态:与角色已知、作者真相分离,防复述、管期待、控误导
    reader_lines = _reader_block(reader)
    if reader_lines:
        parts.append(reader_lines)

    return "\n\n".join(parts)


_READER_LABELS = {"known": "读者已知", "expect": "读者期待", "mislead": "读者误认"}


def _reader_block(reader):
    if not reader:
        return ""
    groups = {"known": [], "expect": [], "mislead": []}
    for r in reader:
        kind = r.get("kind") if r.get("kind") in groups else "known"
        if r.get("status") == "open" and (r.get("content") or "").strip():
            origin = f"(第{r['chapter_idx']}章起)" if r.get("chapter_idx") else ""
            groups[kind].append(f"- {_clip(r['content'], 90)}{origin}")
    lines = []
    if groups["known"]:
        lines.append("读者已知的事实(严禁当作悬念复述,可作共识直接引用):\n" + "\n".join(groups["known"][-8:]))
    if groups["expect"]:
        lines.append("读者正期待兑现的承诺(可延迟满足,不可无视遗忘):\n" + "\n".join(groups["expect"][-6:]))
    if groups["mislead"]:
        lines.append("读者被引导的误认(与作者真相不符;反转揭示前维持误导,勿提前泄底):\n" + "\n".join(groups["mislead"][-6:]))
    if not lines:
        return ""
    return "【读者认知状态(与角色已知/作者真相分离)】\n" + "\n".join(lines)
