"""生成流水线编排:设定 → 角色 → 大纲 → 章纲 → 正文(含一致性门禁)→ 润色 → 审校 → 修订 → 定稿(摘要+状态)→ 批量连写。

所有函数均为同步阻塞实现,由路由层包成 SSE 流;on_delta 用于流式回传文本。
润色与修订只返回新稿、不落库,由前端确认后再保存(人为主编原则);
采用时自动对原稿做版本快照,可随时回滚。
"""
import json
import re

from app import db, prompts
from app.config import find_profile, load_settings, resolve_profile
from app.llm import chat, embed, extract_json
from app.memory import build_context_report
from app.lint import lint_spans, lint_text


def _load(pid):
    project = db.get_project(pid)
    if not project:
        raise ValueError("项目不存在")
    return project


def _chat(pid, stage, profile, messages, on_delta=None, json_mode=False):
    text = chat(profile, messages, json_mode=json_mode, on_delta=on_delta)
    db.add_usage(pid, stage, len(json.dumps(messages, ensure_ascii=False)), len(text))
    return text


# ---------- Lorebook 兼容引擎(ST World Info 导入导出) ----------

_LORE_LOGIC_TO_ST = {"and_any": 0, "not_all": 1, "not_any": 2, "and_all": 3}
_LORE_LOGIC_FROM_ST = {v: k for k, v in _LORE_LOGIC_TO_ST.items()}


def _split_keys(raw):
    if isinstance(raw, list):
        return ", ".join(str(k).strip() for k in raw if str(k).strip())
    return str(raw or "").strip()


def export_lore(pid):
    """导出为 SillyTavern World Info JSON(可直接导入 ST / NovelAI 生态)。"""
    _load(pid)
    entries = {}
    for i, r in enumerate(db.list_lore(pid)):
        secondary = (r.get("secondary") or "").strip()
        entries[str(i)] = {
            "uid": i,
            "key": [k.strip() for k in re.split(r"[,，、;；\s]+", r["keywords"] or "") if k.strip()],
            "keysecondary": [k.strip() for k in re.split(r"[,，、;；\s]+", secondary) if k.strip()],
            "comment": r.get("note") or "",
            "content": r["content"],
            "constant": bool(r.get("constant")),
            "selective": bool(secondary),
            "selectiveLogic": _LORE_LOGIC_TO_ST.get(r.get("logic") or "and_any", 0),
            "order": int(r.get("priority") or 0) + 100,
            "position": 0,
            "disable": not r.get("enabled"),
            "addMemo": True,
            "displayIndex": i,
        }
    return {"name": "castnovel-lorebook", "entries": entries}


def import_lore(pid, data):
    """导入 SillyTavern World Info JSON(merge,不清空现有条目),返回导入条数。

    兼容 ST 完整格式({entries:{...}})、本项目导出格式与简表 [{key,content,...}]。
    常驻条目(constant)允许无主键;格式非法时抛 ValueError。
    """
    _load(pid)
    raw = []
    if isinstance(data, dict) and isinstance(data.get("entries"), dict):
        raw = list(data["entries"].values())
    elif isinstance(data, list):
        raw = data
    elif isinstance(data, dict) and data.get("content"):
        raw = [data]
    if not raw:
        raise ValueError("未识别的格式:需要 SillyTavern World Info JSON(entries 字段)或条目数组")
    n = 0
    for e in raw:
        if not isinstance(e, dict):
            continue
        content = str(e.get("content") or "").strip()
        keys = _split_keys(e.get("key") or e.get("keys") or e.get("keywords"))
        constant = 1 if e.get("constant") else 0
        if not content or (not keys and not constant):
            continue
        logic = e.get("logic")
        if logic not in _LORE_LOGIC_TO_ST:
            try:
                sel = int(e.get("selectiveLogic") or 0)
            except (TypeError, ValueError):
                sel = 0
            logic = _LORE_LOGIC_FROM_ST.get(sel, "and_any")
        try:
            priority = max(0, min(999, int(e.get("order")) - 100)) if e.get("order") is not None else 0
        except (TypeError, ValueError):
            priority = 0
        db.add_lore(
            pid, keys, content, _split_keys(e.get("comment") or e.get("note")),
            secondary=_split_keys(e.get("keysecondary") or e.get("secondary")),
            logic=logic, constant=constant, priority=priority,
            enabled=0 if e.get("disable") else 1,
        )
        n += 1
    if not n:
        raise ValueError("导入文件里没有可用条目(缺少 content,或既无 key 也非 constant)")
    return n


# ---------- 程序化一致性门禁(不依赖模型自觉,借鉴 wfcz10086/天命) ----------

def _ngrams(text, n=8):
    s = re.sub(r"\s+", "", text or "")
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))} if len(s) >= n else set()


