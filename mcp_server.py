"""CastNovel MCP Server —— 把「铸书」的小说创作流水线作为 MCP 工具暴露给 CodeBuddy / WorkBuddy。

由 MCP 客户端以 stdio 方式拉起，无需先启动 Web 服务：
本进程直接调用 app.stages，与 Web 界面共享同一个 SQLite（WAL 模式，可并发读写）。

stdio 传输下 stdout 是协议通道，任何调试输出一律走 stderr。
配置方式见项目根 mcp.json 与 README「接入 CodeBuddy / WorkBuddy」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from mcp.server.fastmcp import FastMCP

from app import db, export, publish, stages
from app.config import load_settings

db.init()

mcp = FastMCP("castnovel", log_level="WARNING")


# ---------- 内部工具 ----------

def _log(msg: str) -> None:
    print(f"[castnovel-mcp] {msg}", file=sys.stderr, flush=True)


def _d(row) -> dict:
    """sqlite3.Row → dict(Row 不支持 .get())。"""
    return dict(row) if row is not None else {}


def _clip(text, n: int, hint: str = "") -> str:
    text = text or ""
    if n <= 0 or len(text) <= n:
        return text
    tail = f"…(共 {len(text)} 字" + (f"，{hint}" if hint else "") + ")"
    return f"{text[:n]}{tail}"


def _need_project(pid: int):
    project = db.get_project(pid)
    if not project:
        raise ValueError(f"作品 id={pid} 不存在，可用 list_projects 查看现有作品")
    return _d(project)


def _need_chapter(pid: int, idx: int):
    for ch in db.list_chapters(pid):
        if ch["idx"] == idx:
            return _d(ch)
    total = len(db.list_chapters(pid, with_content=False))
    raise ValueError(f"第 {idx} 章不存在（当前共 {total} 章），可用 get_project_overview 查看章节列表")


def _gate_brief(gate: dict) -> str:
    if not gate:
        return ""
    hits = gate.get("char_hits") or []
    missed = gate.get("char_missed") or []
    head = (f"门禁：字数 {gate.get('words')}/{gate.get('target')}"
            f"，角色覆盖 {len(hits)}/{len(hits) + len(missed)}"
            f"，近三章最大重复率 {float(gate.get('overlap') or 0):.0%}")
    issues = gate.get("issues") or []
    if not issues:
        return head + "，无告警。"
    lines = [head + "，告警："]
    for it in issues[:8]:
        lines.append(f"- [{it.get('level')}] {it.get('type')}：{it.get('msg')}")
    return "\n".join(lines)


def _chapter_line(ch: dict) -> str:
    ch = _d(ch)
    flags = []
    if (ch.get("content") or "").strip():
        flags.append(f"正文{len(ch['content'])}字")
    if (ch.get("beats") or "").strip():
        flags.append("有章纲")
    if (ch.get("review") or "").strip():
        flags.append("已审校")
    status = ch.get("status") or "-"
    return f"  - 第{ch['idx']}章《{ch.get('title') or ''}》状态:{status}" + (
        f"（{'，'.join(flags)}）" if flags else "")


# ---------- 作品管理 ----------

@mcp.tool()
def list_projects() -> str:
    """列出所有小说作品（id、书名、题材、一句话梗概、更新时间）。第一步通常调用它。"""
    rows = [_d(r) for r in db.list_projects()]
    if not rows:
        return "暂无作品。可用 create_project 新建一部。"
    lines = [f"- id={r['id']}《{r['title']}》题材:{r['genre'] or '未设'} 更新:{r['updated_at']}"
             + (f" 梗概:{_clip(r['premise'], 60)}" if r.get("premise") else "")
             for r in rows]
    return "作品列表：\n" + "\n".join(lines)


@mcp.tool()
def create_project(title: str, genre: str = "", premise: str = "", style: str = "") -> str:
    """新建一部小说作品。

    Args:
        title: 书名（必填）
        genre: 题材，如「东方玄幻」「都市悬疑」
        premise: 一句话梗概，越具体后续生成质量越高
        style: 文风要求，如「冷峻克制、短句为主」
    """
    if not title.strip():
        return "错误：书名不能为空"
    pid = db.run(
        "INSERT INTO projects(title,genre,premise,style) VALUES(?,?,?,?)",
        (title.strip(), genre.strip(), premise.strip(), style.strip()),
    )
    return f"已创建作品 id={pid}《{title.strip()}》。建议下一步：generate_worldview(project_id={pid})。"


@mcp.tool()
def get_project_overview(project_id: int, worldview_chars: int = 800) -> str:
    """查看作品全貌：设定、世界观（默认截断 800 字）、角色档案、章节目录与状态、伏笔、情节线。

    写某一章之前先调一次，可以拿到章节编号（chapter_idx）和已有人物设定。
    """
    project = _need_project(project_id)
    characters = db.list_characters(project_id)
    chapters = db.list_chapters(project_id, with_content=False)
    fores = db.list_foreshadowing(project_id)
    plots = db.list_plotlines(project_id)

    out = [f"《{project['title']}》id={project_id}",
           f"题材:{project['genre'] or '未设'}",
           f"梗概:{project['premise'] or '未填'}",
           f"文风:{project['style'] or '未设'}",
           "",
           f"世界观（{len(project.get('worldview') or '')} 字）:",
           _clip(project.get("worldview") or "（尚未生成，可用 generate_worldview）", worldview_chars,
                 "调大 worldview_chars 可看更多"),
           "",
           f"角色（{len(characters)}）:"]
    out += [f"  - {c['name']}（{c['role'] or '未定'}）{(c['status'] or '')[:80]}" for c in characters] or ["  （暂无，可用 generate_characters）"]
    out += ["", f"章节（{len(chapters)}）:"]
    out += [_chapter_line(ch) for ch in chapters] or ["  （暂无，可用 generate_outline）"]
    if fores:
        out += ["", f"伏笔（{len(fores)}）:"]
        out += [f"  - [{f['status']}] {f['title']}" for f in fores[:20]]
    if plots:
        out += ["", f"情节线（{len(plots)}）:"]
        out += [f"  - [{p['status']}] {p['title']}" for p in plots[:20]]
    reader = db.list_reader(project_id, only_open=True)
    if reader:
        cnt = {"known": 0, "expect": 0, "mislead": 0}
        for r in reader:
            cnt[r["kind"] if r["kind"] in cnt else "known"] += 1
        out += ["", f"读者认知(进行中):已知 {cnt['known']} / 期待 {cnt['expect']} / 误认 {cnt['mislead']}"
                " —— 生成时自动注入;详情用 get_reader_state"]
    return "\n".join(out)


# ---------- 设定层生成 ----------

@mcp.tool()
def generate_worldview(project_id: int, preview_chars: int = 800) -> str:
    """生成世界观设定并保存到作品（强模型，耗时约 30~90 秒）。会覆盖原有世界观。"""
    try:
        text = stages.gen_worldview(project_id)
    except Exception as e:
        return f"错误：{e}"
    return f"世界观已生成并保存（{len(text)} 字）。下一步：generate_characters。\n\n{_clip(text, preview_chars)}"


@mcp.tool()
def generate_characters(project_id: int) -> str:
    """按世界观生成角色档案并保存（会替换现有角色表）。需先有世界观。"""
    try:
        chars = stages.gen_characters(project_id)
    except Exception as e:
        return f"错误：{e}"
    lines = [f"已生成 {len(chars)} 个角色："]
    for c in chars[:30]:
        if isinstance(c, dict):
            lines.append(f"  - {c.get('name', '?')}（{c.get('role', '')}）{_clip(str(c.get('profile', '')), 60)}")
    return "\n".join(lines)


@mcp.tool()
def generate_outline(project_id: int, count: int = 30, replace: bool = False) -> str:
    """生成全书章纲（卷/章标题/概要/章末钩子）并保存。

    Args:
        count: 章节数量，建议 20~60
        replace: 已有章节时是否覆盖；章节里已有正文时会被拒绝，需先导出备份
    """
    try:
        norm = stages.gen_outline(project_id, count=max(3, min(count, 200)), replace=replace)
    except Exception as e:
        return f"错误：{e}"
    lines = [f"已生成 {len(norm)} 章大纲："]
    for ch in norm[:60]:
        lines.append(f"  - 第{ch['idx']}章《{ch['title']}》{_clip(ch.get('summary', ''), 50)}")
    return "\n".join(lines)


# ---------- 章节层生成 ----------

@mcp.tool()
def generate_beats(project_id: int, chapter_idx: int) -> str:
    """为指定章生成场景级章纲（节拍），并保存。写正文前应先做这一步。"""
    ch = _need_chapter(project_id, chapter_idx)
    try:
        beats = stages.gen_beats(project_id, ch["id"])
    except Exception as e:
        return f"错误：{e}"
    return f"第{chapter_idx}章章纲已生成并保存：\n{beats}"


@mcp.tool()
def generate_draft(project_id: int, chapter_idx: int, preview_chars: int = 600) -> str:
    """按章纲生成该章正文并保存（强模型，约 1~3 分钟），同时跑一致性门禁。

    返回门禁结果与正文预览；需要完整正文请随后调用 read_chapter。
    """
    ch = _need_chapter(project_id, chapter_idx)
    try:
        out = stages.gen_draft(project_id, ch["id"])
    except Exception as e:
        return f"错误：{e}"
    gate = out.get("_gate") or {}
    return (f"第{chapter_idx}章正文已生成并保存（{len(out.get('content') or '')} 字）。\n"
            f"{_gate_brief(gate)}\n\n预览：\n{_clip(out.get('content') or '', preview_chars, '全文请用 read_chapter')}")


@mcp.tool()
def polish_chapter(project_id: int, chapter_idx: int, adopt: bool = False, preview_chars: int = 800) -> str:
    """去 AI 味润色本章（不覆盖原稿，仅返回新稿）。

    Args:
        adopt: 为 True 时才把润色稿写入章节（写前自动快照旧稿，可回滚）；默认 False 只预览
    """
    ch = _need_chapter(project_id, chapter_idx)
    try:
        text = stages.polish_chapter(project_id, ch["id"])
    except Exception as e:
        return f"错误：{e}"
    if adopt:
        stages.adopt_content(project_id, ch["id"], text, "MCP 采用润色稿前自动保存")
        return f"润色稿已采用（{len(text)} 字），旧稿已存入版本历史。\n\n{_clip(text, preview_chars)}"
    return (f"润色稿已生成（{len(text)} 字，未写入章节）。确认后可用 adopt=True 重跑，"
            f"或 update_chapter 写入。\n\n{_clip(text, preview_chars)}")


@mcp.tool()
def review_chapter(project_id: int, chapter_idx: int) -> str:
    """审校本章：返回总分、四维评分与带定位的问题清单，结果同时保存到章节。"""
    ch = _need_chapter(project_id, chapter_idx)
    try:
        review = stages.review_chapter(project_id, ch["id"])
    except Exception as e:
        return f"错误：{e}"
    if not isinstance(review, dict):
        return f"审校完成：{review}"
    lines = [f"第{chapter_idx}章审校总分：{review.get('overall', '?')}"]
    for k, label in (("plot", "情节"), ("character", "人物"), ("prose", "文笔"), ("consistency", "一致性")):
        if k in review:
            lines.append(f"  - {label}：{review[k]}")
    issues = review.get("issues") or []
    if issues:
        lines.append("问题清单：")
        for it in issues[:15]:
            if isinstance(it, dict):
                lines.append(f"  - [{it.get('severity', '-')}] {it.get('problem') or it.get('issue') or it}"
                             f"（建议：{it.get('suggestion', '-')}）")
            else:
                lines.append(f"  - {it}")
    return "\n".join(lines)


@mcp.tool()
def finalize_chapter(project_id: int, chapter_idx: int) -> str:
    """定稿本章：生成摘要、抽取人物状态变化/伏笔/情节线并回写故事圣经（快模型）。

    只在本章内容已满意时调用，定稿后下一章会自动携带最新状态。
    """
    ch = _need_chapter(project_id, chapter_idx)
    try:
        result = stages.finalize_chapter(project_id, ch["id"])
    except Exception as e:
        return f"错误：{e}"
    state = result.get("state") or {}
    return (f"第{chapter_idx}章已定稿。\n摘要：{_clip(result.get('summary') or '', 300)}\n"
            f"状态回写：角色 {len(state.get('characters') or [])} 项、"
            f"伏笔 {len(state.get('foreshadowing') or [])} 项、"
            f"情节线 {len(state.get('plotlines') or [])} 项。")


# ---------- 独立评审（供 CodeBuddy / WorkBuddy 的模型盲审） ----------

_RUBRIC = """你是严格的网文审校编辑,与本章作者无任何关系,请独立盲审,输出 JSON:
{"overall": 0-100总分,
 "dimensions": {"情节推进": 0-100, "人物一致性": 0-100, "文笔表现": 0-100, "节奏张力": 0-100},
 "issues": [{"type": "逻辑/重复/人设崩坏/节奏/文笔/衔接/对话密度", "severity": "高/中/低", "desc": "具体指出位置与问题"}],
 "suggestions": ["可执行的修改建议"]}

