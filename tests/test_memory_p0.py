"""P0-1 测试:定稿事务、memory_events 审计、项目数据清理。

用法:python tests/test_memory_p0.py(使用临时库,不碰 data/novelforge.db)
"""
import os
import shutil
import sys
import tempfile

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

os.environ["CASTNOVEL_FORCE_MOCK"] = "1"
sys.path.insert(0, ".")

from app import db, stages


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def fresh_db():
    tmp = tempfile.mkdtemp(prefix="castnovel-p0-")
    db.DB_PATH = os.path.join(tmp, "test.db")
    db._conn = None
    db._tx_depth = 0
    db.init()
    return tmp


def test_transaction():
    tmp = fresh_db()
    try:
        pid = db.run("INSERT INTO projects(title) VALUES(?)", ("事务测试",))
        try:
            with db.transaction():
                db.run("INSERT INTO characters(project_id,name) VALUES(?,?)", (pid, "A"))
                db.add_memory_event(pid, 1, "character", "create", after={"name": "A"})
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        check(not db.list_characters(pid), "事务回滚后角色不应存在")
        check(not db.list_memory_events(pid), "事务回滚后变更日志不应存在")

        with db.transaction():
            db.run("INSERT INTO characters(project_id,name) VALUES(?,?)", (pid, "B"))
            db.add_memory_event(pid, 1, "character", "create", after={"name": "B"})
        check(len(db.list_characters(pid)) == 1, "事务提交后角色应存在")
        check(len(db.list_memory_events(pid)) == 1, "事务提交后变更日志应存在")

        cid = db.add_chapter(pid, 1)
        db.add_version(cid, "快照")
        db.delete_project_data(pid)
        check(not db.get_project(pid), "删除项目后项目不应存在")
        check(not db.list_characters(pid), "删除项目后角色应清理")
        check(not db.list_chapters(pid), "删除项目后章节应清理")
        check(not db.list_memory_events(pid), "删除项目后变更日志应清理")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_finalize_mock():
    tmp = fresh_db()
    try:
        pid = db.run(
            "INSERT INTO projects(title,premise,genre,worldview,style) VALUES(?,?,?,?,?)",
            ("定稿测试", "玉佩认主", "悬疑", "大渊王朝,修行者隐于市井。", "节奏明快,画面感强"),
        )
        db.upsert_character(pid, "林辰", "主角", "少年", "初入江湖")
        db.upsert_character(pid, "苏绾", "女主", "医女", "平静")
        cid = db.add_chapter(pid, 1)
        db.update_chapter(cid, {"title": "雷夜", "content": "雨夜,他摸到怀中的玉佩。" * 80, "status": "draft"})

        res = stages.finalize_chapter(pid, cid)
        check(res["summary"], "定稿摘要为空")
        check(db.get_chapter(cid)["status"] == "final", "定稿后章节状态应为 final")
        check(db.get_character(pid, "林辰")["status"] == "修为突破,获得关键信物", "人物状态未回写")
        check(len(db.list_character_states(pid)) >= 2, "人物状态时间线未写入")

        events = db.list_memory_events(pid)
        check(events, "定稿未产生记忆变更日志")
        entities = {e["entity"] for e in events}
        for ent in ("chapter", "character", "character_state", "foreshadowing", "plotline", "reader"):
            check(ent in entities, f"变更日志缺少实体:{ent}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_transaction()
    print("[OK] 事务提交/回滚 + memory_events + 项目清理")
    test_finalize_mock()
    print("[OK] mock 定稿单事务回写 + 审计日志")
    print("\nP0-1 memory tests passed")


if __name__ == "__main__":
    main()