def run_gate(settings, project, chapters, characters, chapter):
    content = chapter.get("content") or ""
    words = len(re.sub(r"\s", "", content))
    target = int(settings.get("gen", {}).get("chapter_words", 3000))
    issues = []
    if not content.strip():
        issues.append({"type": "字数", "level": "warn", "msg": "本章正文为空"})
    elif words < target * 0.5:
        issues.append({"type": "字数", "level": "warn", "msg": f"正文 {words} 字,不足目标 {target} 字的一半"})
    elif words < target * 0.8:
        issues.append({"type": "字数", "level": "info", "msg": f"正文 {words} 字,略低于目标 {target} 字"})

    names = [c["name"] for c in (characters or []) if c.get("name")]
    hits = [n for n in names if n in content]
    if names and not hits:
        issues.append({"type": "人物", "level": "warn", "msg": "全文未提及任何既有关键角色,疑似跑题或人名不一致"})

    cur = _ngrams(content)
    worst = 0.0
    prevs = [ch for ch in (chapters or []) if ch["idx"] < chapter["idx"] and (ch.get("content") or "").strip()][-3:]
    for ch in prevs:
        other = _ngrams(ch["content"])
        union = cur | other
        if union:
            worst = max(worst, len(cur & other) / len(union))
    if worst > 0.12:
        issues.append({"type": "重复", "level": "warn", "msg": f"与近三章的最大文本重复率约 {worst:.0%},疑似复述旧情节"})

    # 第 2.5 道:确定性去 AI 味 lint(零 token,规则可扩展)
    for li in lint_text(content):
        issues.append({"type": "AI味", "level": li["level"], "msg": f"{li['msg']}(出现 {li['count']} 次)。改写方向:{li['suggestion']}"})

    return {
        "words": words,
        "target": target,
        "char_hits": hits,
        "char_missed": [n for n in names if n not in hits],
        "overlap": round(worst, 3),
        "issues": issues,
    }


def _ctx_bundle(pid, target_idx, settings):
    project = _load(pid)
    characters = db.list_characters(pid)
    chapters = db.list_chapters(pid)
    fores = db.list_foreshadowing(pid)
    lore = db.list_lore(pid, only_enabled=True)
    reader = db.list_reader(pid)
    char_states = db.list_character_states(pid)
    qvec, dvecs = _vector_aux(pid, chapters, target_idx, settings)
    context, report = build_context_report(
        project, characters, chapters, fores, target_idx, settings,
        lore=lore, query_vec=qvec, doc_vectors=dvecs, reader=reader,
        char_states=char_states,
    )
    return {
        "project": project, "characters": characters, "chapters": chapters,
        "context": context, "report": report,
    }


def _ctx_parts(pid, target_idx, settings):
    bundle = _ctx_bundle(pid, target_idx, settings)
    return bundle["project"], bundle["characters"], bundle["chapters"], bundle["context"]


def _vector_aux(pid, chapters, target_idx, settings):
    """可选向量语义检索:返回 (查询向量, {旧章idx: 向量});未配置或任何异常 → (None, None) 降级 BM25。"""
    name = settings.get("embedding_profile") or ""
    if not name:
        return None, None
    prof = find_profile(settings, name)
    if not prof:
        return None, None
    tag = f"{prof.get('base_url', '')}#{prof.get('embed_model') or prof.get('model', '')}"
    try:
        prevs = [ch for ch in chapters if ch["idx"] < target_idx and (ch.get("summary") or ch.get("content"))]
        cache = db.list_embeddings(pid, tag)
        missing = [ch for ch in prevs if ch["idx"] not in cache]
        if missing:
            texts = [(ch.get("summary") or (ch.get("content") or "")[:600]) for ch in missing]
            for ch, vec in zip(missing, embed(prof, texts)):
                db.save_embedding(pid, ch["idx"], tag, vec)
                cache[ch["idx"]] = vec
        target = next((ch for ch in chapters if ch["idx"] == target_idx), {})
        qtext = " ".join(x for x in [target.get("beats") or "", target.get("summary") or ""] if x)
        qvec = embed(prof, [qtext or " "])[0] if qtext else None
        return qvec, cache
    except Exception:
        return None, None  # embedding 服务不可用:静默降级 BM25,不阻塞写作主流程


def gen_worldview(pid, on_delta=None):
    settings = load_settings(db)
    project = _load(pid)
    text = _chat(pid, "worldview", resolve_profile(settings, "strong"),
                 prompts.worldview_messages(project), on_delta=on_delta)
    db.update_project(pid, {"worldview": text})
    return text


def gen_characters(pid, on_delta=None):
    settings = load_settings(db)
    project = _load(pid)
    if not project.get("worldview"):
        raise ValueError("请先生成或手写世界观设定,再生成角色。")
    text = _chat(pid, "characters", resolve_profile(settings, "strong"),
                 prompts.characters_messages(project), json_mode=True, on_delta=on_delta)
    data = extract_json(text)
    chars = data.get("characters") if isinstance(data, dict) else data
    if not isinstance(chars, list) or not chars:
        raise ValueError("模型未返回角色列表,请重试。")
    db.replace_characters(pid, chars)
    return chars


def gen_outline(pid, count=30, replace=False, on_delta=None):
    settings = load_settings(db)
    project = _load(pid)
    if not project.get("worldview"):
        raise ValueError("请先生成或手写世界观设定,再做全书大纲。")
    existing = db.list_chapters(pid)
    if existing and not replace:
        raise ValueError("已存在章节大纲。如需重新生成并覆盖,请勾选「覆盖现有章节」。")
    if existing and replace:
        has_content = any((ch.get("content") or "").strip() for ch in existing)
        if has_content:
            raise ValueError("已有章节写有正文,覆盖会丢失内容,请先导出备份再确认覆盖。")
    chars = db.list_characters(pid)
    brief = "\n".join(f"- {c['name']}({c['role']}):{(c['profile'] or '')[:60]}" for c in chars) or "(暂无)"
    project = dict(project)
    project["_characters_brief"] = brief
    text = _chat(pid, "outline", resolve_profile(settings, "strong"),
                 prompts.outline_messages(project, count), json_mode=True, on_delta=on_delta)
    data = extract_json(text)
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("模型未返回章节大纲,请重试。")
    norm = []
    for i, ch in enumerate(chapters, start=1):
        idx = ch.get("idx")
        idx = i if not isinstance(idx, int) or idx < 1 else idx
        summary = str(ch.get("summary", "")).strip()
        hook = str(ch.get("hook", "")).strip()
        beats = f"章末钩子:{hook}" if hook else ""
        norm.append({"idx": idx, "title": str(ch.get("title", f"第{idx}章")).strip() or f"第{idx}章",
                     "summary": summary, "beats": beats})
    db.replace_chapters(pid, norm)
    volumes = data.get("volumes") if isinstance(data, dict) else None
    if isinstance(volumes, list) and volumes:
        db.update_project(pid, {"volumes": json.dumps(volumes, ensure_ascii=False)})
    return norm


