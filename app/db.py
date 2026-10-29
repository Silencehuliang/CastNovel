"""SQLite 存储层:单连接 + 写锁,覆盖项目/角色/章节/伏笔/设置的读写。"""
import json
import sqlite3
import threading

from app.config import DB_PATH

_lock = threading.Lock()
_conn = None

SCHEMA = """
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
  keywords TEXT NOT NULL,
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
"""

# 运行时增量迁移:旧库缺列时补齐
_MIGRATE_COLUMNS = [
    ("projects", "style_ref", "TEXT DEFAULT ''"),
    ("chapters", "gate", "TEXT DEFAULT ''"),
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


# ---------- 角色(故事圣经) ----------

def list_characters(pid):
    return q("SELECT * FROM characters WHERE project_id=? ORDER BY id", (pid,))


def upsert_character(pid, name, role="", profile="", status="", overwrite=True):
    name = (name or "").strip()
    if not name:
        return
    row = one("SELECT id FROM characters WHERE project_id=? AND name=?", (pid, name))
    if row:
        if overwrite:
            sets, params = [], []
            if role:
                sets.append("role=?"); params.append(role)
            if profile:
                sets.append("profile=?"); params.append(profile)
            if status:
                sets.append("status=?"); params.append(status)
            if sets:
                params.append(row["id"])
                run(f"UPDATE characters SET {', '.join(sets)} WHERE id=?", tuple(params))
    else:
        run(
            "INSERT INTO characters(project_id,name,role,profile,status) VALUES(?,?,?,?,?)",
            (pid, name, role, profile, status),
        )


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
        return
    row = one("SELECT * FROM foreshadowing WHERE project_id=? AND title=?", (pid, title))
    if row is None:
        if action == "plant":
            run(
                "INSERT INTO foreshadowing(project_id,title,description,planted_ch,status) "
                "VALUES(?,?,?,?,?)",
                (pid, title, description, chapter_idx, "open"),
            )
        return
    if action == "resolve":
        run(
            "UPDATE foreshadowing SET payoff_ch=?, status='resolved' WHERE id=?",
            (chapter_idx, row["id"]),
        )
    elif description and not row["description"]:
        run("UPDATE foreshadowing SET description=? WHERE id=?", (description, row["id"]))


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
        return
    row = one("SELECT * FROM plotlines WHERE project_id=? AND title=?", (pid, title))
    if row is None:
        if action in ("start", "advance"):
            run(
                "INSERT INTO plotlines(project_id,title,description,start_ch,last_ch,status) "
                "VALUES(?,?,?,?,?,?)",
                (pid, title, description, chapter_idx, chapter_idx, "active"),
            )
        return
    if action == "end":
        run("UPDATE plotlines SET last_ch=?, status='done' WHERE id=?", (chapter_idx, row["id"]))
    else:
        run("UPDATE plotlines SET last_ch=? WHERE id=?", (chapter_idx, row["id"]))
        if description and not row["description"]:
            run("UPDATE plotlines SET description=? WHERE id=?", (description, row["id"]))


def delete_plotline(plid):
    run("DELETE FROM plotlines WHERE id=?", (plid,))


# ---------- Lorebook(关键词触发设定注入) ----------

def list_lore(pid, only_enabled=False):
    sql = "SELECT * FROM lorebook WHERE project_id=?"
    if only_enabled:
        sql += " AND enabled=1"
    return q(sql + " ORDER BY id", (pid,))


def add_lore(pid, keywords, content, note=""):
    return run(
        "INSERT INTO lorebook(project_id,keywords,content,note) VALUES(?,?,?,?)",
        (pid, (keywords or "").strip(), (content or "").strip(), (note or "").strip()),
    )


def update_lore(lid, fields: dict):
    allowed = {k: fields[k] for k in ("keywords", "content", "note", "enabled") if k in fields}
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
    resolve 按 content 匹配未结条目 → 置 done(期待兑现/误认揭穿)。"""
    content = (content or "").strip()
    kind = kind if kind in READER_KINDS else "known"
    if not content:
        return
    row = one(
        "SELECT id, status FROM reader_state WHERE project_id=? AND kind=? AND content=?",
        (pid, kind, content),
    )
    if action == "resolve":
        if row and row["status"] == "open":
            run("UPDATE reader_state SET status='done' WHERE id=?", (row["id"],))
            return
        like = one(
            "SELECT id, status FROM reader_state WHERE project_id=? AND kind=? AND status='open' "
            "AND (? LIKE '%'||content||'%' OR content LIKE '%'||?||'%') LIMIT 1",
            (pid, kind, content, content),
        )
        if like:
            run("UPDATE reader_state SET status='done' WHERE id=?", (like["id"],))
        return
    if row:
        return
    run(
        "INSERT INTO reader_state(project_id,kind,content,chapter_idx) VALUES(?,?,?,?)",
        (pid, kind, content, chapter_idx),
    )


def update_reader(rid, fields: dict):
    allowed = {k: fields[k] for k in ("kind", "content", "status", "chapter_idx") if k in fields}
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)
    run(f"UPDATE reader_state SET {sets} WHERE id=?", (*allowed.values(), rid))


def delete_reader(rid):
    run("DELETE FROM reader_state WHERE id=?", (rid,))


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