重点检查:
1. 对话密度:连续对白超过 5 段、台词占全文字数 38% 以上,必须作为「对话密度」问题列出并给 severity。
2. 与前情是否矛盾;人物言行是否符合档案;是否复述已知信息。
3. 结尾钩子是否有力;叙事与对白是否穿插;字数是否达标(约 3000 字)。
4. 评分要严格:90 分以上应当是几乎无可挑剔;发现 3 个以上中等问题,总分不应超过 85。"""


@mcp.tool()
def export_review_prompt(project_id: int, chapter_idx: int) -> str:
    """导出本章的独立盲审提示词（评分卡 + 前情设定 + 正文全文）。

    把返回内容直接交给任意大模型（或在 CodeBuddy 里让 agent 按它执行）即可完成盲审。
    审完后把模型输出的 JSON 用 submit_review 写回。
    """
    project = _need_project(project_id)
    ch = _need_chapter(project_id, chapter_idx)
    _, _, _, context = stages._ctx_parts(project_id, ch["id"], load_settings(db))
    content = ch.get("content") or ""
    if not content.strip():
        return f"错误：第 {chapter_idx} 章尚无正文。"
    return (f"{_RUBRIC}\n\n【前情与设定】\n{context}\n\n"
            f"【第 {chapter_idx} 章《{ch.get('title') or ''}》正文】\n{content}")


@mcp.tool()
def submit_review(project_id: int, chapter_idx: int, review_json: str, reviewer: str = "independent") -> str:
    """把独立盲审结果写回章节（与作者自评分并存，互不覆盖）。

    Args:
        review_json: 盲审模型输出的 JSON 字符串，需含 overall/dimensions/issues/suggestions
        reviewer: 评审者标识，如「codebuddy-glm」「codebuddy-deepseek」
    """
    ch = _need_chapter(project_id, chapter_idx)
    try:
        review = json.loads(review_json)
    except Exception as e:
        return f"错误：review_json 不是合法 JSON（{e}）"
    if not isinstance(review, dict) or "overall" not in review:
        return "错误：review_json 缺少 overall 字段"
    review["reviewer"] = reviewer
    try:
        old = json.loads(ch.get("review") or "{}")
    except Exception:
        old = {}
    old["independent"] = review
    db.update_chapter(ch["id"], {"review": json.dumps(old, ensure_ascii=False)})
    mine = old.get("overall")
    ind = review.get("overall")
    gap = (mine - ind) if isinstance(mine, (int, float)) and isinstance(ind, (int, float)) else None
    gap_txt = f"，与作者自评（{mine}）差 {gap:+d} 分" if gap is not None else ""
    hi = [i for i in (review.get("issues") or []) if isinstance(i, dict) and i.get("severity") == "高"]
    out = [f"第{chapter_idx}章独立评审已写入（评审者：{reviewer}）。",
           f"盲审总分：{ind}{gap_txt}",
           f"四维：{review.get('dimensions')}"]
    if hi:
        out.append("高危问题：")
        for it in hi[:8]:
            out.append(f"  - [{it.get('type', '?')}] {it.get('desc', '')}")
    out.append("定稿门槛以盲审分为准（≥85）。")
    return "\n".join(out)


# ---------- 读取与编辑 ----------

@mcp.tool()
def read_chapter(project_id: int, chapter_idx: int, with_content: bool = True) -> str:
    """读取单章详情：标题、章纲、概要、状态、正文全文。"""
    ch = _need_chapter(project_id, chapter_idx)
    out = [f"第{ch['idx']}章《{ch.get('title') or ''}》状态:{ch.get('status')}",
           f"章纲：\n{ch.get('beats') or '（空）'}",
           f"概要：{ch.get('summary') or '（空）'}"]
    if with_content:
        out.append(f"正文（{len(ch.get('content') or '')} 字）：\n{ch.get('content') or '（尚未写作）'}")
    if ch.get("review"):
        out.append(f"审校结果：{ch['review']}")
    return "\n\n".join(out)


@mcp.tool()
def update_chapter(project_id: int, chapter_idx: int, title: str | None = None,
                   beats: str | None = None, summary: str | None = None,
                   content: str | None = None) -> str:
    """手工编辑某章：可改标题、章纲、概要、正文（只传需要改的字段）。"""
    ch = _need_chapter(project_id, chapter_idx)
    fields = {k: v for k, v in (("title", title), ("beats", beats), ("summary", summary), ("content", content))
              if v is not None}
    if not fields:
        return "未提供任何要修改的字段"
    if content is not None and (ch.get("content") or "").strip() and content != ch["content"]:
        db.add_version(ch["id"], "MCP 手工改写前自动保存")
    db.update_chapter(ch["id"], fields)
    db.touch_project(project_id)
    return f"第{chapter_idx}章已更新：{', '.join(fields)}"


@mcp.tool()
def export_novel(project_id: int, fmt: str = "md") -> str:
    """导出全书到项目 exports/ 目录，返回文件绝对路径。

    Args:
        fmt: md / txt / docx / epub
    """
    project = _need_project(project_id)
    chapters = db.list_chapters(project_id, with_content=True)
    if not any((c.get("content") or "").strip() for c in chapters):
        return "还没有任何正文，无法导出。"
    fmt = (fmt or "md").lower().lstrip(".")
    out_dir = BASE_DIR / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if fmt == "docx":
            data, name, _ = export.export_docx(project, chapters)
            path = out_dir / name
            path.write_bytes(data)
        elif fmt == "epub":
            data, name, _ = export.export_epub(project, chapters)
            path = out_dir / name
            path.write_bytes(data)
        else:
            text, name, _ = export.export_text(project, chapters, fmt=fmt)
            path = out_dir / name
            path.write_text(text, encoding="utf-8")
    except Exception as e:
        return f"错误：{e}"
    return f"已导出：{path}"


# ---------- Lorebook(关键词触发设定) ----------

@mcp.tool()
def list_lore_entries(project_id: int) -> str:
    """列出作品的 Lorebook 条目(关键词触发设定,命中即注入生成上下文)。"""
    _need_project(project_id)
    rows = db.list_lore(project_id)
    if not rows:
        return "暂无 Lorebook 条目。可用 upsert_lore_entry 添加,例如:关键词「玉佩」→「玉佩在雷雨夜发烫,是身世信物」。"
    lines = [f"- id={r['id']} [{'启用' if r['enabled'] else '停用'}] 触发词:{r['keywords']} → {_clip(r['content'], 80)}"
             for r in rows]
    return f"Lorebook({len(rows)} 条):\n" + "\n".join(lines)


@mcp.tool()
def upsert_lore_entry(project_id: int, keywords: str, content: str, note: str = "",
                      enabled: bool = True, secondary: str = "", logic: str = "and_any",
                      constant: bool = False, priority: int = 0) -> str:
    """添加 Lorebook 条目(六字段):正文或章纲命中主键时,该条内容自动注入生成上下文。

    Args:
        keywords: 触发主键,逗号分隔,如「玉佩, 青黛」;常驻条目可留空
        content: 命中后注入的设定内容(人物细节、规则、事实),保持简短
        note: 备注,可空
        enabled: 是否启用
        secondary: 次级键,逗号分隔;配合 logic 二次过滤
        logic: and_any / and_all / not_any / not_all,默认 and_any
        constant: 常驻条目,不经命中直接注入
        priority: 注入顺序,大者优先,默认 0
    """
    _need_project(project_id)
    if not content.strip() or (not keywords.strip() and not constant):
        return "错误:content 不能为空;非常驻条目 keywords 也不能为空"
    if logic not in ("and_any", "and_all", "not_any", "not_all"):
        logic = "and_any"
    lid = db.add_lore(project_id, keywords, content, note, secondary=secondary, logic=logic,
                      constant=constant, priority=priority, enabled=enabled)
    return (f"Lorebook 条目已添加 id={lid}(触发词:{keywords.strip() or '常驻'})。"
            f"之后生成章节时,只要章纲/前情命中关键词就会自动注入。")


@mcp.tool()
def delete_lore_entry(project_id: int, lore_id: int) -> str:
    """删除一条 Lorebook 条目。"""
    db.delete_lore(lore_id)
    return f"已删除 Lorebook 条目 id={lore_id}。"


# ---------- 影响分析 + 高级生成 ----------

@mcp.tool()
def search_impact(project_id: int, query: str) -> str:
    """设定变更影响分析:按关键词(角色名/地点/设定词,逗号分隔)反查受影响章节,零 token。

    改了世界观/角色/时间线之后调用,评估哪些章节需要回改或重写。
    """
    try:
        r = stages.find_impact(project_id, query)
    except Exception as e:
        return f"错误:{e}"
    rows = r["affected"]
    if not rows:
        return f"关键词 {r['keywords']} 未命中任何章节。"
    lines = [f"关键词 {r['keywords']} 命中 {len(rows)} 章(命中角色:{'、'.join(r['characters_matched']) or '无'}):"]
    for x in rows[:40]:
        lines.append(f"  - 第{x['idx']}章《{x['title']}》[{x['status']}] 共{x['hits']}处"
                     f"(概要{x['n_summary']}/章纲{x['n_beats']}/正文{x['n_content']})")
    return "\n".join(lines)


@mcp.tool()
def generate_draft_best(project_id: int, chapter_idx: int, n: int = 2) -> str:
    """候选拉样+重排:生成 n 份(2~4)候选正文,门禁打分 + 主编重排,择优写入。

    落选候选存入版本历史(read_chapter 前可先查 versions)。比 generate_draft 慢约 n 倍,
    但能显著提升单章质量下限,适合关键章节。
    """
    ch = _need_chapter(project_id, chapter_idx)
    try:
        out = stages.gen_draft_best(project_id, ch["id"], n=max(2, min(n, 4)))
    except Exception as e:
        return f"错误:{e}"
    rank = out.get("_rank") or {}
    gate = out.get("_gate") or {}
    scores = rank.get("scores") or {}
    score_txt = "  ".join(f"{k}={v}" for k, v in scores.items()) or "(未给出)"
    return (f"第{chapter_idx}章已写入最优候选{rank.get('best')}(共 {rank.get('labels') and len(rank['labels'])} 选 1,{len(out.get('content') or '')} 字)。\n"
            f"候选评分:{score_txt}\n理由:{rank.get('reason') or '-'}\n{_gate_brief(gate)}\n"
            f"落选稿在版本历史中,可用 read_chapter 查看正文。")


@mcp.tool()
def deai_rewrite_chapter(project_id: int, chapter_idx: int, adopt: bool = False, preview_chars: int = 800) -> str:
    """两步去 AI 味:第一步程序化 span 级定位(零 token),第二步模型按定位清单逐条改写。

    Args:
        adopt: 为 True 时把改写稿写入章节(写前自动快照,可回滚);默认 False 只预览
    """
    ch = _need_chapter(project_id, chapter_idx)
    try:
        r = stages.deai_rewrite(project_id, ch["id"])
    except Exception as e:
        return f"错误:{e}"
    if not r.get("spans"):
        return r.get("note") or "未检测到 AI 味命中。"
    if adopt:
        stages.adopt_content(project_id, ch["id"], r["text"], "MCP 采用两步去AI味稿前自动保存")
        return f"改写稿已采用({len(r['text'])} 字),旧稿已快照。{r.get('note', '')}\n\n{_clip(r['text'], preview_chars)}"
    return (f"命中 {len(r['spans'])} 处:{'; '.join(s['span'] for s in r['spans'][:6])}…\n"
            f"{r.get('note', '')}\n确认后可用 adopt=True 重跑,或 update_chapter 手工写入。\n\n{_clip(r['text'], preview_chars)}")


# ---------- 读者认知 + 门禁 ----------

@mcp.tool()
def get_reader_state(project_id: int, only_open: bool = True) -> str:
    """查看读者认知状态(读者已知/期待/误认,与角色已知、作者真相分离)。

    定稿章节时自动抽取;写作时自动注入上下文:已知防复述、期待管兑现、误认控反转时机。
    """
    _need_project(project_id)
    rows = db.list_reader(project_id, only_open=only_open)
    if not rows:
        return "暂无读者认知记录(定稿章节后自动生成,也可手工登记)。"
    labels = {"known": "已知", "expect": "期待", "mislead": "误认"}
    lines = []
    for kind in ("known", "expect", "mislead"):
        items = [r for r in rows if r["kind"] == kind]
        if items:
            lines.append(f"{labels[kind]}({len(items)}):")
            lines += [f"  - {r['content']}" + (f"(第{r['chapter_idx']}章起)" if r["chapter_idx"] else "") for r in items[-10:]]
    return "\n".join(lines)


@mcp.tool()
def get_character_history(project_id: int, name: str) -> str:
    """查看角色状态沿革(时间性事实):定稿时每次状态变化作为一条事件记录(角色+章节+状态),
    支持按章回放——「第 N 章时这个角色是什么状态」。乱序写作/改稿不串味。

    Args:
        name: 角色名(与 characters 表一致)
    """
    _need_project(project_id)
    if not name.strip():
        return "错误:请提供角色名"
    rows = db.list_character_states(project_id, name=name.strip())
    if not rows:
        return f"《{name.strip()}》暂无状态记录(定稿章节后自动抽取,或手工登记)。"
    lines = [f"《{name.strip()}》状态沿革({len(rows)} 条事件):"]
    lines += [f"  - 第{r['chapter_idx']}章起:{r['status']}" for r in rows]
    return "\n".join(lines)


@mcp.tool()
def export_lorebook(project_id: int) -> str:
    """导出为 SillyTavern World Info JSON(可直接导入 ST / NovelAI 生态复用现成 worldbook)。"""
    _need_project(project_id)
    rows = db.list_lore(project_id)
    if not rows:
        return "暂无 Lorebook 条目,无需导出。"
    return json.dumps(stages.export_lore(project_id), ensure_ascii=False)


@mcp.tool()
def import_lorebook(project_id: int, data: str) -> str:
    """导入 SillyTavern World Info JSON(merge,不清空现有条目)。

    Args:
        data: ST 格式 JSON 字符串({entries:{...}}),或 [{keywords,content,...}] 数组,或单条对象
    """
    _need_project(project_id)
    try:
        payload = json.loads(data)
    except Exception as e:
        return f"错误:不是合法 JSON({e})"
    try:
        n = stages.import_lore(project_id, payload)
    except Exception as e:
        return f"错误:{e}"
    return f"已导入 {n} 条 Lorebook 条目(关键词命中后自动注入上下文)。"


@mcp.tool()
def edit_reader_state(state_id: int, action: str = "resolve", content: str = "") -> str:
    """手工修正读者认知条目:action=resolve(标记已兑现/已揭穿)、reopen、delete,或 content 更新文本。"""
    row = db.one("SELECT * FROM reader_state WHERE id=?", (state_id,))
    if not row:
        return f"错误:id={state_id} 的读者认知条目不存在(可用 get_reader_state 查看)"
    if action == "delete":
        db.delete_reader(state_id)
        return f"已删除条目「{row['content'][:30]}」"
    if action == "resolve":
        db.update_reader(state_id, {"status": "done"})
        return f"已了结:「{row['content'][:30]}」"
    if action == "reopen":
        db.update_reader(state_id, {"status": "open"})
        return f"已重新打开:「{row['content'][:30]}」"
    if content.strip():
        db.update_reader(state_id, {"content": content.strip()})
        return f"已更新:「{row['content'][:30]}」→「{content.strip()[:30]}」"
    return "未识别的 action(可用:resolve/reopen/delete)或未提供 content"


@mcp.tool()
def run_gate_check(project_id: int, chapter_idx: int) -> str:
    """对章节当前正文重跑一致性门禁(字数/角色覆盖/重复率/AI 味,零 token)。

    用 update_chapter 改完正文后调用,刷新门禁徽章并获取告警清单。
    """
    ch = _need_chapter(project_id, chapter_idx)
    try:
        gate = stages.recompute_gate(project_id, ch["id"])
    except Exception as e:
        return f"错误:{e}"
    return f"第{chapter_idx}章门禁重算完成。\n{_gate_brief(gate)}"


@mcp.tool()
def get_publish_status(project_id: int) -> str:
    """查看平台发布状态:绑定书籍、已映射章节数、逐章同步计划(新建/更新/跳过)。

    改完稿后先用它确认哪些章需要推送;真实同步用 sync_to_platform(dry=True) 预演。
    """
    _need_project(project_id)
    ov = publish.overview(project_id)
    lines = []
    for p in ov["platforms"]:
        head = f"{p['label']}({p['key']})"
        if not p["bound"]:
            lines.append(f"- {head}:未绑定(用 set_publish_binding 设置书籍 ID)")
            continue
        plan = p["plan"]
        cnt = {a: sum(1 for i in plan if i["action"] == a) for a in ("create", "update", "skip")}
        lines.append(f"- {head}:book_id={p['book_id']},映射 {len(p['map'])} 章,"
                     f"计划 新建 {cnt['create']} / 更新 {cnt['update']} / 跳过 {cnt['skip']}")
        todo = [i for i in plan if i["action"] != "skip"]
        for i in todo[:15]:
            lines.append(f"    第{i['idx']}章《{i['title']}》{i['action']} —— {i['reason']}")
        if len(todo) > 15:
            lines.append(f"    …另有 {len(todo) - 15} 章待处理")
    return "平台发布状态:\n" + "\n".join(lines)


@mcp.tool()
def set_publish_binding(project_id: int, book_id: str, platform: str = "fanqie") -> str:
    """绑定平台书籍 ID(番茄作家后台 URL 中的数字)。绑定后才能拉映射与同步。"""
    _need_project(project_id)
    try:
        publish.set_binding(project_id, platform, book_id)
    except Exception as e:
        return f"错误:{e}"
    return f"已绑定 {platform} 书籍 {book_id}。建议下一步:pull_publish_mapping(project_id={project_id})。"


@mcp.tool()
def pull_publish_mapping(project_id: int, platform: str = "fanqie", with_ids: bool = True) -> str:
    """拉取平台章节列表/章节 ID 写入本地映射(只读,不改平台内容)。

    需要本机 ego-browser 已登录平台,耗时约 1~3 分钟;首次同步前必须先拉一次映射,
    否则本地会把平台已发布的章节当作新章节重复上传。
    """
    _need_project(project_id)
    try:
        r = publish.pull_mapping(project_id, platform, with_ids=with_ids)
    except Exception as e:
        return f"错误:{e}"
    out = (f"映射已更新:{platform} 平台 {r['platform_rows']} 章,"
           f"匹配本地 {r['matched']} 章")
    if r["unmatched"]:
        out += f";平台有而本地没有的章:{r['unmatched']}"
    if r.get("warnings"):
        out += "\n警告:\n" + "\n".join(f"  - {w}" for w in r["warnings"])
    return out


@mcp.tool()
def sync_to_platform(project_id: int, lo: int, hi: int = 0, platform: str = "fanqie",
                     force: bool = False, dry: bool = False) -> str:
    """把某一章(hi 省略即单章)或 [lo, hi] 范围同步到平台。

    无映射→新建章节,本地内容有改动→覆盖平台章节,内容未变→跳过。
    dry=True 只预演:打开编辑页核对标题/字数,不提交。会驱动本机 ego-browser
    (需已登录平台),耗时随章数增长;提交时「是否使用 AI」固定选「否」。
    """
    _need_project(project_id)
    lo = int(lo)
    hi = int(hi or lo)
    if lo < 1:
        return "错误:lo 必须 ≥ 1"
    if hi < lo:
        lo, hi = hi, lo
    try:
        r = publish.sync_chapters(project_id, platform, lo, hi, force=force, dry=dry)
    except Exception as e:
        return f"错误:{e}"
    if r.get("dry"):
        lines = [f"预演第 {lo}-{hi} 章(未提交):"]
        for i in r["items"]:
            mark = "✓" if i.get("ok") else "✗"
            extra = ""
            if i.get("error"):
                extra = f" —— {i['error']}"
            elif i.get("dry"):
                extra = f" —— {i['dry']}"
            lines.append(f"  {mark} 第{i['idx']}章《{i['title']}》{i['action']}{extra}")
        return "\n".join(lines)
    out = (f"同步完成:共 {r['done']} 章(新建 {r['created']} / 更新 {r['updated']}),"
           f"跳过 {r['skipped']} 章,失败 {r['failed']} 章")
    for err in r["errors"]:
        out += f"\n  - 第{err['idx']}章:{err['error']}"
    return out


if __name__ == "__main__":
    _log(f"CastNovel MCP Server 已启动,项目根:{BASE_DIR}")
    mcp.run()