def gen_beats(pid, cid, on_delta=None):
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter:
        raise ValueError("章节不存在")
    project, _, chapters, context = _ctx_parts(pid, chapter["idx"], settings)
    total = len(chapters)
    text = _chat(pid, "beats", resolve_profile(settings, "strong"),
                 prompts.beats_messages(project, chapter, context, total),
                 json_mode=True, on_delta=on_delta)
    data = extract_json(text)
    beats_list = data.get("beats") if isinstance(data, dict) else data
    if not isinstance(beats_list, list) or not beats_list:
        raise ValueError("模型未返回节拍,请重试。")
    lines = [f"{i+1}. {str(b).strip()}" for i, b in enumerate(beats_list) if str(b).strip()]
    old = chapter.get("beats") or ""
    hook = ""
    for ln in old.splitlines():
        if ln.strip().startswith("章末钩子"):
            hook = ln.strip()
    if hook:
        lines.append(hook)
    beats_text = "\n".join(lines)
    db.update_chapter(cid, {"beats": beats_text, "status": "planned"})
    db.touch_project(pid)
    return beats_text


def gen_draft(pid, cid, on_delta=None):
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter:
        raise ValueError("章节不存在")
    if (chapter.get("content") or "").strip():
        db.add_version(cid, f"第{chapter['idx']}章重新生成前自动保存")
    project, characters, chapters, context = _ctx_parts(pid, chapter["idx"], settings)
    words = int(settings.get("gen", {}).get("chapter_words", 3000))
    beats_text = chapter.get("beats") or ""
    beats = [ln.strip() for ln in beats_text.splitlines() if ln.strip() and not ln.strip().startswith("章末钩子")]
    messages = prompts.draft_messages(project, chapter, beats, context, words)
    db.update_chapter(cid, {"status": "drafting"})
    text = _chat(pid, "draft", resolve_profile(settings, "strong"), messages, on_delta=on_delta)
    gate = run_gate(settings, project, chapters, characters, {**chapter, "content": text})
    db.update_chapter(cid, {"content": text, "status": "draft", "gate": json.dumps(gate, ensure_ascii=False)})
    db.touch_project(pid)
    db.add_log(pid, "draft", f"第{chapter['idx']}章 {len(text)}字")
    return {**db.get_chapter(cid), "_gate": gate}


def gen_draft_best(pid, cid, n=2, on_delta=None):
    """候选拉样+重排(Re3 的 Sample-Rank 思路):拉 n 份候选 → 门禁打分 → 强模型主编重排 → 择优采用。

    落选候选全部存入版本历史(非正典分支,可随时回滚取用),正典候选写入章节。
    """
    def say(msg):
        if on_delta:
            on_delta(msg)

    n = max(2, min(int(n or 2), 4))
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter:
        raise ValueError("章节不存在")
    if (chapter.get("content") or "").strip():
        db.add_version(cid, f"第{chapter['idx']}章候选重排前自动保存")
    project, characters, chapters, context = _ctx_parts(pid, chapter["idx"], settings)
    words = int(settings.get("gen", {}).get("chapter_words", 3000))
    beats = [ln.strip() for ln in (chapter.get("beats") or "").splitlines()
             if ln.strip() and not ln.strip().startswith("章末钩子")]
    messages = prompts.draft_messages(project, chapter, beats, context, words)
    profile = resolve_profile(settings, "strong")
    cands = []
    for lb in ["甲", "乙", "丙", "丁"][:n]:
        say(f"[候选{lb}] 生成中…\n")
        text = _chat(pid, "draft", profile, messages)
        gate = run_gate(settings, project, chapters, characters, {**chapter, "content": text})
        warns = sum(1 for it in gate["issues"] if it.get("level") in ("warn", "advisory"))
        cands.append({"label": lb, "text": text, "gate": gate, "warns": warns})
        say(f"[候选{lb}] 完成 {len(text)} 字,门禁告警 {warns} 项\n")
    say("[重排] 主编评审候选中…\n")
    try:
        rank = extract_json(_chat(pid, "rank", profile,
                                  prompts.rank_messages(project, chapter, cands, words), json_mode=True))
    except Exception:
        rank = {}
    by_label = {c["label"]: c for c in cands}
    best_label = rank.get("best")
    if best_label not in by_label:  # 模型没按规矩回答:门禁告警最少者优先
        best_label = min(cands, key=lambda c: c["warns"])["label"]
    best = by_label[best_label]
    scores = rank.get("scores") if isinstance(rank.get("scores"), dict) else {}
    for c in cands:
        if c["label"] == best_label:
            continue
        score = scores.get(c["label"])
        db.add_version_text(cid, c["text"], f"落选候选{c['label']}" + (f"(评分{score})" if score is not None else ""))
    gate = run_gate(settings, project, chapters, characters, {**chapter, "content": best["text"]})
    db.update_chapter(cid, {"content": best["text"], "status": "draft",
                            "gate": json.dumps(gate, ensure_ascii=False)})
    db.touch_project(pid)
    db.add_log(pid, "draft_best", f"第{chapter['idx']}章 {len(cands)} 选 1 → 候选{best_label}({len(best['text'])}字)")
    return {**db.get_chapter(cid), "_gate": gate,
            "_rank": {"best": best_label, "scores": scores, "reason": str(rank.get("reason", "")),
                      "labels": [c["label"] for c in cands]}}


