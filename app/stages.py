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
from app.memory import build_context
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


def _ctx_parts(pid, target_idx, settings):
    project = _load(pid)
    characters = db.list_characters(pid)
    chapters = db.list_chapters(pid)
    fores = db.list_foreshadowing(pid)
    lore = db.list_lore(pid, only_enabled=True)
    reader = db.list_reader(pid)
    qvec, dvecs = _vector_aux(pid, chapters, target_idx, settings)
    context = build_context(project, characters, chapters, fores, target_idx, settings,
                            lore=lore, query_vec=qvec, doc_vectors=dvecs, reader=reader)
    return project, characters, chapters, context


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


def finalize_chapter(pid, cid):
    """定稿:用快模型生成摘要 + 抽取人物状态与伏笔变化,写入故事圣经。"""
    settings = load_settings(db)
    profile = resolve_profile(settings, "fast")
    chapter = db.get_chapter(cid)
    if not chapter or not (chapter.get("content") or "").strip():
        raise ValueError("本章还没有正文,无法定稿。")
    project = _load(pid)
    idx = chapter["idx"]

    prev_chapters = [ch for ch in db.list_chapters(pid) if ch["idx"] < idx]
    summary_text = _chat(pid, "summary", profile,
                         prompts.summary_messages(project, chapter, prev_chapters, chapter["content"]))
    db.update_chapter(cid, {"summary": summary_text})

    chars = db.list_characters(pid)
    fores = db.list_foreshadowing(pid)
    known = "、".join(c["name"] for c in chars) or "(暂无)"
    open_titles = "、".join(f["title"] for f in fores if f["status"] == "open") or "(暂无)"
    state_text = _chat(pid, "state", profile,
                       prompts.state_extract_messages(project, chapter, chapter["content"], known, open_titles),
                       json_mode=True)
    state = extract_json(state_text)
    for c in state.get("characters", []) or []:
        if isinstance(c, dict) and c.get("name"):
            db.upsert_character(pid, c.get("name", ""), c.get("role", ""),
                                c.get("profile", ""), c.get("status", ""))
    for f in state.get("foreshadowing", []) or []:
        if isinstance(f, dict) and f.get("title"):
            db.upsert_foreshadow(pid, f.get("title", ""), f.get("description", ""),
                                 f.get("action", "plant"), idx)
    for pl in state.get("plotlines", []) or []:
        if isinstance(pl, dict) and pl.get("title"):
            db.upsert_plotline(pid, pl.get("title", ""), pl.get("description", ""),
                               pl.get("action", "start"), idx)
    for r in state.get("reader_state", []) or []:
        if isinstance(r, dict) and r.get("content"):
            db.upsert_reader(pid, r.get("kind", "known"), r.get("content", ""),
                             idx, r.get("action", "add"))
    db.update_chapter(cid, {"status": "final"})
    db.touch_project(pid)
    db.add_log(pid, "finalize", f"第{idx}章 定稿,摘要 {len(summary_text)}字")
    return {"summary": summary_text, "state": state}


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
