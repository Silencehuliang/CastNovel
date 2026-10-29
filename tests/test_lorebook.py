"""Lorebook 六字段 + SillyTavern 导入导出测试(临时库,不碰 data/novelforge.db)。
用法:python tests/test_lorebook.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

from app import db, stages
from app.memory import hit_lore


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


ST_DATA = {
    "entries": {
        "0": {
            "uid": 0,
            "key": ["玉佩", "身世"],
            "keysecondary": ["雷雨"],
            "selectiveLogic": 3,  # AND ALL
            "content": "玉佩是身世信物。",
            "comment": "玉佩设定",
            "constant": False,
            "order": 150,
            "disable": False,
        },
        "1": {
            "uid": 1,
            "key": [],
            "keysecondary": [],
            "content": "本书世界为大渊王朝。",
            "constant": True,
            "order": 100,
            "disable": False,
        },
        "2": {
            "uid": 2,
            "key": ["暗门"],
            "content": "暗门在城西。",
            "constant": False,
            "order": 120,
            "disable": True,
        },
        "3": {
            "uid": 3,
            "key": ["玉佩"],
            "content": "常驻且主键命中的条目。",
            "constant": True,
            "order": 100,
            "disable": False,
        },
    }
}


def main():
    tmp = tempfile.mkdtemp(prefix="castnovel-lore-")
    try:
        db.DB_PATH = os.path.join(tmp, "test.db")
        db._conn = None
        db.init()
        pid = db.run("INSERT INTO projects(title) VALUES(?)", ("测试项目",))

        n = stages.import_lore(pid, ST_DATA)
        check(n == 4, f"应导入 4 条,实际 {n}")
        rows = {r["keywords"]: r for r in db.list_lore(pid)}
        check("玉佩, 身世" in rows, "主键应以逗号拼接")
        jade = rows["玉佩, 身世"]
        check(jade["secondary"] == "雷雨", "次级键未持久化")
        check(jade["logic"] == "and_all", "selectiveLogic=3 应映射为 and_all")
        check(jade["priority"] == 50, "order=150 应映射为 priority=50")
        check(jade["enabled"] == 1, "启用状态错误")
        constant = rows[""]
        check(constant["constant"] == 1, "无主键的常驻条目应被导入")
        check(rows["暗门"]["enabled"] == 0, "disable 条目应停用")

        enabled = db.list_lore(pid, only_enabled=True)
        hits = hit_lore(enabled, "他摸着玉佩,屋外雷雨大作。")
        check(len(hits) == 3, f"命中主键+次键与两条常驻应注入 3 条,实际 {len(hits)}")
        check(sum(1 for h in hits if "常驻且主键命中" in h) == 1, "常驻条目主键命中时不应重复注入")
        check(not any("暗门" in h for h in hits), "停用条目不应注入")
        hits = hit_lore(enabled, "他摸着玉佩,天晴。")
        check(len(hits) == 2 and not any("雷雨" in h for h in hits), "AND ALL 次键不满足时应被过滤,仅常驻注入")
        hits = hit_lore(enabled, "天晴。")
        check(len(hits) == 2 and all("大渊" in h or "常驻且" in h for h in hits), "无命中时应只注入常驻条目")

        out = stages.export_lore(pid)
        check(out["name"] == "castnovel-lorebook", "导出名称错误")
        exported = {e["comment"]: e for e in out["entries"].values()}
        jade_out = exported["玉佩设定"]
        check(jade_out["key"] == ["玉佩", "身世"], "导出主键错误")
        check(jade_out["keysecondary"] == ["雷雨"], "导出次级键错误")
        check(jade_out["selectiveLogic"] == 3, "导出 selectiveLogic 错误")
        check(jade_out["order"] == 150, "导出 order 错误")
        check(any(e["constant"] and "大渊" in e["content"] for e in out["entries"].values()),
              "导出常驻标记错误")

        pid2 = db.run("INSERT INTO projects(title) VALUES(?)", ("往返项目",))
        n2 = stages.import_lore(pid2, out)
        check(n2 == 4, f"往返导入应为 4 条,实际 {n2}")
        rows2 = {r["keywords"]: r for r in db.list_lore(pid2)}
        check(rows2["玉佩, 身世"]["logic"] == "and_all", "往返后 logic 丢失")
        check(rows2["玉佩, 身世"]["priority"] == 50, "往返后 priority 丢失")
        check(rows2["暗门"]["enabled"] == 0, "往返后启用状态丢失")

        lid = jade["id"]
        db.update_lore(lid, {"secondary": "月夜", "logic": "not_any", "priority": 7, "constant": 1})
        row = db.one("SELECT * FROM lorebook WHERE id=?", (lid,))
        check(row["secondary"] == "月夜" and row["logic"] == "not_any"
              and row["priority"] == 7 and row["constant"] == 1, "update_lore 未写入六字段")

        try:
            stages.import_lore(pid, {"foo": "bar"})
            raise AssertionError("非法格式应抛 ValueError")
        except ValueError:
            pass
        print("lorebook tests passed")
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