def deai_rewrite(pid, cid, on_delta=None):
    """两步去 AI 味管线:第一步程序化 span 级检测(零 token),第二步模型按定位清单带例改写。

    返回新稿不落库,由前端预览确认后采用(与润色同样的人为主编原则)。
    """
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter or not (chapter.get("content") or "").strip():
        raise ValueError("本章还没有正文,请先生成或粘贴正文。")
    spans = lint_spans(chapter["content"])
    if not spans:
        return {"text": chapter["content"], "spans": [], "note": "程序化检测未发现 AI 味命中,无需改写。"}
    project = _load(pid)
    if on_delta:
        on_delta(f"[检测] 命中 {len(spans)} 处,已逐条定位,模型改写中…\n")
    text = _chat(pid, "deai", resolve_profile(settings, "strong"),
                 prompts.deai_messages(project, chapter, chapter["content"], spans), on_delta=on_delta)
    remain = len(lint_spans(text))
    return {"text": text, "spans": spans,
            "note": f"改写前命中 {len(spans)} 处,改写后复检剩余 {remain} 处。"}


def find_impact(pid, query):
    """设定变更影响分析(天命式反查):按关键词确定性扫描,列出受影响的章节。零 token。"""
    kws = [k.strip() for k in re.split(r"[,，、;；\s]+", query or "") if k.strip()]
    if not kws:
        raise ValueError("请提供要反查的关键词(角色名、地点、设定词,逗号分隔)")
    rows = []
    for ch in db.list_chapters(pid):
        fields = {"title": ch.get("title") or "", "summary": ch.get("summary") or "",
                  "beats": ch.get("beats") or "", "content": ch.get("content") or ""}
        counts = {f: sum(fields[f].count(k) for k in kws) for f in fields}
        total = sum(counts.values())
        if total:
            rows.append({"idx": ch["idx"], "id": ch["id"], "title": ch.get("title", ""),
                         "status": ch.get("status", ""), "hits": total,
                         "n_title": counts["title"], "n_summary": counts["summary"],
                         "n_beats": counts["beats"], "n_content": counts["content"]})
    chars = [c["name"] for c in db.list_characters(pid) if any(k in c["name"] or c["name"] in k for k in kws)]
    return {"keywords": kws, "characters_matched": chars, "affected": rows}


def cascade_regenerate(pid, chapter_ids, include_written=False, on_event=None):
    """级联重生成:对选定章节依次 章纲 → 正文 → 定稿;已有正文的章自动快照,不丢稿。

    典型用途:改了某章章纲或某项设定后,把反查出的受影响下游章节勾选进来批量重做。
    """
    def ev(data):
        if on_event:
            on_event(data)

    ids = sorted({int(c) for c in (chapter_ids or [])})
    if not ids:
        raise ValueError("未选择任何要重做的章节")
    done, fails = 0, 0
    for cid in ids:
        ch = db.get_chapter(cid)
        if not ch or ch["project_id"] != pid:
            ev({"event": "chapter_error", "idx": cid, "message": "章节不存在"})
            continue
        ev({"event": "chapter_start", "idx": ch["idx"], "title": ch.get("title") or ""})
        try:
            if (ch.get("content") or "").strip() and not include_written:
                ev({"event": "chapter_skipped", "idx": ch["idx"], "message": "已有正文,未勾选「包含已写章节」"})
                continue
            gen_beats(pid, cid)
            gen_draft(pid, cid)
            finalize_chapter(pid, cid)
            done += 1
            fails = 0
            ev({"event": "chapter_done", "idx": ch["idx"], "cid": cid})
        except Exception as e:
            fails += 1
            ev({"event": "chapter_error", "idx": ch["idx"], "message": str(e)})
            if fails >= 2:
                ev({"event": "aborted", "done": done, "message": "连续 2 章失败,级联任务中止(避免空烧 API)"})
                break
    ev({"event": "batch_done", "done": done, "total": len(ids)})
    return {"done": done, "total": len(ids)}


def recompute_gate(pid, cid):
    """对章节当前正文重跑一致性门禁并保存(零 token)。手工编辑/采用新稿后调用,保持徽章新鲜。"""
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter or not (chapter.get("content") or "").strip():
        raise ValueError("本章无正文,无需门禁")
    project = db.get_project(pid)
    characters = db.list_characters(pid)
    chapters = db.list_chapters(pid)
    gate = run_gate(settings, project, chapters, characters, chapter)
    db.update_chapter(cid, {"gate": json.dumps(gate, ensure_ascii=False)})
    return gate


