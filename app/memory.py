"""三层记忆系统:为每章生成组装「结构化上下文」。

1. 静态层:世界观、文风、角色档案(故事圣经);另有 Lorebook:按关键词命中注入的设定条目;
2. 序列层:全书各章摘要(滚动压缩)+ 上一章结尾原文(口吻衔接);
3. 检索层:对旧章摘要做字符二元组 BM25 检索(或配置 embedding 档案后走向量余弦),
   把与本章最相关的旧情节拉回上下文;另附「待回收伏笔」清单,供正文呼应。

每次组装都按 settings.gen.context_chars 做显式预算分配,返回每层占用与被裁条目;
BM25 中文友好、零外部依赖;向量检索为可选增强,任何异常自动降级回 BM25。
"""
import math
import re
from collections import Counter

from app.db import status_asof  # 纯函数(角色状态按章时点回放),随存储层便于 MCP/测试复用


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
    return [i for _, i in topk_scored(query, docs, k)]


def topk_scored(query, docs, k=3, recency=None):
    """BM25 打分并返回 [(id, score)]。recency: {id: weight} 时近加权(MemGPT 注意力公式中的 recency 项)。"""
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
        if recency:
            s *= recency.get(docs[idx]["id"], 1.0)
        scores.append((s, idx))
    scores.sort(key=lambda x: -x[0])
    return [(docs[i]["id"], s) for s, i in scores[:k] if s > 0]


