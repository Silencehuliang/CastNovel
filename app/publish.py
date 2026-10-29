"""平台发布与同步:把本地章节上传到番茄等连载平台,修改后可同步单章或范围。

架构:
  平台驱动(浏览器自动化) = tools/ego_fanqie.mjs,由 ego-browser nodejs 执行,
    通过 data/publish_task/{task,result}.json 文件约定交换数据(规避命令行编码问题)。
  本模块 = 平台注册表 + 内容清洗 + 同步计划 + 编排执行 + 映射落库。
  新增平台只需:写一个 ego_<platform>.mjs 驱动 + 在 PLATFORMS 注册实现。

同步语义(用户改完稿后):
  - 本地无映射记录            → create(新建章节上传)
  - 有映射且 content_hash 一致 → skip(平台已是最新)
  - 有映射但内容不同           → update(覆盖平台章节)
"""
import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path

from app import db
from app.config import BASE_DIR

TASK_DIR = BASE_DIR / "data" / "publish_task"
DRIVER_DIR = BASE_DIR / "tools"

PLATFORMS = {
    "fanqie": {"label": "番茄小说", "driver": "ego_fanqie.mjs", "space": "castnovel-fanqie",
               "site": "fanqienovel.com"},
}

_lock = threading.Lock()


class PublishError(Exception):
    pass


# ---------- 内容准备 ----------

def clean_content(text: str) -> str:
    """正文清洗:与导出口径一致 —— 去标题行/分节线/加粗记号,空行分段。"""
    lines = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln == "---" or ln.startswith("# "):
            continue
        ln = ln.replace("**", "")
        lines.append(ln)
    return "\n\n".join(lines)


def content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


# ---------- 绑定 ----------

def get_binding(pid: int, platform: str) -> dict:
    row = db.get_publish_binding(pid, platform)
    if not row:
        raise PublishError(
            f"项目 {pid} 未绑定平台 {platform}:先在导出页或 CLI 里设置平台书籍 ID"
        )
    cfg = {}
    try:
        cfg = json.loads(row["config"] or "{}")
    except Exception:
        pass
    return {"book_id": row["book_id"], "config": cfg}


def set_binding(pid: int, platform: str, book_id: str, config: dict | None = None):
    if platform not in PLATFORMS:
        raise PublishError(f"未知平台:{platform},可用:{', '.join(PLATFORMS)}")
    if not str(book_id).strip():
        raise PublishError("书籍 ID 不能为空")
    db.set_publish_binding(pid, platform, str(book_id).strip(), config or {})


def volume_for(pid: int, binding: dict, idx: int) -> str:
    """章节所属分卷的平台卷名。config.volume_map 优先,缺省取项目大纲的 volumes。"""
    vm = binding["config"].get("volume_map")
    if not vm:
        proj = db.get_project(pid)
        if not proj:
            return ""
        try:
            vols = json.loads(proj["volumes"] or "[]")
        except Exception:
            vols = []
        vm = [
            {"lo": v["chapters"][0], "hi": v["chapters"][1], "name": v.get("title", "")}
            for v in vols
            if isinstance(v, dict) and isinstance(v.get("chapters"), list) and len(v["chapters"]) == 2
        ]
    for v in vm:
        try:
            if int(v.get("lo", 0)) <= idx <= int(v.get("hi", 0)):
                return str(v.get("name", ""))
        except (TypeError, ValueError):
            continue
    return ""


# ---------- 驱动调用 ----------

def _ego_browser_exe() -> str:
    import shutil

    exe = shutil.which("ego-browser")
    if not exe:
        raise PublishError("找不到 ego-browser 命令,请确认 Ego Lite 与 CLI 已安装")
    return exe