def adopt_content(pid, cid, content, note=""):
    """采用新稿(润色/修订/回滚确认):旧稿先做版本快照,随后自动重跑门禁。"""
    chapter = db.get_chapter(cid)
    if not chapter:
        raise ValueError("章节不存在")
    if (chapter.get("content") or "").strip() and content != chapter["content"]:
        db.add_version(cid, note or "采用新稿前自动保存")
    db.update_chapter(cid, {"content": content})
    db.touch_project(pid)
    try:
        recompute_gate(pid, cid)
    except Exception:
        pass  # 门禁失败不阻塞采用(如空稿)
    return db.get_chapter(cid)


def polish_chapter(pid, cid, on_delta=None):
    """润色不直接覆盖:返回新稿供前端预览确认。"""
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter or not (chapter.get("content") or "").strip():
        raise ValueError("本章还没有正文,请先生成或粘贴正文。")
    project = _load(pid)
    return _chat(pid, "polish", resolve_profile(settings, "strong"),
                 prompts.polish_messages(project, chapter, chapter["content"]), on_delta=on_delta)


def review_chapter(pid, cid):
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter or not (chapter.get("content") or "").strip():
        raise ValueError("本章还没有正文,无法审校。")
    project, _, _, context = _ctx_parts(pid, chapter["idx"], settings)
    project = dict(project)
    project["_words"] = int(settings.get("gen", {}).get("chapter_words", 3000))
    text = _chat(pid, "review", resolve_profile(settings, "strong"),
                 prompts.review_messages(project, chapter, context, chapter["content"]),
                 json_mode=True)
    review = extract_json(text)
    db.update_chapter(cid, {"review": json.dumps(review, ensure_ascii=False), "status": "reviewed"})
    db.add_log(pid, "review", f"第{chapter['idx']}章 得分 {review.get('overall')}")
    return review


def revise_chapter(pid, cid, on_delta=None):
    """按审校意见修订,返回新稿不落库,由前端确认采用。"""
    settings = load_settings(db)
    chapter = db.get_chapter(cid)
    if not chapter or not (chapter.get("content") or "").strip():
        raise ValueError("本章还没有正文。")
    if not (chapter.get("review") or "").strip():
        raise ValueError("请先运行审校,再按意见修订。")
    project = _load(pid)
    text = _chat(pid, "revise", resolve_profile(settings, "strong"),
                 prompts.revise_messages(project, chapter, chapter["content"], chapter["review"]),
                 on_delta=on_delta)
    db.add_log(pid, "revise", f"第{chapter['idx']}章 修订稿 {len(text)}字")
    return text


_STATE_ACTION_ALIASES = {
    "foreshadowing": {
        "plant": "plant", "planted": "plant", "埋设": "plant", "埋下": "plant",
        "resolve": "resolve", "resolved": "resolve", "payoff": "resolve", "回收": "resolve", "兑现": "resolve",
    },
    "plotlines": {
        "start": "start", "begin": "start", "开启": "start",
        "advance": "advance", "advancing": "advance", "progress": "advance", "推进": "advance",
        "end": "end", "finish": "end", "conclude": "end", "结束": "end", "收束": "end",
    },
    "reader_state": {
        "add": "add", "new": "add", "新增": "add",
        "resolve": "resolve", "resolved": "resolve", "done": "resolve", "close": "resolve",
        "结算": "resolve", "兑现": "resolve", "揭穿": "resolve",
    },
}
_READER_KIND_ALIASES = {
    "known": "known", "已知": "known",
    "expect": "expect", "期待": "expect",
    "mislead": "mislead", "误认": "mislead", "误导": "mislead",
}
_DEAD_MARKERS = ("已死", "身亡", "阵亡", "殒命", "死亡", "断气", "气绝", "死了")
_REVIVAL_MARKERS = ("复活", "重生", "诈死", "假死", "未死", "没死", "救活", "苏醒", "醒来", "死而复生", "死里逃生", "逃过一死")


def _norm_action(raw, mapping, default):
    s = str(raw or "").strip().lower()
    if not s:
        return default
    return mapping.get(s, s)


def _clip_state(value, limit):
    s = str(value or "").strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _status_at_or_before(states, name, idx, fallback=""):
    evs = [
        e for e in (states or [])
        if e.get("name") == name and int(e.get("chapter_idx") or 0) <= idx
    ]
    if not evs:
        return fallback
    return max(evs, key=lambda e: (int(e["chapter_idx"]), int(e.get("id") or 0))).get("status") or fallback


def _is_dead_status(status):
    s = str(status or "")
    if any(m in s for m in _REVIVAL_MARKERS):
        return False
    return any(m in s for m in _DEAD_MARKERS)


