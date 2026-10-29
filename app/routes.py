"""REST API 路由。所有生成类接口统一以 SSE 流式返回:
data: {"type":"delta","text":"..."}(过程文本)/ {"type":"done","result":...} / {"type":"error","message":"..."}
非流式接口返回普通 JSON。
"""
import json
import queue
import threading

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import db, export, publish, stages
from app.config import PROVIDER_PRESETS, find_profile, is_mock, load_settings, resolve_profile, save_settings
from app.llm import LLMError, chat, list_models

router = APIRouter()


class ProjectIn(BaseModel):
    title: str
    genre: str = ""
    premise: str = ""
    style: str = ""


class ProjectPatch(BaseModel):
    title: str | None = None
    genre: str | None = None
    premise: str | None = None
    style: str | None = None
    worldview: str | None = None
    style_ref: str | None = None


class BatchIn(BaseModel):
    count: int = 3
    review_each: bool = False


class AdoptIn(BaseModel):
    content: str
    note: str = ""


class OutlineIn(BaseModel):
    count: int = 30
    replace: bool = False


class ChapterPatch(BaseModel):
    title: str | None = None
    beats: str | None = None
    summary: str | None = None
    content: str | None = None
    status: str | None = None


class PublishBindingIn(BaseModel):
    book_id: str
    config: dict | None = None


class PublishSyncIn(BaseModel):
    lo: int | None = None
    hi: int | None = None
    force: bool = False
    dry: bool = False


class CharacterIn(BaseModel):
    name: str
    role: str = ""
    profile: str = ""
    status: str = ""


def _err(e: Exception):
    if isinstance(e, (LLMError, ValueError)):
        raise HTTPException(status_code=400, detail=str(e))
    raise HTTPException(status_code=500, detail=f"服务器错误:{e}")


def _sse_stage(fn):
    """fn(on_delta) 在工作线程中执行;on_delta 的文本实时推给前端,结束后推 result。"""

    def gen():
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                result = fn(on_delta=lambda t: q.put(("delta", t)))
                q.put(("done", result))
            except Exception as e:
                q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind == "delta":
                yield f"data: {json.dumps({'type': 'delta', 'text': payload}, ensure_ascii=False)}\n\n"
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done', 'result': payload}, ensure_ascii=False)}\n\n"
                break
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': str(payload)}, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_progress(fn):
    """fn(emit) 在工作线程中执行;emit 的进度事件实时推给前端,结束后推 result。"""

    def gen():
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                result = fn(emit=lambda evt: q.put(("progress", evt)))
                q.put(("done", result))
            except Exception as e:
                q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind == "progress":
                yield f"data: {json.dumps({'type': 'progress', 'data': payload}, ensure_ascii=False)}\n\n"
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done', 'result': payload}, ensure_ascii=False)}\n\n"
                break
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': str(payload)}, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _bundle(pid, with_content=True):
    project = db.get_project(pid)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {
        "project": project,
        "characters": db.list_characters(pid),
        "chapters": db.list_chapters(pid, with_content=with_content),
        "foreshadowing": db.list_foreshadowing(pid),
        "plotlines": db.list_plotlines(pid),
    }


# ---------- 设置 ----------

@router.get("/settings")
def get_settings():
    s = load_settings(db)
    return {
        "settings": s,
        "resolved": {
            "strong": resolve_profile(s, "strong").get("name"),
            "fast": resolve_profile(s, "fast").get("name"),
            "strong_mock": is_mock(resolve_profile(s, "strong")),
            "fast_mock": is_mock(resolve_profile(s, "fast")),
        },
    }


@router.put("/settings")
def put_settings(payload: dict):
    try:
        s = save_settings(db, payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"设置格式有误:{e}")
    return {"settings": s}


class TestProfileIn(BaseModel):
    name: str