def run_driver(task: dict, timeout: int = 300) -> dict:
    """调 ego-browser 跑一次驱动动作,返回 result.json。"""
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    platform = task.get("platform", "fanqie")
    spec = PLATFORMS.get(platform)
    if not spec:
        raise PublishError(f"未知平台:{platform}")
    task_path = TASK_DIR / "task.json"
    result_path = TASK_DIR / "result.json"
    payload = {k: v for k, v in task.items() if k != "platform"}
    payload.setdefault("space", spec["space"])
    task_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if result_path.exists():
        result_path.unlink()

    driver = DRIVER_DIR / spec["driver"]
    exe = _ego_browser_exe()
    with open(driver, "rb") as stdin:
        try:
            proc = subprocess.run(
                [exe, "nodejs"], stdin=stdin, capture_output=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise PublishError(f"驱动超时({timeout}s):{task.get('action')} ch{task.get('serial')}")
    tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
    if not result_path.exists():
        raise PublishError("驱动没有写出 result.json:" + " | ".join(tail))
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise PublishError(f"result.json 解析失败:{e};stdout 尾部:{' | '.join(tail)}")
    result.setdefault("_stdout", tail)
    return result


# ---------- 计划与同步 ----------

def plan(pid: int, platform: str, lo: int = None, hi: int = None, force: bool = False) -> list[dict]:
    """逐章给出 create/update/skip 决定(不动平台)。"""
    binding = get_binding(pid, platform)
    mapping = {r["idx"]: r for r in db.list_publish_map(pid, platform, binding["book_id"])}
    items = []
    for ch in db.list_chapters(pid):
        idx = ch["idx"]
        if lo is not None and idx < lo:
            continue
        if hi is not None and idx > hi:
            continue
        body = clean_content(ch["content"])
        h = content_hash(body)
        m = mapping.get(idx)
        item = {
            "idx": idx, "title": ch["title"] or "", "words": len(body), "hash": h,
            "action": "skip", "reason": "", "chapter_id": "",
            "platform_words": int(m["words"] or 0) if m else 0,
            "platform_status": m["status"] if m else "",
            "synced_at": m["synced_at"] if m else "",
        }
        if not body:
            item["reason"] = "本地正文为空"
        elif m is None:
            item.update(action="create", reason="平台尚无此章,将新建上传")
        elif not m["platform_chapter_id"]:
            item["reason"] = "映射缺平台章节 ID,先拉取平台映射"
        elif m["content_hash"] == h and not force:
            item.update(reason="平台内容已是最新", chapter_id=m["platform_chapter_id"])
        elif force:
            item.update(action="update", reason="强制同步",
                        chapter_id=m["platform_chapter_id"])
        elif not m["content_hash"]:
            item.update(action="update", reason="首次同步,将用本地正文覆盖平台章节",
                        chapter_id=m["platform_chapter_id"])
        else:
            item.update(action="update", reason="本地内容已修改",
                        chapter_id=m["platform_chapter_id"])
        items.append(item)
    return items


def overview(pid: int) -> dict:
    """发布页一次取全:平台列表 + 各平台绑定/映射/计划(未绑定平台只返回元信息)。"""
    out = []
    for key, spec in PLATFORMS.items():
        row = db.get_publish_binding(pid, key)
        cfg = {}
        if row:
            try:
                cfg = json.loads(row["config"] or "{}")
            except Exception:
                cfg = {}
        item = {
            "key": key, "label": spec["label"], "site": spec.get("site", ""),
            "bound": bool(row), "book_id": row["book_id"] if row else "",
            "config": cfg, "map": [], "plan": [],
        }
        if row:
            item["map"] = [dict(r) for r in db.list_publish_map(pid, key)]
            try:
                item["plan"] = plan(pid, key)
            except PublishError:
                pass
        out.append(item)
    return {"platforms": out}


def pull_mapping(pid: int, platform: str, with_ids: bool = True, on_event=None) -> dict:
    """抓平台章节列表,把 序号/标题/字数/章节ID 写进 publish_map(本地内容不变)。"""
    with _lock:
        return _pull_locked(pid, platform, with_ids, on_event)


def _pull_locked(pid, platform, with_ids, on_event):
    binding = get_binding(pid, platform)
    task = {
        "platform": platform, "action": "list",
        "book_id": binding["book_id"], "with_ids": bool(with_ids),
    }
    if on_event:
        on_event({"type": "pull_start", "book_id": binding["book_id"]})
    result = run_driver(task, timeout=600)
    if not result.get("ok"):
        raise PublishError("拉取映射失败:" + str(result.get("error")) + ";日志:" + " | ".join(result.get("log", [])[-5:]))
    rows = result.get("rows") or []
    local = {c["idx"]: c for c in db.list_chapters(pid, with_content=False)}
    matched, unmatched = 0, []
    for r in rows:
        idx = int(r.get("serial") or 0)
        if idx <= 0:
            continue
        db.upsert_publish_map(
            pid, platform, binding["book_id"], idx,
            platform_chapter_id=str(r.get("cid") or ""),
            title=r.get("title") or "",
            words=int(r.get("words") or 0),
            status="mapped",
        )
        if idx in local:
            matched += 1
        else:
            unmatched.append(idx)
        if on_event:
            on_event({"type": "pull_row", "idx": idx, "title": r.get("title", ""),
                      "chapter_id": r.get("cid", ""), "words": r.get("words", 0)})
    if on_event:
        on_event({"type": "pull_done", "platform_rows": len(rows), "matched": matched})
    return {"platform_rows": len(rows), "matched": matched, "unmatched": unmatched,
            "warnings": result.get("warnings") or []}


def sync_chapters(pid: int, platform: str, lo: int = None, hi: int = None,
                  force: bool = False, dry: bool = False, on_event=None) -> dict:
    """同步一个范围(单章即 lo==hi):按计划逐章 create/update,skip 直接跳过。"""
    with _lock:
        return _sync_locked(pid, platform, lo, hi, force, dry, on_event)


def _sync_locked(pid, platform, lo, hi, force, dry, on_event):
    binding = get_binding(pid, platform)
    cfg = binding["config"]
    all_items = plan(pid, platform, lo, hi, force)
    items = [i for i in all_items if i["action"] in ("create", "update")]
    skipped = len(all_items) - len(items)
    if dry:
        # dry 只验证映射与驱动连通:update 对已映射章做「打开编辑页核对」,create 校验分卷
        for i in items:
            if on_event:
                on_event({"type": "chapter_start", **{k: i[k] for k in ("idx", "title", "action")}})
            try:
                if i["action"] == "update":
                    res = run_driver({
                        "platform": platform, "action": "update", "dry": True,
                        "book_id": binding["book_id"], "chapter_id": i["chapter_id"],
                        "title": i["title"], "serial": i["idx"],
                    })
                    if not res.get("ok"):
                        raise PublishError(res.get("error") or "dry 失败")
                    i["dry"] = {"platform_title": res.get("editor", {}).get("title", ""),
                                "platform_len": res.get("editor", {}).get("len", 0),
                                "title_match": res.get("match")}
                else:
                    res = run_driver({
                        "platform": platform, "action": "create", "dry": True,
                        "book_id": binding["book_id"],
                        "volume": volume_for(pid, binding, i["idx"]),
                        "serial": i["idx"], "title": i["title"],
                    })
                    if not res.get("ok"):
                        raise PublishError(res.get("error") or "dry 失败")
                    i["dry"] = {"volume_ok": res.get("volume_ok"), "new_chapter_button": res.get("new_chapter_button")}
                i["ok"] = True
                if on_event:
                    on_event({"type": "chapter_done", "idx": i["idx"], "ok": True, "dry": i["dry"]})
            except Exception as e:
                i["ok"] = False
                i["error"] = str(e)
                if on_event:
                    on_event({"type": "chapter_done", "idx": i["idx"], "ok": False, "error": str(e)})
        return {"dry": True, "items": items}

    summary = {"done": 0, "created": 0, "updated": 0, "failed": 0, "skipped": 0, "errors": []}
    for i in items:
        idx, action = i["idx"], i["action"]
        if on_event:
            on_event({"type": "chapter_start", "idx": idx, "title": i["title"], "action": action})
        task = {
            "platform": platform,
            "book_id": binding["book_id"],
            "serial": idx,
            "title": i["title"],
            "body": clean_content(db.get_chapter_by_idx(pid, idx)["content"]),
            "ai_flag": cfg.get("ai_flag", "no"),
        }
        try:
            if action == "create":
                task["action"] = "create"
                task["volume"] = volume_for(pid, binding, idx)
                if not task["volume"]:
                    raise PublishError(f"第 {idx} 章找不到对应分卷(config.volume_map 或项目大纲 volumes)")
            else:
                task["action"] = "update"
                task["chapter_id"] = i["chapter_id"]
            res = run_driver(task)
            if not res.get("ok"):
                raise PublishError(res.get("error") or "驱动返回失败")
            cid = str(res.get("chapter_id") or i["chapter_id"] or "")
            words = int(res.get("words") or 0)
            db.upsert_publish_map(
                pid, platform, binding["book_id"], idx,
                platform_chapter_id=cid, title=i["title"], words=words,
                content_hash=i["hash"], status="published", note="",
            )
            summary["done"] += 1
            summary["created" if action == "create" else "updated"] += 1
            if on_event:
                on_event({"type": "chapter_done", "idx": idx, "ok": True,
                          "chapter_id": cid, "words": words, "action": action})
        except Exception as e:
            summary["failed"] += 1
            summary["errors"].append({"idx": idx, "error": str(e)})
            # 新建失败不写映射(下次仍是 create);已有映射则只标 failed 保留章节 ID
            if db.get_publish_map_row(pid, platform, idx, binding["book_id"]):
                db.upsert_publish_map(
                    pid, platform, binding["book_id"], idx,
                    status="failed", note=str(e)[:200],
                )
            if on_event:
                on_event({"type": "chapter_done", "idx": idx, "ok": False, "error": str(e)})
        time.sleep(1.0)
    summary["skipped"] = skipped
    return summary


def status(pid: int, platform: str) -> dict:
    binding_row = db.get_publish_binding(pid, platform)
    spec = PLATFORMS.get(platform, {})
    out = {
        "platform": platform, "label": spec.get("label", platform),
        "bound": bool(binding_row),
        "book_id": binding_row["book_id"] if binding_row else "",
        "config": json.loads(binding_row["config"]) if binding_row and binding_row["config"] else {},
        "map": [dict(r) for r in db.list_publish_map(pid, platform)] if binding_row else [],
        "plan": [],
    }
    if binding_row:
        try:
            out["plan"] = plan(pid, platform)
        except PublishError:
            pass
    return out