def _validate_state(state, characters, foreshadowing, plotlines, reader, chapter_idx, chapters, char_states):
    """定稿状态确定性校验:normalize 动作别名、去重、检查角色/章节/schema/状态回退。

    结构性或高风险错误直接抛 ValueError(调用方不落库);可修复问题写入 validation.fixes。
    """
    errors, warnings, fixes = [], [], []
    if not isinstance(state, dict):
        raise ValueError("定稿状态校验失败:模型返回的不是 JSON 对象")
    if not isinstance(chapter_idx, int) or chapter_idx < 1:
        errors.append(f"章节号非法:{chapter_idx}")
    max_idx = max((int(ch.get("idx") or 0) for ch in chapters), default=0)
    if max_idx and isinstance(chapter_idx, int) and chapter_idx > max_idx:
        errors.append(f"章节号 {chapter_idx} 超出当前最大章节 {max_idx}")

    def section(key):
        val = state.get(key, [])
        if val is None:
            return []
        if not isinstance(val, list):
            errors.append(f"{key} 必须是数组")
            return []
        return val

    known = {c["name"] for c in characters if c.get("name")}
    current = {c["name"]: c for c in characters if c.get("name")}

    char_out, seen_char = [], {}
    for i, c in enumerate(section("characters")):
        if not isinstance(c, dict):
            errors.append(f"characters[{i}] 必须是对象")
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            errors.append(f"characters[{i}] 缺少 name")
            continue
        if len(name) > 30 or "\n" in name or "\r" in name:
            errors.append(f"角色名「{name[:30]}」非法(过长或含换行)")
            continue
        role = _clip_state(c.get("role"), 20)
        profile = _clip_state(c.get("profile"), 240)
        status = _clip_state(c.get("status"), 120)
        if len(str(c.get("profile") or "")) > 240:
            fixes.append(f"角色「{name}」profile 超长,已截断")
        if len(str(c.get("status") or "")) > 120:
            fixes.append(f"角色「{name}」status 超长,已截断")
        if name not in known and not profile:
            profile = role or "新出场角色"
            fixes.append(f"新角色「{name}」缺少 profile,已补默认值")
        if name in seen_char:
            prev = seen_char[name]
            prev["role"] = role or prev["role"]
            prev["profile"] = profile or prev["profile"]
            prev["status"] = status or prev["status"]
            fixes.append(f"角色「{name}」重复,已合并")
            continue
        item = {"name": name, "role": role, "profile": profile, "status": status}
        seen_char[name] = item
        char_out.append(item)

    for c in char_out:
        name, status = c["name"], c["status"]
        if not status:
            continue
        prior = _status_at_or_before(char_states, name, chapter_idx, (current.get(name) or {}).get("status", ""))
        if _is_dead_status(prior) and not _is_dead_status(status) and not any(m in status for m in _REVIVAL_MARKERS):
            errors.append(
                f"角色「{name}」状态从「{_clip_state(prior, 40)}」回退为「{_clip_state(status, 40)}」;"
                "若为复活/诈死,请在 status 中写明"
            )
        latest = max(
            (int(s.get("chapter_idx") or 0) for s in char_states if s.get("name") == name),
            default=None,
        )
        if latest is not None and chapter_idx < latest:
            warnings.append(
                f"角色「{name}」已有第{latest}章状态;本次重定稿第{chapter_idx}章只追加时间线,不会回退当前档案状态"
            )

    fores_out, seen_fores = [], {}
    all_titles = {f["title"] for f in foreshadowing}
    for i, f in enumerate(section("foreshadowing")):
        if not isinstance(f, dict):
            errors.append(f"foreshadowing[{i}] 必须是对象")
            continue
        title = str(f.get("title") or "").strip()
        if not title:
            errors.append(f"foreshadowing[{i}] 缺少 title")
            continue
        if len(title) > 30:
            errors.append(f"伏笔标题「{title[:30]}」过长")
            continue
        action = _norm_action(f.get("action"), _STATE_ACTION_ALIASES["foreshadowing"], "plant")
        if action not in ("plant", "resolve"):
            errors.append(f"伏笔「{title}」action 非法:{f.get('action')}")
            continue
        desc = _clip_state(f.get("description"), 200)
        if len(str(f.get("description") or "")) > 200:
            fixes.append(f"伏笔「{title}」description 超长,已截断")
        if action == "resolve" and title not in all_titles:
            warnings.append(f"伏笔「{title}」resolve 未匹配已有条目,将被跳过")
        if title in seen_fores:
            prev = seen_fores[title]
            if action == "resolve":
                prev["action"] = "resolve"
            prev["description"] = desc or prev["description"]
            fixes.append(f"伏笔「{title}」重复,已合并")
            continue
        item = {"title": title, "description": desc, "action": action}
        seen_fores[title] = item
        fores_out.append(item)

    plot_out, seen_plot = [], {}
    plot_titles = {p["title"] for p in plotlines}
    for i, p in enumerate(section("plotlines")):
        if not isinstance(p, dict):
            errors.append(f"plotlines[{i}] 必须是对象")
            continue
        title = str(p.get("title") or "").strip()
        if not title:
            errors.append(f"plotlines[{i}] 缺少 title")
            continue
        if len(title) > 30:
            errors.append(f"情节线标题「{title[:30]}」过长")
            continue
        action = _norm_action(p.get("action"), _STATE_ACTION_ALIASES["plotlines"], "start")
        if action not in ("start", "advance", "end"):
            errors.append(f"情节线「{title}」action 非法:{p.get('action')}")
            continue
        desc = _clip_state(p.get("description"), 200)
        if len(str(p.get("description") or "")) > 200:
            fixes.append(f"情节线「{title}」description 超长,已截断")
        if action in ("advance", "end") and title not in plot_titles:
            warnings.append(
                f"情节线「{title}」{action} 未匹配已有条目"
                + ("，将被跳过" if action == "end" else "，将新建")
            )
        if title in seen_plot:
            prev = seen_plot[title]
            if {"start": 1, "advance": 2, "end": 3}[action] >= {"start": 1, "advance": 2, "end": 3}[prev["action"]]:
                prev["action"] = action
            prev["description"] = desc or prev["description"]
            fixes.append(f"情节线「{title}」重复,已合并")
            continue
        item = {"title": title, "description": desc, "action": action}
        seen_plot[title] = item
        plot_out.append(item)

    reader_out, seen_reader = [], {}
    open_reader = [(r.get("kind"), r.get("content") or "") for r in reader if r.get("status") == "open"]
    for i, r in enumerate(section("reader_state")):
        if not isinstance(r, dict):
            errors.append(f"reader_state[{i}] 必须是对象")
            continue
        kind_raw = str(r.get("kind") or "known").strip().lower()
        kind = _READER_KIND_ALIASES.get(kind_raw, kind_raw)
        if kind not in ("known", "expect", "mislead"):
            errors.append(f"reader_state[{i}] kind 非法:{r.get('kind')}")
            continue
        content = _clip_state(r.get("content"), 120)
        if not content:
            warnings.append(f"reader_state[{i}] 内容为空,已跳过")
            continue
        if len(str(r.get("content") or "")) > 120:
            fixes.append("reader_state 内容超长,已截断")
        action = _norm_action(r.get("action"), _STATE_ACTION_ALIASES["reader_state"], "add")
        if action not in ("add", "resolve"):
            errors.append(f"reader_state[{i}] action 非法:{r.get('action')}")
            continue
        if action == "resolve" and not any(
            k == kind and (content in c or c in content) for k, c in open_reader
        ):
            warnings.append(f"读者认知「{content}」resolve 未匹配未结条目,将被跳过")
        key = (kind, content)
        if key in seen_reader:
            if action == "resolve":
                seen_reader[key]["action"] = "resolve"
            fixes.append("reader_state 重复条目,已合并")
            continue
        item = {"kind": kind, "content": content, "action": action}
        seen_reader[key] = item
        reader_out.append(item)

    if errors:
        more = " 等" if len(errors) > 8 else ""
        raise ValueError("定稿状态校验失败:" + "; ".join(errors[:8]) + more)

    clean = {
        "characters": char_out,
        "foreshadowing": fores_out,
        "plotlines": plot_out,
        "reader_state": reader_out,
    }
    validation = {
        "errors": [],
        "warnings": warnings[:50],
        "fixes": fixes[:50],
        "counts": {k: len(v) for k, v in clean.items()},
    }
    return clean, validation


