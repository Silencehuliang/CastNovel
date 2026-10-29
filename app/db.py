"""SQLite 存储层:单连接 + 写锁,覆盖项目/角色/章节/伏笔/设置的读写。"""
import json
import sqlite3
import threading
from contextlib import contextmanager

from app.config import DB_PATH

_lock = threading.RLock()
_conn = None
_tx_depth = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS character_states(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  name TEXT NOT NULL,            -- 角色名(与 characters.name 对应)
  chapter_idx INTEGER NOT NULL,  -- 该状态自第 idx 章定稿起生效(双时间线之「事件时间线」)
  status TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  genre TEXT DEFAULT '',
  premise TEXT DEFAULT '',
  style TEXT DEFAULT '',
  worldview TEXT DEFAULT '',
  volumes TEXT DEFAULT '',
  extra TEXT DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS characters(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  role TEXT DEFAULT '',
  profile TEXT DEFAULT '',
  status TEXT DEFAULT '',
  UNIQUE(project_id, name)
);
CREATE TABLE IF NOT EXISTS chapters(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  title TEXT DEFAULT '',
  beats TEXT DEFAULT '',
  summary TEXT DEFAULT '',
  content TEXT DEFAULT '',
  status TEXT DEFAULT 'planned',
  review TEXT DEFAULT '',
  UNIQUE(project_id, idx)
);
CREATE TABLE IF NOT EXISTS foreshadowing(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  planted_ch INTEGER,
  payoff_ch INTEGER,
  status TEXT DEFAULT 'open',
  UNIQUE(project_id, title)
);
CREATE TABLE IF NOT EXISTS kv(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS usage_stats(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER,
  stage TEXT,
  prompt_chars INTEGER DEFAULT 0,
  completion_chars INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER,
  stage TEXT,
  detail TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS chapter_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chapter_id INTEGER NOT NULL,
  content TEXT,
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS plotlines(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  status TEXT DEFAULT 'active',
  start_ch INTEGER,
  last_ch INTEGER,
  UNIQUE(project_id, title)
);
CREATE TABLE IF NOT EXISTS lorebook(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  keywords TEXT NOT NULL,          -- 主键(逗号分隔,命中其一即候选)
  secondary TEXT DEFAULT '',       -- 次级键(逗号分隔,配合 logic 二次过滤)
  logic TEXT DEFAULT 'and_any',    -- and_any / and_all / not_any / not_all
  constant INTEGER DEFAULT 0,      -- 常驻:不依赖命中,直接注入
  priority INTEGER DEFAULT 0,      -- 注入顺序,大者优先;预算不足时让位
  content TEXT NOT NULL,
  note TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS embeddings(
  project_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  model_tag TEXT NOT NULL,
  vector TEXT NOT NULL,
  updated_at TEXT DEFAULT (datetime('now','localtime')),
  PRIMARY KEY(project_id, idx, model_tag)
);
CREATE TABLE IF NOT EXISTS reader_state(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  kind TEXT NOT NULL,            -- known(读者已知) / expect(读者期待) / mislead(读者误认)
  content TEXT NOT NULL,
  chapter_idx INTEGER,
  status TEXT DEFAULT 'open',    -- open / done(期待已兑现、误认已揭穿)
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS memory_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  chapter_idx INTEGER,
  entity TEXT NOT NULL,          -- character / character_state / foreshadowing / plotline / reader / chapter / validation
  entity_id INTEGER,
  action TEXT NOT NULL,          -- create / update / resolve / noop / skipped / check
  before TEXT DEFAULT '',        -- 变更前快照(JSON)
  after TEXT DEFAULT '',         -- 变更后快照(JSON)
  source TEXT DEFAULT 'finalize',
  status TEXT DEFAULT 'applied', -- applied / skipped / rejected
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_memory_events_project
  ON memory_events(project_id, chapter_idx, id);
"""

# 运行时增量迁移:旧库缺列时补齐
_MIGRATE_COLUMNS = [
    ("projects", "style_ref", "TEXT DEFAULT ''"),
    ("chapters", "gate", "TEXT DEFAULT ''"),
    ("lorebook", "secondary", "TEXT DEFAULT ''"),
    ("lorebook", "logic", "TEXT DEFAULT 'and_any'"),
    ("lorebook", "constant", "INTEGER DEFAULT 0"),
    ("lorebook", "priority", "INTEGER DEFAULT 0"),
]


def init():
    global _conn
    if _conn is not None:
        return
    _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(SCHEMA)
    for table, col, ddl in _MIGRATE_COLUMNS:
        cols = {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    _conn.commit()


@contextmanager
def transaction():
    """单连接上的事务边界:块内所有 run() 不单独提交,异常整体回滚。

    只允许一个线程持有事务;块内对 q/one/run 的调用会重入同一把可重入锁。
    """
    global _tx_depth
    init()
    with _lock:
        if _tx_depth:
            _tx_depth += 1
            try:
                yield
            finally:
                _tx_depth -= 1
            return
        _conn.execute("BEGIN IMMEDIATE")
        _tx_depth = 1
        try:
            yield
        except BaseException:
            _conn.rollback()
            raise
        else:
            _conn.commit()
        finally:
            _tx_depth = 0


def q(sql, params=()):
    init()
    with _lock:
        cur = _conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def run(sql, params=()):
    init()
    with _lock:
        cur = _conn.execute(sql, params)
        if not _tx_depth:
            _conn.commit()
        return cur.lastrowid


# ---------- kv 设置 ----------

def get_setting(key, default=None):
    row = one("SELECT value FROM kv WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    run(
        "INSERT INTO kv(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# ---------- 项目 ----------

def list_projects():
    return q("SELECT id,title,genre,premise,style,created_at,updated_at FROM projects ORDER BY updated_at DESC")


def get_project(pid):
    return one("SELECT * FROM projects WHERE id=?", (pid,))


def touch_project(pid):
    run("UPDATE projects SET updated_at=datetime('now','localtime') WHERE id=?", (pid,))


def update_project(pid, fields: dict):
    allowed = ["title", "genre", "premise", "style", "worldview", "volumes", "extra", "style_ref"]
    sets, params = [], []
    for k in allowed:
        if k in fields:
            v = fields[k]
            if k == "extra" and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return
    sets.append("updated_at=datetime('now','localtime')")
    params.append(pid)
    run(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", tuple(params))


_PROJECT_TABLES = (
    "character_states", "characters", "chapters", "foreshadowing", "usage_stats",
    "logs", "plotlines", "lorebook", "embeddings", "reader_state", "memory_events",
)


def delete_project_data(pid):
    """项目删除:同一事务清理全部项目级数据(含章节版本与记忆日志),不留孤儿行。"""
    cids = [r["id"] for r in q("SELECT id FROM chapters WHERE project_id=?", (pid,))]
    with transaction():
        for cid in cids:
            run("DELETE FROM chapter_versions WHERE chapter_id=?", (cid,))
        for table in _PROJECT_TABLES:
            run(f"DELETE FROM {table} WHERE project_id=?", (pid,))
        run("DELETE FROM projects WHERE id=?", (pid,))


# ---------- 角色(故事圣经) ----------

def list_characters(pid):
    return q("SELECT * FROM characters WHERE project_id=? ORDER BY id", (pid,))


def get_character(pid, name):
    return one("SELECT * FROM characters WHERE project_id=? AND name=?", (pid, (name or "").strip()))


def upsert_character(pid, name, role="", profile="", status="", overwrite=True):
    name = (name or "").strip()
    if not name:
        return None
    row = one("SELECT * FROM characters WHERE project_id=? AND name=?", (pid, name))
    if row:
        if not overwrite:
            return {"id": row["id"], "created": False, "updated": []}
        sets, params, updated = [], [], []
        if role and role != row["role"]:
            sets.append("role=?"); params.append(role); updated.append("role")
        if profile and profile != row["profile"]:
            sets.append("profile=?"); params.append(profile); updated.append("profile")
        if status and status != row["status"]:
            sets.append("status=?"); params.append(status); updated.append("status")
        if sets:
            params.append(row["id"])
            run(f"UPDATE characters SET {', '.join(sets)} WHERE id=?", tuple(params))
        return {"id": row["id"], "created": False, "updated": updated}
    new_id = run(
        "INSERT INTO characters(project_id,name,role,profile,status) VALUES(?,?,?,?,?)",
        (pid, name, role, profile, status),
    )
    return {"id": new_id, "created": True, "updated": ["role", "profile", "status"]}


def delete_character(cid):
    run("DELETE FROM characters WHERE id=?", (cid,))


def replace_characters(pid, characters):
    run("DELETE FROM characters WHERE project_id=?", (pid,))
    for c in characters or []:
        upsert_character(
            pid,
            c.get("name", ""),
            c.get("role", ""),
            c.get("profile", ""),
            c.get("status", ""),
        )


# ---------- 章节 ----------

def list_chapters(pid, with_content=True):
    cols = "*" if with_content else "id,project_id,idx,title,beats,summary,status,review"
    return q(f"SELECT {cols} FROM chapters WHERE project_id=? ORDER BY idx", (pid,))


def get_chapter(cid):
    return one("SELECT * FROM chapters WHERE id=?", (cid,))


def update_chapter(cid, fields: dict):
    allowed = ["idx", "title", "beats", "summary", "content", "status", "review", "gate"]
    sets, params = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k}=?")
            params.append(fields[k])
    if not sets:
        return
    params.append(cid)
    run(f"UPDATE chapters SET {', '.join(sets)} WHERE id=?", tuple(params))


def replace_chapters(pid, chapters):
    """大纲重排:清空后按顺序写入(仅当用户确认覆盖时调用)。"""
    run("DELETE FROM chapters WHERE project_id=?", (pid,))
    for i, ch in enumerate(chapters or [], start=1):
        run(
            "INSERT INTO chapters(project_id,idx,title,beats,summary,status) "
            "VALUES(?,?,?,?,?,?)",
            (pid, ch.get("idx", i), ch.get("title", ""), ch.get("beats", ""),
             ch.get("summary", ""), "planned"),
        )


def add_chapter(pid, idx=None):
    row = one("SELECT MAX(idx) AS m FROM chapters WHERE project_id=?", (pid,))
    nxt = idx if idx is not None else (row["m"] or 0) + 1
    cid = run(
        "INSERT INTO chapters(project_id,idx,title,status) VALUES(?,?,?,?)",
        (pid, nxt, f"第{nxt}章", "planned"),
    )
    return cid


def delete_chapter(cid):
    run("DELETE FROM chapters WHERE id=?", (cid,))


# ---------- 伏笔 ----------

def list_foreshadowing(pid):
    return q("SELECT * FROM foreshadowing WHERE project_id=? ORDER BY id", (pid,))


def upsert_foreshadow(pid, title, description="", action="plant", chapter_idx=None):
    title = (title or "").strip()
    if not title:
        return None
    row = one("SELECT * FROM foreshadowing WHERE project_id=? AND title=?", (pid, title))
    if row is None:
        if action == "plant":
            fid = run(
                "INSERT INTO foreshadowing(project_id,title,description,planted_ch,status) "
                "VALUES(?,?,?,?,?)",
                (pid, title, description, chapter_idx, "open"),
            )
            after = one("SELECT * FROM foreshadowing WHERE id=?", (fid,))
            return {"action": "create", "id": fid, "before": None, "after": after}
        return {"action": "skipped", "id": None, "before": None, "after": None,
                "reason": "resolve 未匹配到已有伏笔"}
    if action == "resolve":
        if row["status"] == "resolved":
            return {"action": "noop", "id": row["id"], "before": row, "after": row}
        run(
            "UPDATE foreshadowing SET payoff_ch=?, status='resolved' WHERE id=?",
            (chapter_idx, row["id"]),
        )
        after = one("SELECT * FROM foreshadowing WHERE id=?", (row["id"],))
        return {"action": "resolve", "id": row["id"], "before": row, "after": after}
    if description and not row["description"]:
        run("UPDATE foreshadowing SET description=? WHERE id=?", (description, row["id"]))
        after = one("SELECT * FROM foreshadowing WHERE id=?", (row["id"],))
        return {"action": "update", "id": row["id"], "before": row, "after": after}
    return {"action": "noop", "id": row["id"], "before": row, "after": row}


def delete_foreshadow(fid):
    run("DELETE FROM foreshadowing WHERE id=?", (fid,))


def add_log(pid, stage, detail=""):
    run("INSERT INTO logs(project_id,stage,detail) VALUES(?,?,?)", (pid, stage, detail[:4000]))


# ---------- 章节版本 ----------

def add_version(cid, note=""):
    ch = get_chapter(cid)
    if not ch:
        return None
    return run(
        "INSERT INTO chapter_versions(chapter_id,content,note) VALUES(?,?,?)",
        (cid, ch["content"] or "", note),
    )


def list_versions(cid):
    return q(
        "SELECT id, chapter_id, note, created_at, LENGTH(content) AS chars "
        "FROM chapter_versions WHERE chapter_id=? ORDER BY id DESC",
        (cid,),
    )


def get_version(vid):
    return one("SELECT * FROM chapter_versions WHERE id=?", (vid,))


def add_version_text(cid, text, note=""):
    """把给定文本(如落选候选)直接存入版本历史。"""
    return run(
        "INSERT INTO chapter_versions(chapter_id,content,note) VALUES(?,?,?)",
        (cid, text or "", note),
    )


# ---------- 情节线 ----------

def list_plotlines(pid):
    return q("SELECT * FROM plotlines WHERE project_id=? ORDER BY id", (pid,))


def upsert_plotline(pid, title, description="", action="start", chapter_idx=None):
    title = (title or "").strip()
    if not title:
        return None
    row = one("SELECT * FROM plotlines WHERE project_id=? AND title=?", (pid, title))
    if row is None:
        if action in ("start", "advance"):
            plid = run(
                "INSERT INTO plotlines(project_id,title,description,start_ch,last_ch,status) "
                "VALUES(?,?,?,?,?,?)",
                (pid, title, description, chapter_idx, chapter_idx, "active"),
            )
            after = one("SELECT * FROM plotlines WHERE id=?", (plid,))
            return {"action": "create", "id": plid, "before": None, "after": after}
        return {"action": "skipped", "id": None, "before": None, "after": None,
                "reason": "end 未匹配到已有情节线"}
    if action == "end":
        if row["status"] == "done":
            return {"action": "noop", "id": row["id"], "before": row, "after": row}
        run("UPDATE plotlines SET last_ch=?, status='done' WHERE id=?", (chapter_idx, row["id"]))
        after = one("SELECT * FROM plotlines WHERE id=?", (row["id"],))
        return {"action": "end", "id": row["id"], "before": row, "after": after}
    run("UPDATE plotlines SET last_ch=? WHERE id=?", (chapter_idx, row["id"]))
    if description and not row["description"]:
        run("UPDATE plotlines SET description=? WHERE id=?", (description, row["id"]))
    after = one("SELECT * FROM plotlines WHERE id=?", (row["id"],))
    action_out = "update" if (after != row) else "noop"
    return {"action": action_out, "id": row["id"], "before": row, "after": after}


def delete_plotline(plid):
    run("DELETE FROM plotlines WHERE id=?", (plid,))


# ---------- Lorebook(关键词触发设定注入) ----------

def list_lore(pid, only_enabled=False):
    sql = "SELECT * FROM lorebook WHERE project_id=?"
    if only_enabled:
        sql += " AND enabled=1"
    return q(sql + " ORDER BY id", (pid,))


def add_lore(pid, keywords, content, note="", secondary="", logic="and_any",
             constant=0, priority=0, enabled=1):
    return run(
        "INSERT INTO lorebook(project_id,keywords,secondary,logic,constant,priority,content,note,enabled) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (pid, (keywords or "").strip(), (secondary or "").strip(), logic or "and_any",
         1 if constant else 0, int(priority or 0),
         (content or "").strip(), (note or "").strip(), 0 if enabled in (0, False) else 1),
    )


def update_lore(lid, fields: dict):
    allowed = {k: fields[k] for k in
               ("keywords", "secondary", "logic", "constant", "priority", "content", "note", "enabled")
               if k in fields}
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)
    run(f"UPDATE lorebook SET {sets} WHERE id=?", (*allowed.values(), lid))


def delete_lore(lid):
    run("DELETE FROM lorebook WHERE id=?", (lid,))


# ---------- 向量缓存(可选语义检索) ----------

def list_embeddings(pid, model_tag):
    rows = q("SELECT idx, vector FROM embeddings WHERE project_id=? AND model_tag=?", (pid, model_tag))
    return {r["idx"]: json.loads(r["vector"]) for r in rows}


def save_embedding(pid, idx, model_tag, vector):
    run(
        "INSERT INTO embeddings(project_id,idx,model_tag,vector) VALUES(?,?,?,?) "
        "ON CONFLICT(project_id,idx,model_tag) DO UPDATE SET vector=excluded.vector, updated_at=excluded.updated_at",
        (pid, idx, model_tag, json.dumps(vector)),
    )


# ---------- 读者认知状态(与角色已知/作者真相分离) ----------

READER_KINDS = ("known", "expect", "mislead")


def list_reader(pid, only_open=False):
    sql = "SELECT * FROM reader_state WHERE project_id=?"
    if only_open:
        sql += " AND status='open'"
    return q(sql + " ORDER BY id", (pid,))


def upsert_reader(pid, kind, content, chapter_idx=None, action="add"):
    """定稿抽取入口:add 新增(kind 限 known/expect/mislead,同内容去重);
    resolve 按 content 匹配未结条目 → 置 done(期待兑现/误认揭穿)。返回变更结果供审计。"""
    content = (content or "").strip()
    kind = kind if kind in READER_KINDS else "known"
    if not content:
        return None
    row = one(
        "SELECT * FROM reader_state WHERE project_id=? AND kind=? AND content=?",
        (pid, kind, content),
    )
    if action == "resolve":
        if row and row["status"] == "open":
            run("UPDATE reader_state SET status='done' WHERE id=?", (row["id"],))
            after = one("SELECT * FROM reader_state WHERE id=?", (row["id"],))
            return {"action": "resolve", "id": row["id"], "before": row, "after": after}
        like = one(
            "SELECT * FROM reader_state WHERE project_id=? AND kind=? AND status='open' "
            "AND (? LIKE '%'||content||'%' OR content LIKE '%'||?||'%') LIMIT 1",
            (pid, kind, content, content),
        )
        if like:
            run("UPDATE reader_state SET status='done' WHERE id=?", (like["id"],))
            after = one("SELECT * FROM reader_state WHERE id=?", (like["id"],))
            return {"action": "resolve", "id": like["id"], "before": like, "after": after,
                    "fuzzy": True}
        return {"action": "skipped", "id": None, "before": None, "after": None,
                "reason": "未匹配到待结算的读者认知"}
    if row:
        return {"action": "noop", "id": row["id"], "before": row, "after": row}
    rid = run(
        "INSERT INTO reader_state(project_id,kind,content,chapter_idx) VALUES(?,?,?,?)",
        (pid, kind, content, chapter_idx),
    )
    after = one("SELECT * FROM reader_state WHERE id=?", (rid,))
    return {"action": "create", "id": rid, "before": None, "after": after}


def update_reader(rid, fields: dict):
    allowed = {k: fields[k] for k in ("kind", "content", "status", "chapter_idx") if k in fields}
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)
    run(f"UPDATE reader_state SET {sets} WHERE id=?", (*allowed.values(), rid))


def delete_reader(rid):
    run("DELETE FROM reader_state WHERE id=?", (rid,))


# ---------- 角色状态时间线(时间性事实:双时间线之「事件时间线」) ----------

def list_character_states(pid, name=None):
    if name:
        return q(
            "SELECT * FROM character_states WHERE project_id=? AND name=? ORDER BY chapter_idx, id",
            (pid, name),
        )
    return q("SELECT * FROM character_states WHERE project_id=? ORDER BY chapter_idx, id", (pid,))


def get_character_state(pid, name, chapter_idx):
    return one(
        "SELECT * FROM character_states WHERE project_id=? AND name=? AND chapter_idx=? ORDER BY id DESC LIMIT 1",
        (pid, (name or "").strip(), int(chapter_idx)),
    )


def add_character_state(pid, name, chapter_idx, status):
    """定稿抽取入口:同人同章去重(重定稿覆盖),状态沿革只追加不删改,支持任意时点回查。"""
    name = (name or "").strip()
    status = (status or "").strip()
    if not name or not status:
        return None
    row = get_character_state(pid, name, chapter_idx)
    if row:
        if row["status"] == status:
            return {"action": "noop", "id": row["id"], "before": row, "after": row}
        run("UPDATE character_states SET status=? WHERE id=?", (status, row["id"]))
        after = one("SELECT * FROM character_states WHERE id=?", (row["id"],))
        return {"action": "update", "id": row["id"], "before": row, "after": after}
    sid = run(
        "INSERT INTO character_states(project_id,name,chapter_idx,status) VALUES(?,?,?,?)",
        (pid, name, int(chapter_idx), status),
    )
    after = one("SELECT * FROM character_states WHERE id=?", (sid,))
    return {"action": "create", "id": sid, "before": None, "after": after}


def status_asof(states, name, before_idx, current=""):
    """取角色在第 before_idx 章「当时」的状态:最近一条 chapter_idx < before_idx 的事件;无则回退当前档案。"""
    evs = [e for e in (states or []) if e.get("name") == name and e.get("chapter_idx", 0) < before_idx]
    if not evs:
        return current
    return max(evs, key=lambda e: (e["chapter_idx"], e["id"])).get("status") or current


def delete_character_state(sid):
    run("DELETE FROM character_states WHERE id=?", (sid,))


# ---------- 记忆变更日志(定稿回写的 before/after 审计) ----------

def add_memory_event(pid, chapter_idx, entity, action, before=None, after=None,
                     source="finalize", status="applied", entity_id=None):
    """记录一次记忆变更。before/after 统一存 JSON,便于回放与审计。"""
    def dump(value):
        if value is None:
            return ""
        return json.dumps(value, ensure_ascii=False)

    return run(
        "INSERT INTO memory_events(project_id,chapter_idx,entity,entity_id,action,before,after,source,status) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (pid, chapter_idx, entity, entity_id, action, dump(before), dump(after), source, status),
    )


def list_memory_events(pid, chapter_idx=None, limit=200):
    sql = "SELECT * FROM memory_events WHERE project_id=?"
    params = [pid]
    if chapter_idx is not None:
        sql += " AND chapter_idx=?"
        params.append(int(chapter_idx))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 200), 1000)))
    rows = q(sql, tuple(params))
    for r in rows:
        for k in ("before", "after"):
            if r.get(k):
                try:
                    r[k] = json.loads(r[k])
                except (TypeError, ValueError):
                    pass
    return rows


def add_usage(pid, stage, prompt_chars, completion_chars):
    run(
        "INSERT INTO usage_stats(project_id,stage,prompt_chars,completion_chars) VALUES(?,?,?,?)",
        (pid, stage, int(prompt_chars), int(completion_chars)),
    )


def sum_usage(pid):
    row = one(
        "SELECT COUNT(*) AS calls, "
        "COALESCE(SUM(prompt_chars),0) AS pc, COALESCE(SUM(completion_chars),0) AS cc "
        "FROM usage_stats WHERE project_id=?",
        (pid,),
    )
    return {
        "calls": row["calls"] or 0,
        "prompt_chars": row["pc"] or 0,
        "completion_chars": row["cc"] or 0,
        # 中文场景粗略估算:约 1.6 字符/Token,仅作展示参考
        "approx_tokens": int((row["pc"] + row["cc"]) / 1.6),
    }