@router.post("/llm/test")
def test_llm(payload: TestProfileIn):
    s = load_settings(db)
    prof = find_profile(s, payload.name)
    if not prof:
        raise HTTPException(status_code=404, detail="找不到该模型档案")
    try:
        text = chat(prof, [{"role": "user", "content": "请只回复四个字:连接成功"}])
        return {"ok": True, "reply": text[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/providers")
def get_providers():
    """供应商预设:设置页「从模板添加」用,一键填 Base URL 与默认模型。"""
    return PROVIDER_PRESETS


class ModelsProbeIn(BaseModel):
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout: int = 180


@router.post("/llm/models")
def probe_models(payload: ModelsProbeIn):
    """拉取某服务的模型列表(不要求先保存档案),供设置页下拉选择。"""
    try:
        ids = list_models(payload.model_dump())
        return {"ok": True, "models": ids}
    except Exception as e:
        return {"ok": False, "error": str(e), "models": []}


# ---------- 项目 ----------

@router.get("/projects")
def list_projects():
    return db.list_projects()


@router.post("/projects")
def create_project(payload: ProjectIn):
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="书名不能为空")
    pid = db.run(
        "INSERT INTO projects(title,genre,premise,style) VALUES(?,?,?,?)",
        (payload.title.strip(), payload.genre.strip(), payload.premise.strip(), payload.style.strip()),
    )
    return {"id": pid}


@router.get("/projects/{pid}")
def get_project(pid: int):
    return _bundle(pid)


@router.put("/projects/{pid}")
def patch_project(pid: int, payload: ProjectPatch):
    db.update_project(pid, payload.model_dump(exclude_none=True))
    db.touch_project(pid)
    return {"ok": True}


@router.delete("/projects/{pid}")
def delete_project(pid: int):
    if not db.get_project(pid):
        raise HTTPException(status_code=404, detail="项目不存在")
    db.delete_project_data(pid)
    return {"ok": True}


# ---------- 生成流水线(SSE) ----------

@router.post("/projects/{pid}/generate/worldview")
def gen_worldview(pid: int):
    return _sse_stage(lambda on_delta: stages.gen_worldview(pid, on_delta=on_delta))


@router.post("/projects/{pid}/generate/characters")
def gen_characters(pid: int):
    def run(on_delta):
        stages.gen_characters(pid)
        return _bundle(pid)

    return _sse_stage(run)


@router.post("/projects/{pid}/generate/outline")
def gen_outline(pid: int, payload: OutlineIn):
    def run(on_delta):
        stages.gen_outline(pid, count=max(3, min(payload.count, 200)), replace=payload.replace, on_delta=on_delta)
        return _bundle(pid)

    return _sse_stage(run)


@router.post("/projects/{pid}/chapters/{cid}/beats")
def gen_beats(pid: int, cid: int):
    def run(on_delta):
        stages.gen_beats(pid, cid, on_delta=on_delta)
        return db.get_chapter(cid)

    return _sse_stage(run)


@router.post("/projects/{pid}/chapters/{cid}/draft")
def gen_draft(pid: int, cid: int):
    def run(on_delta):
        stages.gen_draft(pid, cid, on_delta=on_delta)
        return db.get_chapter(cid)

    return _sse_stage(run)


class DraftBestIn(BaseModel):
    n: int = 2


@router.post("/projects/{pid}/chapters/{cid}/draft/best")
def gen_draft_best(pid: int, cid: int, payload: DraftBestIn):
    """候选拉样+重排:拉 n 份候选,主编评分重排,择优写入;落选稿存入版本历史。"""
    def run(on_delta):
        out = stages.gen_draft_best(pid, cid, n=payload.n, on_delta=on_delta)
        return {**db.get_chapter(cid), "rank": out.get("_rank")}

    return _sse_stage(run)


@router.post("/projects/{pid}/chapters/{cid}/deai")
def deai(pid: int, cid: int):
    """两步去 AI 味:span 检测 → 带例改写。返回新稿供预览,不落库。"""
    def run(on_delta):
        return stages.deai_rewrite(pid, cid, on_delta=on_delta)

    return _sse_stage(run)


@router.post("/projects/{pid}/chapters/{cid}/polish")
def polish(pid: int, cid: int):
    def run(on_delta):
        text = stages.polish_chapter(pid, cid, on_delta=on_delta)
        return {"text": text}

    return _sse_stage(run)


@router.post("/projects/{pid}/chapters/{cid}/review")
def review(pid: int, cid: int):
    def run(on_delta):
        return stages.review_chapter(pid, cid)

    return _sse_stage(run)


@router.post("/projects/{pid}/chapters/{cid}/revise")
def revise(pid: int, cid: int):
    def run(on_delta):
        text = stages.revise_chapter(pid, cid, on_delta=on_delta)
        return {"text": text}

    return _sse_stage(run)


@router.post("/projects/{pid}/chapters/{cid}/finalize")
def finalize(pid: int, cid: int):
    def run(on_delta):
        result = stages.finalize_chapter(pid, cid)
        result["bundle"] = _bundle(pid)
        return result

    return _sse_stage(run)


# ---------- 记忆变更审计 ----------

@router.get("/projects/{pid}/memory-events")
def memory_events(pid: int, chapter_idx: int | None = None, limit: int = 200):
    """定稿回写的 before/after 变更日志(按时间倒序)。"""
    if not db.get_project(pid):
        raise HTTPException(status_code=404, detail="项目不存在")
    return db.list_memory_events(pid, chapter_idx=chapter_idx, limit=limit)


# ---------- 章节编辑 ----------

@router.get("/projects/{pid}/chapters/{cid}/context")
def get_context(pid: int, cid: int):
    chapter = db.get_chapter(cid)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    settings = load_settings(db)
    bundle = stages._ctx_bundle(pid, chapter["idx"], settings)
    return {
        "context": bundle["context"],
        "chars": len(bundle["context"]),
        "budget": bundle["report"],
    }


@router.put("/projects/{pid}/chapters/{cid}")
def patch_chapter(pid: int, cid: int, payload: ChapterPatch):
    db.update_chapter(cid, payload.model_dump(exclude_none=True))
    db.touch_project(pid)
    return db.get_chapter(cid)


@router.post("/projects/{pid}/chapters")
def add_chapter(pid: int):
    cid = db.add_chapter(pid)
    db.touch_project(pid)
    return db.get_chapter(cid)


@router.delete("/projects/{pid}/chapters/{cid}")
def delete_chapter(pid: int, cid: int):
    db.delete_chapter(cid)
    return {"ok": True}


@router.post("/projects/{pid}/chapters/{cid}/adopt")
def adopt_content(pid: int, cid: int, payload: AdoptIn):
    try:
        return stages.adopt_content(pid, cid, payload.content, payload.note)
    except Exception as e:
        _err(e)


# ---------- 章节版本 ----------

@router.get("/projects/{pid}/chapters/{cid}/versions")
def list_versions(pid: int, cid: int):
    return db.list_versions(cid)


@router.post("/projects/{pid}/chapters/{cid}/versions")
def create_version(pid: int, cid: int, payload: dict):
    note = (payload or {}).get("note", "")
    db.add_version(cid, note)
    return db.list_versions(cid)


@router.get("/projects/{pid}/chapters/{cid}/versions/{vid}")
def get_version(pid: int, cid: int, vid: int):
    v = db.get_version(vid)
    if not v or v["chapter_id"] != cid:
        raise HTTPException(status_code=404, detail="版本不存在")
    return v


@router.post("/projects/{pid}/chapters/{cid}/versions/{vid}/restore")
def restore_version(pid: int, cid: int, vid: int):
    try:
        return stages.restore_version(pid, cid, vid)
    except Exception as e:
        _err(e)


# ---------- 批量连写 ----------

@router.post("/projects/{pid}/batch")
def batch(pid: int, payload: BatchIn):
    def gen():
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                result = stages.batch_generate(
                    pid, payload.count, payload.review_each,
                    on_event=lambda e: q.put(("ev", e)))
                q.put(("done", result))
            except Exception as e:
                q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, data = q.get()
            if kind == "ev":
                yield f"data: {json.dumps({'type': 'progress', 'data': data}, ensure_ascii=False)}\n\n"
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done', 'result': data}, ensure_ascii=False)}\n\n"
                break
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': str(data)}, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- 设定变更影响分析 + 级联重生成 ----------

@router.get("/projects/{pid}/impact")
def impact(pid: int, query: str = ""):
    """按关键词(角色/地点/设定词,逗号分隔)反查受影响章节。零 token。"""
    try:
        return stages.find_impact(pid, query)
    except Exception as e:
        _err(e)


class CascadeIn(BaseModel):
    chapter_ids: list[int]
    include_written: bool = False


@router.post("/projects/{pid}/cascade")
def cascade(pid: int, payload: CascadeIn):
    def gen():
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                result = stages.cascade_regenerate(
                    pid, payload.chapter_ids, payload.include_written,
                    on_event=lambda e: q.put(("ev", e)))
                q.put(("done", result))
            except Exception as e:
                q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, data = q.get()
            if kind == "ev":
                yield f"data: {json.dumps({'type': 'progress', 'data': data}, ensure_ascii=False)}\n\n"
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done', 'result': data}, ensure_ascii=False)}\n\n"
                break
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': str(data)}, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- Lorebook(关键词触发设定注入) ----------

class LoreIn(BaseModel):
    keywords: str = ""
    content: str
    note: str = ""
    enabled: bool = True
    secondary: str = ""
    logic: str = "and_any"
    constant: bool = False
    priority: int = 0


@router.get("/projects/{pid}/lore")
def list_lore(pid: int):
    return db.list_lore(pid)


@router.post("/projects/{pid}/lore")
def add_lore(pid: int, payload: LoreIn):
    if not payload.content.strip() or (not payload.keywords.strip() and not payload.constant):
        raise HTTPException(status_code=400, detail="内容不能为空;非常驻条目关键词也不能为空")
    lid = db.add_lore(pid, payload.keywords, payload.content, payload.note,
                      secondary=payload.secondary, logic=payload.logic,
                      constant=payload.constant, priority=payload.priority,
                      enabled=payload.enabled)
    return {"id": lid}


@router.put("/projects/{pid}/lore/{lid}")
def patch_lore(pid: int, lid: int, payload: dict):
    fields = {}
    for k in ("keywords", "secondary", "logic", "content", "note"):
        if k in payload and payload[k] is not None:
            fields[k] = payload[k]
    for k in ("enabled", "constant"):
        if k in payload:
            fields[k] = 1 if payload[k] else 0
    if "priority" in payload and payload["priority"] is not None:
        fields["priority"] = int(payload["priority"])
    db.update_lore(lid, fields)
    return {"ok": True}


@router.delete("/projects/{pid}/lore/{lid}")
def remove_lore(pid: int, lid: int):
    db.delete_lore(lid)
    return {"ok": True}


@router.get("/projects/{pid}/lore/export")
def export_lore(pid: int):
    """导出为 SillyTavern World Info JSON(可直接导入 ST / NovelAI 生态)。"""
    _bundle(pid)
    return stages.export_lore(pid)


class LoreImportIn(BaseModel):
    data: dict | list


@router.post("/projects/{pid}/lore/import")
def import_lore(pid: int, payload: LoreImportIn):
    """导入 SillyTavern World Info JSON(merge,不清空现有条目)。"""
    _bundle(pid)
    try:
        n = stages.import_lore(pid, payload.data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"count": n}


# ---------- 角色状态时间线 ----------

@router.get("/projects/{pid}/character-states")
def character_states(pid: int, name: str = ""):
    """角色状态沿革(时间性事实):按章回放;name 留空返回全部角色的事件。"""
    _bundle(pid)
    return db.list_character_states(pid, name=name or None)


# ---------- 读者认知状态 ----------

class ReaderIn(BaseModel):
    kind: str = "known"
    content: str
    chapter_idx: int | None = None


@router.get("/projects/{pid}/reader")
def list_reader(pid: int):
    return db.list_reader(pid)


@router.post("/projects/{pid}/reader")
def add_reader(pid: int, payload: ReaderIn):
    if payload.kind not in db.READER_KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 known/expect/mislead")
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    rid = db.run(
        "INSERT INTO reader_state(project_id,kind,content,chapter_idx) VALUES(?,?,?,?)",
        (pid, payload.kind, payload.content.strip(), payload.chapter_idx),
    )
    return {"id": rid}


@router.put("/projects/{pid}/reader/{rid}")
def patch_reader(pid: int, rid: int, payload: dict):
    fields = {}
    for k in ("kind", "content", "status", "chapter_idx"):
        if k in payload and payload[k] is not None:
            fields[k] = payload[k]
    if fields.get("kind") not in (None,) and fields["kind"] not in db.READER_KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 known/expect/mislead")
    db.update_reader(rid, fields)
    return {"ok": True}


@router.delete("/projects/{pid}/reader/{rid}")
def remove_reader(pid: int, rid: int):
    db.delete_reader(rid)
    return {"ok": True}


# ---------- 门禁重算 ----------

@router.post("/projects/{pid}/chapters/{cid}/gate")
def regate(pid: int, cid: int):
    """对章节当前正文重跑一致性门禁(零 token),刷新徽章。"""
    try:
        return stages.recompute_gate(pid, cid)
    except Exception as e:
        _err(e)


# ---------- 情节线 ----------

@router.put("/projects/{pid}/plotlines/{plid}")
def patch_plotline(pid: int, plid: int, payload: dict):
    allowed = {k: payload[k] for k in ("title", "description", "status") if k in payload}
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        db.run(f"UPDATE plotlines SET {sets} WHERE id=? AND project_id=?", (*allowed.values(), plid, pid))
    return {"ok": True}


@router.delete("/projects/{pid}/plotlines/{plid}")
def remove_plotline(pid: int, plid: int):
    db.delete_plotline(plid)
    return {"ok": True}


class ImportIn(BaseModel):
    text: str


@router.post("/projects/{pid}/import")
def import_novel(pid: int, payload: ImportIn):
    try:
        count = stages.import_text(pid, payload.text)
        return {"count": count, "bundle": _bundle(pid)}
    except Exception as e:
        _err(e)


@router.get("/projects/{pid}/usage")
def get_usage(pid: int):
    return db.sum_usage(pid)


# ---------- 角色与伏笔 ----------

@router.put("/projects/{pid}/characters/{chid}")
def patch_character(pid: int, chid: int, payload: CharacterIn):
    row = db.one("SELECT * FROM characters WHERE id=? AND project_id=?", (chid, pid))
    if not row:
        raise HTTPException(status_code=404, detail="角色不存在")
    db.run(
        "UPDATE characters SET name=?, role=?, profile=?, status=? WHERE id=?",
        (payload.name, payload.role, payload.profile, payload.status, chid),
    )
    return {"ok": True}


@router.delete("/projects/{pid}/characters/{chid}")
def remove_character(pid: int, chid: int):
    db.delete_character(chid)
    return {"ok": True}


@router.put("/projects/{pid}/foreshadowing/{fid}")
def patch_foreshadow(pid: int, fid: int, payload: dict):
    allowed = {k: payload[k] for k in ("title", "description", "status", "planted_ch", "payoff_ch") if k in payload}
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        db.run(f"UPDATE foreshadowing SET {sets} WHERE id=? AND project_id=?", (*allowed.values(), fid, pid))
    return {"ok": True}


@router.delete("/projects/{pid}/foreshadowing/{fid}")
def remove_foreshadow(pid: int, fid: int):
    db.delete_foreshadow(fid)
    return {"ok": True}


# ---------- 导出 ----------

@router.get("/projects/{pid}/export")
def do_export(pid: int, format: str = "md"):
    from urllib.parse import quote

    bundle = _bundle(pid, with_content=True)
    project, chapters = bundle["project"], bundle["chapters"]

    def _headers(name):
        return {
            "Content-Disposition": f"attachment; filename=castnovel-export.{name.rsplit('.', 1)[-1]}; filename*=UTF-8''{quote(name)}"
        }

    if format == "docx":
        data, name, media = export.export_docx(project, chapters)
        return Response(content=data, media_type=media, headers=_headers(name))
    if format == "epub":
        data, name, media = export.export_epub(project, chapters)
        return Response(content=data, media_type=media, headers=_headers(name))
    text, name, media = export.export_text(project, chapters, fmt=format)
    return Response(content=text.encode("utf-8"), media_type=media, headers=_headers(name))


# ---------- 平台发布 / 同步 ----------

@router.get("/projects/{pid}/publish")
def publish_overview(pid: int):
    if not db.get_project(pid):
        raise HTTPException(status_code=404, detail="项目不存在")
    return publish.overview(pid)


@router.put("/projects/{pid}/publish/{platform}/binding")
def publish_bind(pid: int, platform: str, payload: PublishBindingIn):
    try:
        publish.set_binding(pid, platform, payload.book_id, payload.config)
    except publish.PublishError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return publish.overview(pid)


@router.get("/projects/{pid}/publish/{platform}/plan")
def publish_plan(pid: int, platform: str, lo: int | None = None,
                 hi: int | None = None, force: bool = False):
    try:
        return {"items": publish.plan(pid, platform, lo, hi, force)}
    except publish.PublishError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{pid}/publish/{platform}/pull")
def publish_pull(pid: int, platform: str, with_ids: bool = True):
    return _sse_progress(lambda emit: publish.pull_mapping(
        pid, platform, with_ids=with_ids, on_event=emit))


@router.post("/projects/{pid}/publish/{platform}/sync")
def publish_sync(pid: int, platform: str, payload: PublishSyncIn):
    return _sse_progress(lambda emit: publish.sync_chapters(
        pid, platform, payload.lo, payload.hi, payload.force, payload.dry, on_event=emit))