def _snap_character(row):
    return {k: row.get(k) for k in ("name", "role", "profile", "status")} if row else None


def _snap_character_state(row):
    return {k: row.get(k) for k in ("name", "chapter_idx", "status")} if row else None


def _snap_foreshadow(row):
    return {k: row.get(k) for k in ("title", "description", "planted_ch", "payoff_ch", "status")} if row else None


def _snap_plotline(row):
    return {k: row.get(k) for k in ("title", "description", "start_ch", "last_ch", "status")} if row else None


def _snap_reader(row):
    return {k: row.get(k) for k in ("kind", "content", "chapter_idx", "status")} if row else None


def _snap_chapter(row):
    return {k: row.get(k) for k in ("idx", "title", "status", "summary")} if row else None


def _log_memory_result(pid, idx, entity, result, snap):
    if not result or result.get("action") in (None, "noop"):
        return
    action = result["action"]
    after = snap(result.get("after"))
    if action == "skipped":
        after = {"reason": result.get("reason")}
    db.add_memory_event(
        pid, idx, entity, action,
        before=snap(result.get("before")), after=after,
        status="skipped" if action == "skipped" else "applied",
        entity_id=result.get("id"),
    )


def finalize_chapter(pid, cid):
    """定稿:快模型生成摘要 + 抽取状态 JSON → 确定性校验 → 单事务回写故事圣经。

    校验失败不落库;任何回写异常整体回滚;所有变更写入 memory_events(before/after)。
    """
    settings = load_settings(db)
    profile = resolve_profile(settings, "fast")
    chapter = db.get_chapter(cid)
    if not chapter or not (chapter.get("content") or "").strip():
        raise ValueError("本章还没有正文,无法定稿。")
    project = _load(pid)
    idx = chapter["idx"]
    chapters = db.list_chapters(pid)
    prev_chapters = [ch for ch in chapters if ch["idx"] < idx]

    chars = db.list_characters(pid)
    fores = db.list_foreshadowing(pid)
    plots = db.list_plotlines(pid)
    reader_rows = db.list_reader(pid)
    char_states = db.list_character_states(pid)

    summary_text = _chat(pid, "summary", profile,
                         prompts.summary_messages(project, chapter, prev_chapters, chapter["content"]))
    known = "、".join(c["name"] for c in chars) or "(暂无)"
    open_titles = "、".join(f["title"] for f in fores if f["status"] == "open") or "(暂无)"
    state_text = _chat(pid, "state", profile,
                       prompts.state_extract_messages(project, chapter, chapter["content"], known, open_titles),
                       json_mode=True)
    raw_state = extract_json(state_text)
    state, validation = _validate_state(
        raw_state, chars, fores, plots, reader_rows, idx, chapters, char_states
    )

    with db.transaction():
        db.update_chapter(cid, {"summary": summary_text})
        db.add_memory_event(
            pid, idx, "chapter", "update",
            before=_snap_chapter(chapter),
            after={"idx": idx, "title": chapter.get("title"), "status": "final", "summary": summary_text},
        )
        for c in state["characters"]:
            before = _snap_character(db.get_character(pid, c["name"]))
            res = db.upsert_character(
                pid, c["name"], c["role"], c["profile"], c["status"], chapter_idx=idx
            )
            after = _snap_character(db.get_character(pid, c["name"]))
            if before != after:
                db.add_memory_event(
                    pid, idx, "character",
                    "create" if (res and res.get("created")) else "update",
                    before=before, after=after, entity_id=(res or {}).get("id"),
                )
            if res and res.get("status_guard"):
                validation["warnings"].append(
                    f"角色「{c['name']}」当前状态未回退(旧章重定稿;时间线已追加)"
                )
            if c["status"]:
                st = db.add_character_state(pid, c["name"], idx, c["status"])
                _log_memory_result(pid, idx, "character_state", st, _snap_character_state)
        for f in state["foreshadowing"]:
            res = db.upsert_foreshadow(pid, f["title"], f["description"], f["action"], idx)
            _log_memory_result(pid, idx, "foreshadowing", res, _snap_foreshadow)
        for p in state["plotlines"]:
            res = db.upsert_plotline(pid, p["title"], p["description"], p["action"], idx)
            _log_memory_result(pid, idx, "plotline", res, _snap_plotline)
        for r in state["reader_state"]:
            res = db.upsert_reader(pid, r["kind"], r["content"], idx, r["action"])
            _log_memory_result(pid, idx, "reader", res, _snap_reader)
        db.update_chapter(cid, {"status": "final"})
        db.touch_project(pid)
        db.add_log(
            pid, "finalize",
            f"第{idx}章 定稿,摘要 {len(summary_text)}字,"
            f"状态校验提示 {len(validation['warnings'])} 条/修正 {len(validation['fixes'])} 条",
        )
        if validation["warnings"] or validation["fixes"]:
            db.add_memory_event(
                pid, idx, "validation", "check",
                after={"warnings": validation["warnings"], "fixes": validation["fixes"]},
            )
    return {"summary": summary_text, "state": state, "validation": validation}


