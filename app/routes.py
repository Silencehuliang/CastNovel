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

from app import db, export, memory, stages
from app.config import find_profile, is_mock, load_settings, resolve_profile, save_settings
from app.llm import LLMError, chat

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
    db.run("DELETE FROM characters WHERE project_id=?", (pid,))
    db.run("DELETE FROM chapters WHERE project_id=?", (pid,))
    db.run("DELETE FROM foreshadowing WHERE project_id=?", (pid,))
    db.run("DELETE FROM projects WHERE id=?", (pid,))
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


# ---------- 章节编辑 ----------

@router.get("/projects/{pid}/chapters/{cid}/context")
def get_context(pid: int, cid: int):
    chapter = db.get_chapter(cid)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    settings = load_settings(db)
    project = db.get_project(pid)
    characters = db.list_characters(pid)
    chapters = db.list_chapters(pid)
    fores = db.list_foreshadowing(pid)
    ctx = memory.build_context(project, characters, chapters, fores, chapter["idx"], settings)
    return {"context": ctx, "chars": len(ctx)}


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
            "Content-Disposition": f"attachment; filename=novelforge-export.{name.rsplit('.', 1)[-1]}; filename*=UTF-8''{quote(name)}"
        }

    if format == "docx":
        data, name, media = export.export_docx(project, chapters)
        return Response(content=data, media_type=media, headers=_headers(name))
    if format == "epub":
        data, name, media = export.export_epub(project, chapters)
        return Response(content=data, media_type=media, headers=_headers(name))
    text, name, media = export.export_text(project, chapters, fmt=format)
    return Response(content=text.encode("utf-8"), media_type=media, headers=_headers(name))