def _clip(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _clip_block(text, limit):
    """按预算截断长文本,尽量落在段落/句末,避免半个句子。返回 (文本, 是否截断)。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    if limit <= 1:
        return "", True
    cut = text[:limit - 1]
    for sep in ("\n", "。", "！", "？", "；", ".", "!", "?"):
        idx = cut.rfind(sep)
        if idx >= int((limit - 1) * 0.6):
            cut = cut[:idx + 1]
            break
    return cut.rstrip() + "…", True


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def hit_lore_items(lore, *texts, max_entries=8, entry_limit=400):
    """Lorebook 六字段子集匹配,返回结构化条目(便于预算裁剪与审计)。

    - constant=1 常驻条目不经命中直接注入;常驻条目即使命中关键词也只注入一次;
    - 主键(逗号分隔)命中其一即候选;
    - 次级键按 logic 二次过滤:and_any 命中任一 / and_all 全部命中 / not_any、not_all 均要求次级键不出现;
    - 按 priority 降序注入,预算不足时低优先级让位。
    """
    blob = "\n".join(t or "" for t in texts)
    if not blob.strip():
        return []
    entries = [e for e in (lore or []) if e.get("enabled")]
    hits = []
    for e in entries:
        if e.get("constant"):
            continue  # 常驻条目统一在下面优先注入,避免主键同时命中时重复
        kws = [k.strip() for k in re.split(r"[,，、;；\s]+", e.get("keywords") or "") if k.strip()]
        matched = [k for k in kws if k in blob]
        if not matched:
            continue
        sec = [k.strip() for k in re.split(r"[,，、;；\s]+", e.get("secondary") or "") if k.strip()]
        if sec:
            sec_hits = [k for k in sec if k in blob]
            logic = e.get("logic") or "and_any"
            if logic in ("and_any", "and_all"):
                ok = bool(sec_hits) if logic == "and_any" else len(sec_hits) == len(sec)
            else:  # not_any / not_all:次级键出现则不注入
                ok = not sec_hits
            if not ok:
                continue
        hits.append((int(e.get("priority") or 0), e, matched))
    constants = [(int(e.get("priority") or 0), e, ["常驻"]) for e in entries if e.get("constant")]
    combined = sorted(constants + hits, key=lambda x: -x[0])
    items = []
    for order, (priority, e, matched) in enumerate(combined[:max_entries]):
        label = "、".join(matched[:3]) or (e.get("note") or "常驻")
        items.append({
            "label": label,
            "text": f"- [{ '、'.join(matched[:3]) }] {_clip(e.get('content'), entry_limit)}",
            "priority": (1000 if e.get("constant") else 0) + priority * 10 + len(matched),
            "order": order,
            "entry_id": e.get("id"),
        })
    return items


def hit_lore(lore, *texts, max_entries=8, entry_limit=400):
    """兼容入口:返回纯文本行。"""
    return [x["text"] for x in hit_lore_items(
        lore, *texts, max_entries=max_entries, entry_limit=entry_limit
    )]


# 层权重之和为 1;优先级用于预算池回填(越大越先补回被裁条目)
_LAYER_SPECS = (
    ("worldview", "世界观", 0.16, 100, "【世界观与既定事实】", False),
    ("style", "文风", 0.03, 98, "【文风要求】", True),
    ("characters", "角色档案", 0.15, 95, "【主要角色档案】", False),
    ("summary", "前情脉络", 0.12, 90, "【前情脉络】", False),
    ("prev_tail", "上一章结尾", 0.11, 92, "【上一章结尾(衔接口吻,勿复述)】", False),
    ("retrieval", "相关旧情节", 0.07, 70, "【与本章相关的旧情节(避免重复、注意呼应)】", False),
    ("lore", "Lorebook", 0.18, 80, "【设定注入(Lorebook 关键词命中,必须遵守)】", False),
    ("foreshadow", "待回收伏笔", 0.06, 75, "【待回收伏笔(可在合适节点呼应,勿强行填坑)】", False),
    ("reader", "读者认知", 0.12, 72, "【读者认知状态(与角色已知/作者真相分离)】", False),
)

_READER_LABELS = {"known": "读者已知", "expect": "读者期待", "mislead": "读者误认"}
_READER_GROUP_HEADERS = {
    "known": "读者已知的事实(严禁当作悬念复述,可作共识直接引用):",
    "expect": "读者正期待兑现的承诺(可延迟满足,不可无视遗忘):",
    "mislead": "读者被引导的误认(与作者真相不符;反转揭示前维持误导,勿提前泄底):",
}


def _new_layer(spec):
    key, label, weight, priority, header, inline = spec
    return {
        "key": key, "label": label, "weight": weight, "priority": priority,
        "header": header, "inline": inline, "present": False,
        "raw": "", "items": None, "reader": False, "groups": [],
        "text": "", "used": 0, "kept": 0, "dropped": [],
        "truncated": False, "dropped_items": [], "kept_items": [],
    }


def _fit_items(items, limit):
    """按优先级装填条目;返回 (保留条目, 被裁条目, 正文长度)。保留后按原始顺序输出。"""
    if limit <= 0 or not items:
        return [], list(items or []), 0
    kept, dropped, used = [], [], 0
    for item in sorted(items, key=lambda x: -x["priority"]):
        cost = len(item["text"]) + (1 if kept else 0)
        if used + cost <= limit:
            kept.append(item)
            used += cost
        else:
            dropped.append(item)
    kept.sort(key=lambda x: x["order"])
    return kept, dropped, used


def _fit_layer(layer, quota):
    header = layer["header"]
    prefix = len(header) + (0 if layer["inline"] else 1)
    if layer["items"] is None:  # 单段文本(世界观/文风/上一章结尾)
        body_limit = max(0, quota - prefix)
        body, truncated = _clip_block(layer["raw"], body_limit)
        if not body:
            layer.update(text="", used=0, kept=0, dropped=[layer["label"]], truncated=truncated)
            return
        layer["text"] = header + body if layer["inline"] else header + "\n" + body
        layer.update(used=len(layer["text"]), kept=1, dropped=[], truncated=truncated)
        return
    fixed = prefix
    if layer["reader"]:
        fixed += sum(len(_READER_GROUP_HEADERS[g]) + 1 for g in layer["groups"])
    body_limit = max(0, quota - fixed)
    kept, dropped, _ = _fit_items(layer["items"], body_limit)
    layer["kept_items"] = kept
    layer["dropped_items"] = dropped
    if layer["reader"]:
        layer["text"] = _reader_text(kept, header)
    else:
        layer["text"] = (header + "\n" + "\n".join(i["text"] for i in kept)) if kept else ""
    layer.update(used=len(layer["text"]), kept=len(kept),
                 dropped=[i["label"] for i in dropped], truncated=False)


def _reader_text(items, outer_header):
    parts = []
    for kind in ("known", "expect", "mislead"):
        lines = [i["text"] for i in items if i.get("group") == kind]
        if lines:
            parts.append(_READER_GROUP_HEADERS[kind] + "\n" + "\n".join(lines))
    return (outer_header + "\n" + "\n".join(parts)) if parts else ""


def _reader_items(reader):
    groups = {"known": [], "expect": [], "mislead": []}
    for r in reader or []:
        kind = r.get("kind") if r.get("kind") in groups else "known"
        if r.get("status") == "open" and (r.get("content") or "").strip():
            origin = f"(第{r['chapter_idx']}章起)" if r.get("chapter_idx") else ""
            groups[kind].append(f"- {_clip(r['content'], 90)}{origin}")
    items, order = [], 0
    for kind in ("known", "expect", "mislead"):
        lines = groups[kind]
        for i, line in enumerate(lines):
            items.append({
                "label": f"{_READER_LABELS[kind]}:{_clip(line, 20)}",
                "text": line,
                "priority": 1000 - (len(lines) - i),
                "order": order,
                "group": kind,
            })
            order += 1
    return [k for k in ("known", "expect", "mislead") if groups[k]], items


def build_context_report(project, characters, chapters, foreshadowing, target_idx, settings,
                         lore=None, query_vec=None, doc_vectors=None, reader=None, char_states=None):
    """组装写第 target_idx 章所需上下文,同时返回预算报告。

    返回 (context_text, report)。report.layers 记录每层的 quota/used/kept/被裁条目。
    """
    gen = settings.get("gen", {}) or {}
    configured = int(gen.get("context_chars") or 14000)
    budget = max(1000, configured)
    # 预留层间分隔符,保证最终文本不超预算
    usable = max(200, budget - 2 * max(1, len(_LAYER_SPECS) - 1))
    summ_limit = 240

    layers = {spec[0]: _new_layer(spec) for spec in _LAYER_SPECS}

    worldview = _clip(project.get("worldview"), 2200)
    if worldview:
        layers["worldview"].update(present=True, raw=worldview)
    style = _clip(project.get("style"), 300)
    if style:
        layers["style"].update(present=True, raw=style)

    beats_txt = ""
    for ch in chapters:
        if ch["idx"] == target_idx:
            beats_txt = ch.get("beats") or ""
    query = " ".join(
        x for x in [project.get("premise", ""), project.get("genre", ""), beats_txt] if x
    )

    # 角色档案:数量少全给,数量多按相关性挑选;状态按「第 N 章时」回放(时间性事实)
    chars = list(characters)
    if len(chars) > 8 and query:
        docs = [{"id": c["name"], "text": f"{c['name']} {c['role']} {c['profile']}"} for c in chars]
        keep = [name for name, _ in topk_scored(query, docs, k=len(docs))][:8]
        if len(keep) < 8:  # query 与角色无词面重合时,按原始顺序补足,避免一个角色都不给
            for c in chars:
                if c["name"] not in keep:
                    keep.append(c["name"])
                    if len(keep) >= 8:
                        break
        keep_set = set(keep)
        chars = [c for c in chars if c["name"] in keep_set]
    char_items = []
    for i, c in enumerate(chars):
        line = f"- {c['name']}({c['role'] or '角色'}):{_clip(c['profile'], 280)}"
        db_status = status_asof(char_states, c["name"], target_idx, c.get("status"))
        if db_status:
            line += f"【第{target_idx}章时状态:{_clip(db_status, 80)}】"
        char_items.append({"label": c["name"], "text": line, "priority": 100 - i, "order": i})
    if char_items:
        layers["characters"].update(present=True, items=char_items)

    prev = [ch for ch in chapters if ch["idx"] < target_idx and (ch.get("summary") or ch.get("content"))]
    if prev:
        window = int(gen.get("summary_window", 6))
        recent = prev[-window:]
        older = prev[:-window]
        items, order = [], 0
        for ch in older:
            items.append({
                "label": f"第{ch['idx']}章",
                "text": f"第{ch['idx']}章《{ch.get('title', '')}》",
                "priority": 10, "order": order,
            })
            order += 1
        for ch in recent:
            s = ch.get("summary") or _clip(ch.get("content"), summ_limit)
            items.append({
                "label": f"第{ch['idx']}章",
                "text": f"第{ch['idx']}章《{ch.get('title', '')}》:{_clip(s, summ_limit)}",
                "priority": 100, "order": order,
            })
            order += 1
        layers["summary"].update(present=True, items=items)

    last = next((ch for ch in chapters if ch["idx"] == target_idx - 1), None)
    if last and last.get("content"):
        tail = _clip(last["content"][-1400:], 1400)
        layers["prev_tail"].update(present=True, raw=tail)

    # 检索层:旧章摘要 + 开头片段(向量可用则余弦相似,否则 BM25;带时近加权)
    if query and prev:
        docs = []
        for ch in prev:
            docs.append({
                "id": ch["idx"],
                "text": f"第{ch['idx']}章《{ch.get('title', '')}》 {(ch.get('summary') or '')} {(ch.get('content') or '')[:400]}",
            })
        topk = int(gen.get("retrieval_topk", 3))
        max_idx = max(d["id"] for d in docs) or 1
        recency = {d["id"]: 0.7 + 0.3 * (d["id"] / max_idx) for d in docs}
        dvecs = doc_vectors or {}
        if query_vec and dvecs:
            usable_docs = [d for d in docs if d["id"] in dvecs]
            scored = sorted(
                ((cosine(query_vec, dvecs[d["id"]]) * recency.get(d["id"], 1.0), d["id"])
                 for d in usable_docs), key=lambda x: -x[0]
            )
            selected = [(i, s) for s, i in scored[:topk] if s > 0.01]
        else:
            selected = topk_scored(query, docs, k=topk, recency=recency)
        ids = {i for i, _ in selected}
        rank = {i: pos for pos, (i, _) in enumerate(selected)}
        by_idx = {ch["idx"]: ch for ch in prev}
        items = []
        for order, d in enumerate(d for d in docs if d["id"] in ids and d["id"] != (last or {}).get("idx")):
            ch = by_idx.get(d["id"], {})
            frag = _clip((ch.get("summary") or (ch.get("content") or "")[:300]), 300)
            items.append({
                "label": f"第{ch.get('idx')}章",
                "text": f"第{ch.get('idx')}章《{ch.get('title', '')}》:{frag}",
                "priority": 1000 - rank.get(d["id"], 999),
                "order": order,
            })
        if items:
            layers["retrieval"].update(present=True, items=items)

    # Lorebook:关键词命中的设定条目(优先级高于检索,直接注入)
    lore_items = hit_lore_items(
        lore, beats_txt,
        *(ch.get("summary") or "" for ch in prev[-3:]),
        (last or {}).get("content", "")[-2000:],
        max_entries=32,
    )
    if lore_items:
        layers["lore"].update(present=True, items=lore_items)

    open_fs = [f for f in (foreshadowing or []) if f.get("status") == "open"]
    if open_fs:
        items = [{
            "label": f["title"],
            "text": f"- {f['title']}:{_clip(f.get('description'), 100)}",
            "priority": 1000 - i, "order": i,
        } for i, f in enumerate(open_fs)]
        layers["foreshadow"].update(present=True, items=items)

    # 读者认知状态:与角色已知、作者真相分离,防复述、管期待、控误导
    groups, reader_items = _reader_items(reader)
    if reader_items:
        layers["reader"].update(present=True, items=reader_items, reader=True, groups=groups)

    ordered = [layers[spec[0]] for spec in _LAYER_SPECS]
    for layer in ordered:
        layer["quota"] = int(usable * layer["weight"])
        if layer["present"]:
            _fit_layer(layer, layer["quota"])

    # 预算池:先把被截断的长文本补全,再按优先级补回被裁条目(只补给已有内容的层)
    remaining = max(0, usable - sum(l["used"] for l in ordered))
    for layer in sorted([l for l in ordered if l["present"] and l["items"] is None and l["truncated"]],
                        key=lambda x: -x["priority"]):
        if remaining <= 0:
            break
        prefix = len(layer["header"]) + (0 if layer["inline"] else 1)
        current_body = max(0, len(layer["text"]) - prefix)
        body, truncated = _clip_block(layer["raw"], current_body + remaining)
        text = layer["header"] + body if layer["inline"] else layer["header"] + "\n" + body
        gained = len(text) - layer["used"]
        if gained <= 0:
            continue
        layer.update(text=text, used=len(text), truncated=truncated)
        remaining -= gained

    dropped_pool = []
    for layer in ordered:
        if not layer["kept_items"]:
            continue
        for item in layer["dropped_items"]:
            dropped_pool.append((layer["priority"], item["priority"], layer, item))
    dropped_pool.sort(key=lambda x: (-x[0], -x[1]))
    for _, _, layer, item in dropped_pool:
        if remaining <= 0:
            break
        if layer["reader"]:
            same_group = any(i.get("group") == item.get("group") for i in layer["kept_items"])
            cost = len(item["text"]) + (1 if same_group else len(_READER_GROUP_HEADERS[item["group"]]) + 1)
        else:
            cost = len(item["text"]) + 1
        if cost <= remaining:
            layer["kept_items"].append(item)
            remaining -= cost

    for layer in ordered:
        if layer["items"] is None:
            continue
        kept = sorted(layer["kept_items"], key=lambda x: x["order"])
        layer["kept_items"] = kept
        if layer["reader"]:
            layer["text"] = _reader_text(kept, layer["header"])
        else:
            layer["text"] = (layer["header"] + "\n" + "\n".join(i["text"] for i in kept)) if kept else ""
        kept_ids = {id(i) for i in kept}
        layer.update(
            used=len(layer["text"]), kept=len(kept),
            dropped=[i["label"] for i in layer["dropped_items"] if id(i) not in kept_ids],
        )

    text = "\n\n".join(l["text"] for l in ordered if l["text"])
    if len(text) > budget:  # 防御:按优先级丢弃整层,保证不超预算
        for layer in sorted([l for l in ordered if l["text"]], key=lambda x: x["priority"]):
            layer.update(text="", used=0, kept=0)
            layer["dropped"].append(f"{layer['label']}(超出总预算)")
            text = "\n\n".join(l["text"] for l in ordered if l["text"])
            if len(text) <= budget:
                break

    report = {
        "configured": configured,
        "budget": budget,
        "used": len(text),
        "remaining": max(0, budget - len(text)),
        "dropped_total": sum(len(l["dropped"]) for l in ordered),
        "layers": [{
            "key": l["key"], "label": l["label"], "quota": l["quota"],
            "used": l["used"], "kept": l["kept"],
            "dropped_count": len(l["dropped"]), "dropped": l["dropped"][:10],
            "truncated": l["truncated"],
        } for l in ordered],
    }
    return text, report


def build_context(project, characters, chapters, foreshadowing, target_idx, settings,
                  lore=None, query_vec=None, doc_vectors=None, reader=None, char_states=None):
    """组装写第 target_idx 章时所需的上下文文本(兼容入口,不返回预算报告)。"""
    text, _ = build_context_report(
        project, characters, chapters, foreshadowing, target_idx, settings,
        lore=lore, query_vec=query_vec, doc_vectors=doc_vectors,
        reader=reader, char_states=char_states,
    )
    return text
