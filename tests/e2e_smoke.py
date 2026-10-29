"""端到端冒烟测试:mock 模式跑通 设定→角色→大纲→章纲→正文→审校→修订→定稿→导出→导入→用量,
以及 v0.3 新特性(Lorebook / 候选重排 / 两步去AI味 / 影响反查 / 级联 / 向量检索 / 供应商预设)。
用法(务必带 CASTNOVEL_FORCE_MOCK,强制离线、不消耗真实 API 额度):
  CASTNOVEL_FORCE_MOCK=1 python main.py 8901 &
  python tests/e2e_smoke.py http://127.0.0.1:8901
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8901"


def req(method, path, body=None):
    data = json.dumps(body or {}).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r) as resp:
        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct:
            return parse_sse(resp)
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def parse_sse(resp):
    """返回 (拼接的过程文本, done 事件 result)。"""
    buf, deltas, result = b"", [], None
    while True:
        chunk = resp.read(1)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            line, buf = buf.split(b"\n\n", 1)
            s = line.decode("utf-8").strip()
            if s.startswith("data:"):
                evt = json.loads(s[5:].strip())
                if evt["type"] == "delta":
                    deltas.append(evt["text"])
                elif evt["type"] == "done":
                    result = evt.get("result")
                elif evt["type"] == "error":
                    raise RuntimeError("SSE error: " + evt["message"])
    return "".join(deltas), result


def step(name, fn):
    try:
        out = fn()
        print(f"[OK] {name}" + (f" -> {out}" if out is not None else ""))
        return out
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        sys.exit(1)


pid = step("创建项目", lambda: req("POST", "/api/projects", {
    "title": "长夜行者", "genre": "都市玄幻", "premise": "末法时代,守夜人后裔重启修行",
    "style": "节奏明快,画面感强",
})["id"])

_, bundle = step("生成世界观(SSE)", lambda: req("POST", f"/api/projects/{pid}/generate/worldview"))
assert bundle is None or True
p = req("GET", f"/api/projects/{pid}")
assert len(p["project"]["worldview"]) > 50, "世界观为空"
step("世界观已保存", lambda: f"{len(p['project']['worldview'])} 字")

_, b2 = step("生成角色", lambda: req("POST", f"/api/projects/{pid}/generate/characters"))
assert b2 and len(b2["characters"]) > 0
step("角色数量", lambda: len(b2["characters"]))

_, b3 = step("生成大纲(3章)", lambda: req("POST", f"/api/projects/{pid}/generate/outline", {"count": 3, "replace": False}))
assert b3 and len(b3["chapters"]) == 3
step("大纲章数", lambda: len(b3["chapters"]))

cid = b3["chapters"][0]["id"]
_, ch = step("生成第1章章纲", lambda: req("POST", f"/api/projects/{pid}/chapters/{cid}/beats"))
assert ch and "节拍" not in ch["beats"] and len(ch["beats"]) > 30, ch

_, ch = step("生成第1章正文", lambda: req("POST", f"/api/projects/{pid}/chapters/{cid}/draft"))
assert ch and len(ch["content"]) > 500, f"正文过短 {len(ch.get('content',''))}"
step("正文字数", lambda: len(ch["content"]))
assert ch.get("gate"), "门禁报告缺失"
gate = json.loads(ch["gate"]) if isinstance(ch["gate"], str) else ch["gate"]
assert gate["words"] > 0
step("门禁检查", lambda: f"{gate['words']}/{gate['target']}字, 人物命中{len(gate['char_hits'])}")

ctx = step("上下文预览", lambda: req("GET", f"/api/projects/{pid}/chapters/{cid}/context"))
assert "世界观" in ctx["context"] or "写作要求" in ctx["context"]

_, review = step("审校第1章", lambda: req("POST", f"/api/projects/{pid}/chapters/{cid}/review"))
assert review and "overall" in review, review
step("审校得分", lambda: review.get("overall"))

_, rv = step("按意见修订(返回新稿)", lambda: req("POST", f"/api/projects/{pid}/chapters/{cid}/revise"))
assert rv and "text" in rv and len(rv["text"]) > 200
step("采用修订稿", lambda: req("PUT", f"/api/projects/{pid}/chapters/{cid}", {"content": rv["text"]})["status"])

_, fin = step("定稿(摘要+状态)", lambda: req("POST", f"/api/projects/{pid}/chapters/{cid}/finalize"))
assert fin and fin.get("summary")
assert "bundle" in fin
b4 = fin["bundle"]
assert b4["chapters"][0]["status"] == "final"
step("摘要长度", lambda: len(fin["summary"]))
step("人物状态更新数", lambda: len(fin["state"].get("characters", [])))
step("伏笔事件数", lambda: len(fin["state"].get("foreshadowing", [])))

# 第2章的上下文应包含第1章摘要
c1 = b4["chapters"][1]["id"]
ctx2 = step("第2章上下文(含前情)", lambda: req("GET", f"/api/projects/{pid}/chapters/{c1}/context"))
assert "第1章" in ctx2["context"], "前情脉络缺失"

# 批量连写第 2、3 章
_, bat = step("批量连写2章", lambda: req("POST", f"/api/projects/{pid}/batch", {"count": 2, "review_each": False}))
assert bat and bat["done"] == 2, bat
b5 = req("GET", f"/api/projects/{pid}")
finals = [c for c in b5["chapters"] if c["status"] == "final"]
assert len(finals) == 3, f"定稿章数异常 {len(finals)}"
assert any((c.get("gate") or "") for c in b5["chapters"]), "批量章节门禁报告缺失"
step("批量后定稿章数", lambda: len(finals))

# 情节线(定稿时应由状态抽取写入)
assert len(b5.get("plotlines", [])) >= 1, "情节线缺失"
step("情节线条数", lambda: len(b5["plotlines"]))

# 版本:采用新稿 → 自动快照 → 回滚
c2 = next(c for c in b5["chapters"] if c["idx"] == 2)
orig_len = len(c2["content"] or "")
step("采用新稿(自动快照)", lambda: req("POST", f"/api/projects/{pid}/chapters/{c2['id']}/adopt",
                                      {"content": "回滚测试用新稿。" * 50, "note": "e2e 测试"}))
vers = step("版本列表", lambda: req("GET", f"/api/projects/{pid}/chapters/{c2['id']}/versions"))
assert len(vers) >= 1
step("回滚", lambda: req("POST", f"/api/projects/{pid}/chapters/{c2['id']}/versions/{vers[0]['id']}/restore"))
b6 = req("GET", f"/api/projects/{pid}")
c2b = next(c for c in b6["chapters"] if c["idx"] == 2)
assert len(c2b["content"] or "") == orig_len, "回滚后内容与快照不一致"
step("回滚校验", lambda: f"恢复 {len(c2b['content'])} 字")

# ---------- v0.3 新特性 ----------

prov = step("供应商预设", lambda: req("GET", "/api/providers"))
assert len(prov) >= 10, "内置供应商模板不足"

step("登记Lorebook", lambda: req("POST", f"/api/projects/{pid}/lore",
                                 {"keywords": "玉佩", "content": "(测试)玉佩在雷雨夜发烫,是身世信物"}))
ctxl = req("GET", f"/api/projects/{pid}/chapters/{cid}/context")
assert "Lorebook" in ctxl["context"] and "玉佩在雷雨夜发烫" in ctxl["context"], "Lorebook 未注入上下文"
step("Lorebook关键词注入", lambda: "已注入")

rs = req("GET", f"/api/projects/{pid}/reader")
assert any(r["kind"] == "known" for r in rs), "读者认知未抽取(定稿应产生 known 条目)"
assert any(r["kind"] == "expect" for r in rs), "读者认知未抽取(定稿应产生 expect 条目)"
ctxr = req("GET", f"/api/projects/{pid}/chapters/{cid}/context")
assert "读者认知状态" in ctxr["context"] and "读者已知" in ctxr["context"], "读者认知未注入上下文"
step("读者认知抽取+注入", lambda: f"{len(rs)} 条")

imp = req("GET", f"/api/projects/{pid}/impact?query=%E7%8E%89%E4%BD%A9")  # 玉佩(URL编码)
assert imp["affected"], "影响反查未命中(玉佩应出现在正文/章纲)"
step("影响反查", lambda: f"命中 {len(imp['affected'])} 章")

_, best = step("候选拉样重排(2选1)", lambda: req("POST", f"/api/projects/{pid}/chapters/{c2b['id']}/draft/best", {"n": 2}))
assert best and best.get("rank") and best["rank"].get("best") in ("甲", "乙"), best
assert len(best.get("content") or "") > 500
step("重排结果", lambda: f"候选{best['rank']['best']} 胜出")

_, dr = step("两步去AI味", lambda: req("POST", f"/api/projects/{pid}/chapters/{c2b['id']}/deai"))
assert "text" in dr and "note" in dr, dr

newch = req("POST", f"/api/projects/{pid}/chapters")
_, cas = step("级联重生成1章", lambda: req("POST", f"/api/projects/{pid}/cascade",
                                          {"chapter_ids": [newch["id"]], "include_written": False}))
assert cas and cas["done"] == 1, cas

g = step("门禁重算", lambda: req("POST", f"/api/projects/{pid}/chapters/{c2b['id']}/gate"))
assert g.get("words") > 0 and isinstance(g.get("issues"), list), g

st0 = req("GET", "/api/settings")
profs = st0["settings"]["profiles"]
step("启用向量检索(mock)", lambda: req("PUT", "/api/settings", {
    "profiles": profs, "strong_profile": st0["resolved"]["strong"], "fast_profile": st0["resolved"]["fast"],
    "embedding_profile": st0["resolved"]["strong"], "gen": st0["settings"]["gen"]}))
ctxv = req("GET", f"/api/projects/{pid}/chapters/{newch['id']}/context")
assert "相关的旧情节" in ctxv["context"], "向量检索路径未返回旧情节"
step("向量检索上下文", lambda: f"{ctxv['chars']} 字符")
req("PUT", "/api/settings", {
    "profiles": profs, "strong_profile": st0["resolved"]["strong"], "fast_profile": st0["resolved"]["fast"],
    "embedding_profile": "", "gen": st0["settings"]["gen"]})

usage = step("用量统计", lambda: req("GET", f"/api/projects/{pid}/usage"))
assert usage["calls"] >= 12

# 导出
with urllib.request.urlopen(f"{BASE}/api/projects/{pid}/export?format=txt") as resp:
    txt = resp.read().decode("utf-8")
assert "第1章" in txt and len(txt) > 800
step("导出 TXT", lambda: f"{len(txt)} 字符")
with urllib.request.urlopen(f"{BASE}/api/projects/{pid}/export?format=md") as resp:
    md = resp.read().decode("utf-8")
assert "# 长夜行者" in md
step("导出 Markdown", lambda: f"{len(md)} 字符")
with urllib.request.urlopen(f"{BASE}/api/projects/{pid}/export?format=epub") as resp:
    epub = resp.read()
assert epub[:2] == b"PK" and len(epub) > 1000, "EPUB 结构异常"
step("导出 EPUB", lambda: f"{len(epub)} 字节")

# 导入拆书(覆盖)
sample = "第一章 起点\n\n夜色如墨。他推开门。\n\n第二章 转折\n\n雨落下来。玉佩发烫。\n\n第三章 出发\n\n天亮了,他背起行囊。"
imp = step("导入拆书", lambda: req("POST", f"/api/projects/{pid}/import", {"text": sample}))
assert imp["count"] == 3, imp
step("导入后章数", lambda: len(imp["bundle"]["chapters"]))

# 清理:删除测试项目
step("删除项目", lambda: req("DELETE", f"/api/projects/{pid}"))

print("\n全部通过 ✓")
