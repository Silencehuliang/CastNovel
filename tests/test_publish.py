"""平台发布/同步测试(临时库 + 假驱动,不碰真实库、不启动浏览器)。

用法:python tests/test_publish.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, ".")

from app import db, publish


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


CALLS = []
FAIL = set()


def fake_driver(task, timeout=300):
    CALLS.append(task)
    if task["action"] == "list":
        return {"ok": True, "rows": [
            {"serial": 1, "title": "开篇", "words": 111, "cid": "9001"},
            {"serial": 2, "title": "转折", "words": 222, "cid": "9002"},
        ]}
    if task.get("dry"):
        return {"ok": True, "dry": True,
                "editor": {"title": task.get("title", ""), "len": 100},
                "match": True, "volume_ok": True, "new_chapter_button": True}
    if task["serial"] in FAIL:
        raise publish.PublishError("模拟平台失败")
    cid = task.get("chapter_id") or f"c{task['serial']}"
    return {"ok": True, "chapter_id": cid, "words": 120,
            "row": {"serial": task["serial"], "title": task.get("title", ""),
                    "words": 120, "cid": cid}}


def actions(items):
    return [i["action"] for i in items]


def main():
    tmp = tempfile.mkdtemp(prefix="castnovel-publish-")
    db.DB_PATH = os.path.join(tmp, "test.db")
    db._conn = None
    db.init()

    publish.run_driver = fake_driver
    publish.time.sleep = lambda s: None

    pid = db.run("INSERT INTO projects(title, volumes) VALUES(?,?)", (
        "测试书", json.dumps([{"title": "第一卷:起", "chapters": [1, 2]}], ensure_ascii=False)))
    for idx, title, body in [(1, "开篇", "第一段。\n\n第二段。"),
                             (2, "转折", "新的正文。"),
                             (3, "空章", "")]:
        cid = db.add_chapter(pid, idx)
        db.update_chapter(cid, {"title": title, "content": body})

    # ---- 绑定与校验 ----
    try:
        publish.set_binding(pid, "fanqie", "  ")
        raise AssertionError("空书籍 ID 应被拒绝")
    except publish.PublishError:
        pass
    publish.set_binding(pid, "fanqie", "BOOK1")
    check(publish.get_binding(pid, "fanqie")["book_id"] == "BOOK1", "绑定未保存")

    # ---- 首次计划:无映射 → 新建;空正文 → 跳过 ----
    items = publish.plan(pid, "fanqie", 1, 3)
    check(actions(items) == ["create", "create", "skip"], actions(items))
    check(items[2]["reason"] == "本地正文为空", items[2])
    check(items[0]["platform_words"] == 0 and items[0]["platform_status"] == "", items[0])

    # ---- 单章新建 ----
    CALLS.clear()
    r = publish.sync_chapters(pid, "fanqie", 2, 2)
    check(r["done"] == 1 and r["created"] == 1 and r["failed"] == 0, r)
    check(CALLS[-1]["action"] == "create" and CALLS[-1]["serial"] == 2, CALLS[-1])
    check(CALLS[-1]["volume"] == "第一卷:起", CALLS[-1])
    check(CALLS[-1]["body"] == "新的正文。", CALLS[-1])
    row = db.get_publish_map_row(pid, "fanqie", 2)
    check(row["status"] == "published" and row["platform_chapter_id"] == "c2", dict(row))
    check(row["content_hash"] == publish.content_hash("新的正文。"), "同步后应记录内容哈希")

    # ---- 未改动的章节再次计划 → 跳过 ----
    items = publish.plan(pid, "fanqie", 2, 2)
    check(items[0]["action"] == "skip" and items[0]["reason"] == "平台内容已是最新", items[0])

    # ---- 本地改动 → 更新;范围同步一次处理 create + update ----
    ch2 = db.get_chapter_by_idx(pid, 2)
    db.update_chapter(ch2["id"], {"content": "改过的正文。"})
    items = publish.plan(pid, "fanqie", 2, 2)
    check(items[0]["action"] == "update" and items[0]["chapter_id"] == "c2", items[0])
    check(items[0]["reason"] == "本地内容已修改", items[0])

    CALLS.clear()
    r = publish.sync_chapters(pid, "fanqie", 1, 2)
    check((r["created"], r["updated"], r["failed"], r["skipped"]) == (1, 1, 0, 0), r)
    check([c["action"] for c in CALLS] == ["create", "update"], [c["action"] for c in CALLS])
    check(actions(publish.plan(pid, "fanqie", 1, 2)) == ["skip", "skip"], "同步后应全部跳过")

    # ---- 失败:更新失败保留章节 ID,下次仍为 update ----
    FAIL.add(1)
    ch1 = db.get_chapter_by_idx(pid, 1)
    db.update_chapter(ch1["id"], {"content": "再次修改。"})
    r = publish.sync_chapters(pid, "fanqie", 1, 1)
    check(r["failed"] == 1 and r["done"] == 0, r)
    row = db.get_publish_map_row(pid, "fanqie", 1)
    check(row["status"] == "failed" and row["platform_chapter_id"] == "c1", dict(row))
    check(publish.plan(pid, "fanqie", 1, 1)[0]["action"] == "update", "失败后应仍待更新")
    FAIL.clear()

    # ---- 失败:新建失败不写映射,下次仍为 create ----
    cid5 = db.add_chapter(pid, 5)
    db.update_chapter(cid5, {"title": "新章", "content": "第五章正文。"})
    FAIL.add(5)
    r = publish.sync_chapters(pid, "fanqie", 5, 5)
    check(r["failed"] == 1, r)
    check(db.get_publish_map_row(pid, "fanqie", 5) is None, "新建失败不应留下映射")
    check(publish.plan(pid, "fanqie", 5, 5)[0]["action"] == "create", "新建失败后应仍为 create")
    FAIL.clear()

    # ---- 拉取映射:只写映射,不写内容哈希;首次同步提示覆盖 ----
    db.delete_publish_map(pid, "fanqie")
    r = publish.pull_mapping(pid, "fanqie")
    check(r["platform_rows"] == 2 and r["matched"] == 2 and r["unmatched"] == [], r)
    row = db.get_publish_map_row(pid, "fanqie", 1)
    check(row["platform_chapter_id"] == "9001" and row["status"] == "mapped", dict(row))
    check(row["content_hash"] == "", "拉取映射不应写内容哈希")
    item = publish.plan(pid, "fanqie", 1, 1)[0]
    check(item["action"] == "update", item)
    check(item["reason"] == "首次同步,将用本地正文覆盖平台章节", item)
    check(publish.plan(pid, "fanqie", 5, 5)[0]["action"] == "create", "平台没有的章应新建")

    # ---- dry:只预演,不写映射 ----
    CALLS.clear()
    r = publish.sync_chapters(pid, "fanqie", 1, 1, dry=True)
    check(r["dry"] is True and len(r["items"]) == 1 and r["items"][0]["ok"] is True, r)
    check(CALLS and CALLS[0]["dry"] is True and "body" not in CALLS[0], CALLS)
    row = db.get_publish_map_row(pid, "fanqie", 1)
    check(row["status"] == "mapped" and row["content_hash"] == "", "dry 不应写库")

    # ---- 强制同步 ----
    items = publish.plan(pid, "fanqie", 2, 2, force=True)
    check(items[0]["action"] == "update" and items[0]["reason"] == "强制同步", items[0])

    # ---- 正文清洗 / 哈希 / 分卷 ----
    raw = "# 第1章 标题\n\n第一段。\n---\n**加粗**第二段。"
    check(publish.clean_content(raw) == "第一段。\n\n加粗第二段。", publish.clean_content(raw))
    h1 = publish.content_hash("同样的正文")
    check(h1 == publish.content_hash("同样的正文") and h1 != publish.content_hash("别的正文"), "哈希不稳定")
    binding = publish.get_binding(pid, "fanqie")
    check(publish.volume_for(pid, binding, 2) == "第一卷:起", publish.volume_for(pid, binding, 2))
    check(publish.volume_for(pid, binding, 9) == "", "范围外应无分卷")

    # ---- 总览 ----
    ov = publish.overview(pid)
    p = ov["platforms"][0]
    check(p["key"] == "fanqie" and p["bound"] is True and p["book_id"] == "BOOK1", p)
    check(len(p["map"]) == 2 and len(p["plan"]) == 4, (len(p["map"]), len(p["plan"])))
    pid2 = db.run("INSERT INTO projects(title) VALUES(?)", ("未绑定",))
    p2 = publish.overview(pid2)["platforms"][0]
    check(p2["bound"] is False and p2["plan"] == [] and p2["map"] == [], p2)

    print("ALL PASS: publish plan/sync/pull/dry/failure/overview")


if __name__ == "__main__":
    main()
