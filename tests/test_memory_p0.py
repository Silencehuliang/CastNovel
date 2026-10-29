"""P0 记忆可信性测试:事务/变更日志/状态校验/预算分配。

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


def test_validation_and_status_guard():
    tmp = fresh_db()
    try:
        pid = db.run("INSERT INTO projects(title) VALUES(?)", ("校验测试",))
        db.upsert_character(pid, "林辰", "主角", "少年", "初入江湖")
        db.run(
            "INSERT INTO foreshadowing(project_id,title,description,status) VALUES(?,?,?,?)",
            (pid, "玉佩", "身世信物", "open"),
        )
        chapters = [{"idx": 1}, {"idx": 2}]
        char_states = [
            {"id": 1, "name": "阿七", "chapter_idx": 1, "status": "已死亡"},
        ]
        chars = db.list_characters(pid)
        fores = db.list_foreshadowing(pid)
        state = {
            "characters": [
                {"name": "林辰", "role": "主角", "profile": "", "status": "突破"},
                {"name": "林辰", "role": "主角", "profile": "", "status": "突破"},
                {"name": "新人物", "status": "登场"},
            ],
            "foreshadowing": [{"title": "新伏笔", "description": "线索", "action": "planted"}],
            "plotlines": [{"title": "复仇线", "description": "推进", "action": "begin"}],
            "reader_state": [{"kind": "已知", "content": "读者知道玉佩", "action": "new"}],
        }
        clean, val = stages._validate_state(state, chars, fores, [], [], 2, chapters, char_states)
        check(len(clean["characters"]) == 2, "重复角色应合并")
        check(clean["characters"][1]["profile"], "新角色缺 profile 应补默认值")
        check(clean["foreshadowing"][0]["action"] == "plant", "planted 应归一化为 plant")
        check(clean["plotlines"][0]["action"] == "start", "begin 应归一化为 start")
        check(clean["reader_state"][0]["kind"] == "known", "中文 kind 应归一化")
        check(clean["reader_state"][0]["action"] == "add", "new 应归一化为 add")
        check(any("重复" in f for f in val["fixes"]), "应记录去重修正")

        bad = {"foreshadowing": [{"title": "x", "action": "wat"}]}
        try:
            stages._validate_state(bad, chars, fores, [], [], 2, chapters, char_states)
            check(False, "非法 action 应校验失败")
        except ValueError as e:
            check("action 非法" in str(e), f"错误信息不明确:{e}")

        dead = {"characters": [{"name": "阿七", "status": "安然无恙"}]}
        try:
            stages._validate_state(dead, chars, fores, [], [], 2, chapters, char_states)
            check(False, "死亡状态回退应校验失败")
        except ValueError as e:
            check("回退" in str(e), f"错误信息不明确:{e}")

        bad_ch = {"characters": [{"name": "林辰", "status": "突破"}]}
        try:
            stages._validate_state(bad_ch, chars, fores, [], [], 3, chapters, char_states)
            check(False, "未来章号应校验失败")
        except ValueError as e:
            check("超出" in str(e), f"错误信息不明确:{e}")

        db.run("INSERT INTO characters(project_id,name,status) VALUES(?,?,?)", (pid, "守卫", "重伤"))
        db.add_character_state(pid, "守卫", 5, "重伤")
        res = db.upsert_character(pid, "守卫", status="健康", chapter_idx=3)
        check(res["status_guard"], "旧章重定稿应触发当前状态守卫")
        check(db.get_character(pid, "守卫")["status"] == "重伤", "旧章状态不应回退当前档案")
        res = db.upsert_character(pid, "守卫", status="康复", chapter_idx=6)
        check(not res["status_guard"], "新章状态应正常写入")
        check(db.get_character(pid, "守卫")["status"] == "康复", "新章状态应更新")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_finalize_mock_and_reject():
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
        check(not res["validation"]["errors"], "合法 mock 状态不应校验失败")
        check(db.get_chapter(cid)["status"] == "final", "定稿后章节状态应为 final")
        check(db.get_character(pid, "林辰")["status"] == "修为突破,获得关键信物", "人物状态未回写")
        check(len(db.list_character_states(pid)) >= 2, "人物状态时间线未写入")

        events = db.list_memory_events(pid)
        check(events, "定稿未产生记忆变更日志")
        entities = {e["entity"] for e in events}
        for ent in ("chapter", "character", "character_state", "foreshadowing", "plotline", "reader"):
            check(ent in entities, f"变更日志缺少实体:{ent}")

        cid2 = db.add_chapter(pid, 2)
        db.update_chapter(cid2, {"title": "第二章", "content": "第二章正文。" * 80, "status": "draft"})
        orig_chat = stages._chat

        def fake_chat(pid_, stage, profile, messages, on_delta=None, json_mode=False):
            if stage == "summary":
                return "第二章摘要"
            if stage == "state":
                return '{"foreshadowing": [{"title": "坏动作", "action": "wat"}]}'
            return orig_chat(pid_, stage, profile, messages, on_delta=on_delta, json_mode=json_mode)

        stages._chat = fake_chat
        try:
            stages.finalize_chapter(pid, cid2)
            check(False, "非法状态应拒绝定稿")
        except ValueError as e:
            check("action 非法" in str(e), f"错误信息不明确:{e}")
        finally:
            stages._chat = orig_chat

        ch2 = db.get_chapter(cid2)
        check(ch2["status"] == "draft", "校验失败后章节状态不应改变")
        check(not (ch2.get("summary") or ""), "校验失败后摘要不应落库")
        check(not db.list_memory_events(pid, chapter_idx=2), "校验失败后不应产生变更日志")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_transaction()
    print("[OK] 事务提交/回滚 + memory_events")
    test_validation_and_status_guard()
    print("[OK] 状态校验/去重/死亡回退/旧章守卫")
    test_finalize_mock_and_reject()
    print("[OK] mock 定稿全链路 + 校验失败不落库")
    print("\nP0 memory tests passed")


if __name__ == "__main__":
    main()