def import_text(pid, text):
    """拆书导入:按「第X章/节」标题拆分 txt,建立章节(可在此基础上续写/仿写)。"""
    import re

    pat = re.compile(r"^[ \t]*(第[0-9零一二三四五六七八九十百千两]+[章节卷回][^\n]{0,40})$", re.M)
    matches = list(pat.finditer(text or ""))
    chapters = []
    if len(matches) >= 2:
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            chapters.append({"title": m.group(1).strip(), "content": body})
    else:
        chapters.append({"title": "正文", "content": (text or "").strip()})
    chapters = [c for c in chapters if c["content"]]
    if not chapters:
        raise ValueError("导入内容为空")
    db.replace_chapters(pid, [{"idx": i, "title": c["title"], "summary": "", "beats": ""} for i, c in enumerate(chapters, 1)])
    for i, c in enumerate(chapters, 1):
        row = db.one("SELECT id FROM chapters WHERE project_id=? AND idx=?", (pid, i))
        db.update_chapter(row["id"], {"content": c["content"], "status": "draft"})
    db.touch_project(pid)
    db.add_log(pid, "import", f"导入 {len(chapters)} 章")
    return len(chapters)


def restore_version(pid, cid, vid):
    """回滚到指定版本:当前稿先自动快照,再写回历史版本。"""
    version = db.get_version(vid)
    if not version or version["chapter_id"] != cid:
        raise ValueError("版本不存在")
    db.add_version(cid, "回滚前自动保存")
    db.update_chapter(cid, {"content": version["content"] or "", "status": "draft"})
    db.touch_project(pid)
    return db.get_chapter(cid)


def batch_generate(pid, count=3, review_each=False, on_event=None):
    """批量连写:对尚未写作的章节依次执行 章纲 → 正文 →(可选审校)→ 定稿。

    只处理 status=planned 且无正文的章节,绝不覆盖已有内容;
    连续 2 章失败自动熔断,避免空烧 API。
    """
    def ev(data):
        if on_event:
            on_event(data)

    settings = load_settings(db)
    chapters = db.list_chapters(pid)
    todo = [ch for ch in chapters
            if ch["status"] == "planned" and not (ch.get("content") or "").strip()][:max(1, count)]
    if not todo:
        raise ValueError("没有待写的章节(均已定稿或有正文)。可在大纲页手动加章,或勾选覆盖重新生成大纲。")
    done, fails = 0, 0
    for ch in todo:
        cid = ch["id"]
        ev({"event": "chapter_start", "idx": ch["idx"], "title": ch["title"]})
        try:
            if len((ch.get("beats") or "").strip()) < 10:
                gen_beats(pid, cid)
            gen_draft(pid, cid)
            if review_each:
                review_chapter(pid, cid)
            finalize_chapter(pid, cid)
            done += 1
            fails = 0
            fresh = db.get_chapter(cid)
            gate = {}
            try:
                gate = json.loads(fresh.get("gate") or "{}")
            except Exception:
                pass
            ev({"event": "chapter_done", "idx": ch["idx"], "cid": cid,
                "chars": len(fresh.get("content") or ""),
                "gate_issues": [i for i in gate.get("issues", []) if i.get("level") == "warn"]})
        except Exception as e:
            fails += 1
            ev({"event": "chapter_error", "idx": ch["idx"], "message": str(e)})
            if fails >= 2:
                ev({"event": "aborted", "done": done, "message": "连续 2 章失败,批量任务中止(避免空烧 API)"})
                break
    ev({"event": "batch_done", "done": done, "total": len(todo)})
    return {"done": done, "total": len(todo)}
