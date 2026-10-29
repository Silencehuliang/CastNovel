/* CastNovel 铸书 · 前端工作台(vanilla JS,无构建依赖) */
"use strict";

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const state = {
  settings: null,
  resolved: null,
  projects: [],
  pid: null,
  bundle: null,
  tab: "setting",
  cid: null,
  streaming: false,
  dirty: false,
};

/* ---------------- 基础工具 ---------------- */

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isErr ? "err" : "";
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 3600);
}

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let m = `请求失败(${res.status})`;
    try { const j = await res.json(); m = j.detail || m; } catch (e) { /* ignore */ }
    throw new Error(m);
  }
  return res.json();
}

async function sse(path, body, h) {
  state.streaming = true;
  $$("button").forEach(b => { if (b.dataset.lock !== undefined) b.disabled = true; });
  try {
    const res = await fetch("/api" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok || !res.body) {
      let m = `请求失败(${res.status})`;
      try { const j = await res.json(); m = j.detail || m; } catch (e) { /* ignore */ }
      throw new Error(m);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const evt = JSON.parse(line.slice(5).trim());
        if (evt.type === "delta") h.onDelta && h.onDelta(evt.text);
        else if (evt.type === "progress") h.onEvent && h.onEvent(evt.data);
        else if (evt.type === "done") return evt.result;
        else if (evt.type === "error") throw new Error(evt.message);
      }
    }
    return null;
  } finally {
    state.streaming = false;
    $$("button").forEach(b => { if (b.dataset.lock !== undefined) b.disabled = false; });
  }
}

function markDirty() { state.dirty = true; }

function confirmDiscard() {
  if (!state.dirty) return true;
  if (confirm("有未保存的修改,确定放弃这些修改吗?")) { state.dirty = false; return true; }
  return false;
}

const STATUS_TXT = { planned: "计划", drafting: "生成中", draft: "草稿", reviewed: "已审", final: "定稿" };
function statusBadge(s) {
  const cls = { planned: "b-planned", drafting: "b-drafting", draft: "b-draft", reviewed: "b-reviewed", final: "b-final" }[s] || "b-planned";
  return `<span class="badge ${cls}">${STATUS_TXT[s] || s || "计划"}</span>`;
}

function curProject() { return state.bundle ? state.bundle.project : null; }
function curChapters() { return state.bundle ? state.bundle.chapters : []; }
function curChapter() { return curChapters().find(c => c.id === state.cid) || null; }

/* ---------------- 启动与侧栏 ---------------- */

async function boot() {
  await refreshSettings();
  await refreshProjects();
  $("#btnNew").onclick = openNewProject;
  $("#btnSettings").onclick = openSettings;
  const mc = $("#btnMockCfg");
  if (mc) mc.onclick = (e) => { e.preventDefault(); openSettings(); };
  $$("#tabs .tab").forEach(t => t.onclick = () => switchTab(t.dataset.tab));
}

async function refreshSettings() {
  const data = await api("/settings");
  state.settings = data.settings;
  state.resolved = data.resolved;
  const mock = data.resolved.strong_mock && data.resolved.fast_mock;
  $("#mockBanner").classList.toggle("hidden", !mock);
}

async function refreshProjects() {
  state.projects = await api("/projects");
  renderProjectList();
}

function renderProjectList() {
  const box = $("#projectList");
  box.innerHTML = state.projects.map(p => `
    <div class="project-item ${p.id === state.pid ? "active" : ""}" data-pid="${p.id}">
      <span class="p-title">${esc(p.title)}</span>
      <span class="del" data-del="${p.id}" title="删除作品">✕</span>
    </div>`).join("") || `<div style="font-size:12px;color:#8a8177;padding:8px 10px;">还没有作品</div>`;
  $$(".project-item", box).forEach(el => {
    el.onclick = (ev) => {
      const del = ev.target.dataset.del;
      if (del) { deleteProject(parseInt(del, 10)); return; }
      selectProject(parseInt(el.dataset.pid, 10));
    };
  });
}

async function selectProject(pid) {
  if (state.streaming) { toast("正在生成中,请稍候", true); return; }
  if (!confirmDiscard()) return;
  state.pid = pid;
  await loadProject();
  renderProjectList();
  switchTab("setting");
}

async function loadProject() {
  if (!state.pid) return;
  state.bundle = await api(`/projects/${state.pid}`);
  $("#projTitle").textContent = curProject().title;
  $("#tabs").classList.remove("hidden");
  const ids = curChapters().map(c => c.id);
  if (!ids.includes(state.cid)) state.cid = ids[0] || null;
}

async function deleteProject(pid) {
  const p = state.projects.find(x => x.id === pid);
  if (!confirm(`确定删除作品《${p ? p.title : pid}》?该操作不可恢复。`)) return;
  await api(`/projects/${pid}`, { method: "DELETE" });
  if (state.pid === pid) {
    state.pid = null; state.bundle = null; state.cid = null;
    $("#projTitle").textContent = "未选择作品";
    $("#tabs").classList.add("hidden");
    $("#view").innerHTML = `<div class="empty-hint"><h2>已删除</h2><p>选择或新建一部作品继续。</p></div>`;
  }
  await refreshProjects();
  renderProjectList();
}

function openNewProject() {
  openModal("新建作品", `
    <label class="fld">书名 *</label>
    <input type="text" id="np-title" placeholder="例如:长夜余火" />
    <div class="grid2">
      <div><label class="fld">类型</label><input type="text" id="np-genre" placeholder="玄幻 / 都市 / 悬疑 / 言情…" /></div>
      <div><label class="fld">目标篇幅(章)</label><input type="text" id="np-len" placeholder="30" /></div>
    </div>
    <label class="fld">一句话梗概(可留空,交给 AI 提案)</label>
    <textarea id="np-premise" rows="3" placeholder="主角是谁、想要什么、最大的障碍是什么"></textarea>
    <label class="fld">文风(可留空)</label>
    <textarea id="np-style" rows="2" placeholder="例如:节奏明快,画面感强,对话简洁有锋"></textarea>
  `, async () => {
    const title = $("#np-title").value.trim();
    if (!title) { toast("请填写书名", true); return; }
    const r = await api("/projects", {
      method: "POST",
      body: JSON.stringify({
        title, genre: $("#np-genre").value.trim(),
        premise: $("#np-premise").value.trim(), style: $("#np-style").value.trim(),
      }),
    });
    closeModal();
    await refreshProjects();
    await selectProject(r.id);
  }, "创建");
}

/* ---------------- 页签调度 ---------------- */

function switchTab(tab) {
  if (state.streaming) { toast("正在生成中,请稍候", true); return; }
  if (!state.bundle) return;
  if (!confirmDiscard()) return;
  state.tab = tab;
  $$("#tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab));
  const view = $("#view");
  if (tab === "setting") renderSetting(view);
  else if (tab === "outline") renderOutline(view);
  else if (tab === "write") renderWrite(view);
  else if (tab === "review") renderReview(view);
  else if (tab === "export") renderExport(view);
  state.dirty = false;
}

/* ---------------- 设定页 ---------------- */

async function renderSetting(view) {
  const p = curProject();
  const chars = state.bundle.characters;
  const fores = state.bundle.foreshadowing;
  let lore = [], reader = [];
  try { lore = await api(`/projects/${state.pid}/lore`); } catch (e) { /* 忽略,渲染空表 */ }
  try { reader = await api(`/projects/${state.pid}/reader`); } catch (e) { /* 忽略 */ }
  view.innerHTML = `
    <div class="card">
      <h3>作品信息 <span class="hint">梗概与文风是所有生成的起点,务必先写好</span></h3>
      <div class="grid2">
        <div><label class="fld">书名</label><input type="text" id="f-title" value="${esc(p.title)}"></div>
        <div><label class="fld">类型</label><input type="text" id="f-genre" value="${esc(p.genre)}" placeholder="玄幻 / 都市 / 悬疑…"></div>
      </div>
      <label class="fld">一句话梗概</label>
      <textarea id="f-premise" rows="2">${esc(p.premise)}</textarea>
      <label class="fld">文风</label>
      <textarea id="f-style" rows="2">${esc(p.style)}</textarea>
      <label class="fld">文风样本(可选:粘贴一段你最想模仿的正文,AI 将模仿其节奏与用词)</label>
      <textarea id="f-styleref" rows="3" placeholder="(可留空)粘贴 300~900 字样章……">${esc(p.style_ref || "")}</textarea>
      <div class="row" style="margin-top:8px;">
        <span class="chip" data-style="节奏明快,冲突密集,画面感强,对话简洁有锋,金句点睛">爽文快节奏</span>
        <span class="chip" data-style="细腻沉稳,注重心理与氛围,意象化表达,节奏舒缓">细腻文艺</span>
        <span class="chip" data-style="冷峻克制,信息差驱动,伏笔密集,逻辑严密">悬疑冷峻</span>
        <span class="chip" data-style="古雅绵密,白描传神,诗词点缀,情韵悠长">古风雅致</span>
        <span style="flex:1"></span>
        <button class="btn btn-primary" id="btnSaveInfo">保存信息</button>
      </div>
    </div>

    <div class="card">
      <h3>世界观设定 <span class="hint">力量体系 / 势力格局 / 核心冲突,后续一切生成的依据</span></h3>
      <textarea id="f-worldview" rows="10" placeholder="可以自己写,也可以点击「生成世界观」让 AI 依据梗概提案,再手工修改">${esc(p.worldview)}</textarea>
      <div class="stream-box" id="wv-stream"></div>
      <div class="row row-end" style="margin-top:10px;">
        <button class="btn" id="btnImpact" title="设定改动后,按关键词反查哪些章节提到过它,评估需要回改的范围">🔍 反查受影响章节</button>
        <button class="btn" data-lock id="btnGenWv">✦ 生成世界观</button>
        <button class="btn btn-primary" id="btnSaveWv">保存设定</button>
      </div>
    </div>

    <div class="card">
      <h3>角色档案 <span class="hint">人物欲望、伤痕与当前状态;正文生成时自动注入</span></h3>
      <table class="tbl" id="charTbl">
        <thead><tr><th style="width:120px">姓名</th><th style="width:110px">定位</th><th>档案</th><th style="width:180px">当前状态</th><th style="width:56px"></th></tr></thead>
        <tbody>
          ${chars.map(c => charRow(c)).join("") || `<tr><td colspan="5" style="color:var(--ink-soft)">暂无角色。先保存世界观,再点击「从世界观生成角色」。</td></tr>`}
        </tbody>
      </table>
      <div class="stream-box" id="char-stream"></div>
      <div class="row row-end" style="margin-top:10px;">
        <button class="btn" id="btnAddChar">＋ 手动添加</button>
        <button class="btn btn-primary" data-lock id="btnGenChars">✦ 从世界观生成角色(覆盖)</button>
      </div>
    </div>

    <div class="card">
      <h3>伏笔登记 <span class="hint">定稿章节时由 AI 自动登记/回收,也可手工维护</span></h3>
      <table class="tbl">
        <thead><tr><th>伏笔</th><th>内容</th><th style="width:80px">状态</th><th style="width:70px">埋设</th><th style="width:70px">回收</th><th style="width:56px"></th></tr></thead>
        <tbody>
          ${fores.map(f => `
            <tr data-fid="${f.id}">
              <td>${esc(f.title)}</td>
              <td style="color:var(--ink-soft)">${esc(f.description)}</td>
              <td><span class="badge ${f.status === "open" ? "b-open" : "b-resolved"}" data-fstatus="${f.id}" style="cursor:pointer">${f.status === "open" ? "未回收" : "已回收"}</span></td>
              <td>${f.planted_ch ?? "—"}</td>
              <td>${f.payoff_ch ?? "—"}</td>
              <td><button class="btn btn-sm btn-danger" data-fdel="${f.id}">删除</button></td>
            </tr>`).join("") || `<tr><td colspan="6" style="color:var(--ink-soft)">暂无伏笔记录</td></tr>`}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>情节线 <span class="hint">定稿章节时由 AI 自动登记推进,支撑多线叙事不散架</span></h3>
      <table class="tbl">
        <thead><tr><th>情节线</th><th>当前进展</th><th style="width:80px">状态</th><th style="width:70px">开启</th><th style="width:70px">最近</th><th style="width:56px"></th></tr></thead>
        <tbody>
          ${(state.bundle.plotlines || []).map(pl => `
            <tr>
              <td>${esc(pl.title)}</td>
              <td style="color:var(--ink-soft)">${esc(pl.description)}</td>
              <td><span class="badge ${pl.status === "active" ? "b-open" : "b-resolved"}" data-plstatus="${pl.id}" style="cursor:pointer">${pl.status === "active" ? "进行中" : "已完结"}</span></td>
              <td>${pl.start_ch ?? "—"}</td>
              <td>${pl.last_ch ?? "—"}</td>
              <td><button class="btn btn-sm btn-danger" data-pldel="${pl.id}">删除</button></td>
            </tr>`).join("") || `<tr><td colspan="6" style="color:var(--ink-soft)">暂无情节线,定稿章节后自动生成</td></tr>`}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Lorebook 关键词触发设定 <span class="hint">正文/章纲命中关键词时,该条设定自动注入生成上下文(SillyTavern World Info 机制)</span></h3>
      <table class="tbl">
        <thead><tr><th style="width:200px">触发关键词(逗号分隔)</th><th>设定内容(命中即注入)</th><th style="width:70px">启用</th><th style="width:56px"></th></tr></thead>
        <tbody>
          ${lore.map(l => `
            <tr data-lid="${l.id}">
              <td><input type="text" data-lkeywords value="${esc(l.keywords)}"></td>
              <td><textarea data-lcontent rows="2">${esc(l.content)}</textarea></td>
              <td><span class="badge ${l.enabled ? "b-open" : "b-resolved"} data-ltoggle="${l.id}" style="cursor:pointer">${l.enabled ? "启用" : "停用"}</span></td>
              <td><button class="btn btn-sm btn-danger" data-ldel="${l.id}">删除</button></td>
            </tr>`).join("") || `<tr><td colspan="4" style="color:var(--ink-soft)">暂无条目。例如:关键词「玉佩」→ 内容「玉佩在雷雨夜会发烫,是身世信物」</td></tr>`}
        </tbody>
      </table>
      <div class="row" style="margin-top:10px;">
        <input type="text" id="lore-kw" placeholder="关键词,逗号分隔" style="max-width:220px">
        <input type="text" id="lore-content" placeholder="命中后注入的设定内容" style="flex:1">
        <button class="btn" id="btnAddLore">＋ 添加条目</button>
      </div>
    </div>

    <div class="card">
      <h3>读者认知状态 <span class="hint">读者已知/期待/误认,与角色已知、作者真相分离;定稿时自动抽取,生成时注入防复述、管期待、控误导</span></h3>
      <table class="tbl">
        <thead><tr><th style="width:80px">类型</th><th>内容(读者视角)</th><th style="width:70px">起于</th><th style="width:80px">状态</th><th style="width:56px"></th></tr></thead>
        <tbody>
          ${reader.map(r => `
            <tr data-rid="${r.id}">
              <td><span class="badge ${r.kind === "known" ? "b-open" : r.kind === "expect" ? "b-drafting" : "b-中"}">${{ known: "已知", expect: "期待", mislead: "误认" }[r.kind] || r.kind}</span></td>
              <td><input type="text" data-rcontent value="${esc(r.content)}"></td>
              <td>${r.chapter_idx ?? "—"}</td>
              <td><span class="badge ${r.status === "open" ? "b-open" : "b-resolved"} data-rtoggle="${r.id}" style="cursor:pointer">${r.status === "open" ? "进行中" : "已了结"}</span></td>
              <td><button class="btn btn-sm btn-danger" data-rdel="${r.id}">删除</button></td>
            </tr>`).join("") || `<tr><td colspan="5" style="color:var(--ink-soft)">暂无记录,定稿章节后自动抽取;也可手工登记</td></tr>`}
        </tbody>
      </table>
      <div class="row" style="margin-top:10px;">
        <select id="reader-kind" style="max-width:110px">
          <option value="known">已知</option><option value="expect">期待</option><option value="mislead">误认</option>
        </select>
        <input type="text" id="reader-content" placeholder="例如:读者已知玉佩是身世信物" style="flex:1">
        <button class="btn" id="btnAddReader">＋ 登记</button>
      </div>
    </div>
  `;

  $("#btnSaveInfo").onclick = saveProjectInfo;
  $$(".chip").forEach(ch => ch.onclick = () => { $("#f-style").value = ch.dataset.style; markDirty(); });
  $$("[data-plstatus]").forEach(el => el.onclick = async () => {
    const plid = el.dataset.plstatus;
    const cur = el.textContent === "进行中" ? "done" : "active";
    await api(`/projects/${state.pid}/plotlines/${plid}`, { method: "PUT", body: JSON.stringify({ status: cur }) });
    await loadProject(); renderSetting(view);
  });
  $$("[data-pldel]").forEach(el => el.onclick = async () => {
    if (!confirm("删除这条情节线?")) return;
    await api(`/projects/${state.pid}/plotlines/${el.dataset.pldel}`, { method: "DELETE" });
    await loadProject(); renderSetting(view);
  });
  $("#btnImpact").onclick = () => openImpact();
  $("#btnAddLore").onclick = async () => {
    const kw = $("#lore-kw").value.trim(), ct = $("#lore-content").value.trim();
    if (!kw || !ct) { toast("关键词与内容都要填", true); return; }
    await api(`/projects/${state.pid}/lore`, { method: "POST", body: JSON.stringify({ keywords: kw, content: ct }) });
    toast("Lorebook 条目已添加,命中关键词时自动注入");
    await loadProject(); renderSetting(view);
  };
  $$("[data-ltoggle]").forEach(el => el.onclick = async () => {
    const lid = el.dataset.ltoggle;
    const enable = el.textContent === "停用";
    await api(`/projects/${state.pid}/lore/${lid}`, { method: "PUT", body: JSON.stringify({ enabled: enable }) });
    await loadProject(); renderSetting(view);
  });
  $$("[data-ldel]").forEach(el => el.onclick = async () => {
    if (!confirm("删除该 Lorebook 条目?")) return;
    await api(`/projects/${state.pid}/lore/${el.dataset.ldel}`, { method: "DELETE" });
    await loadProject(); renderSetting(view);
  });
  $$("[data-lkeywords],[data-lcontent]").forEach(el => {
    el.addEventListener("input", markDirty);
    el.addEventListener("blur", async () => {
      const tr = el.closest("tr");
      await api(`/projects/${state.pid}/lore/${tr.dataset.lid}`, {
        method: "PUT",
        body: JSON.stringify({ keywords: $("[data-lkeywords]", tr).value, content: $("[data-lcontent]", tr).value }),
      });
      state.dirty = false;
    });
  });
  $("#btnAddReader").onclick = async () => {
    const ct = $("#reader-content").value.trim();
    if (!ct) { toast("请填写内容", true); return; }
    await api(`/projects/${state.pid}/reader`, {
      method: "POST", body: JSON.stringify({ kind: $("#reader-kind").value, content: ct }) });
    toast("读者认知已登记,生成时自动注入");
    await loadProject(); renderSetting(view);
  };
  $$("[data-rtoggle]").forEach(el => el.onclick = async () => {
    const rid = el.dataset.rtoggle;
    const open = el.textContent === "已了结";
    await api(`/projects/${state.pid}/reader/${rid}`, { method: "PUT", body: JSON.stringify({ status: open ? "open" : "done" }) });
    await loadProject(); renderSetting(view);
  });
  $$("[data-rdel]").forEach(el => el.onclick = async () => {
    if (!confirm("删除该条读者认知?")) return;
    await api(`/projects/${state.pid}/reader/${el.dataset.rdel}`, { method: "DELETE" });
    await loadProject(); renderSetting(view);
  });
  $$("[data-rcontent]").forEach(el => {
    el.addEventListener("input", markDirty);
    el.addEventListener("blur", async () => {
      await api(`/projects/${state.pid}/reader/${el.closest("tr").dataset.rid}`, {
        method: "PUT", body: JSON.stringify({ content: el.value }) });
      state.dirty = false;
    });
  });
  $("#btnGenWv").onclick = async () => {
    await saveProjectInfo(true);
    const box = $("#wv-stream"); box.style.display = "block"; box.textContent = "";
    $("#f-worldview").value = "";
    try {
      await sse(`/projects/${state.pid}/generate/worldview`, {}, {
        onDelta: t => { $("#f-worldview").value += t; box.textContent = ($("#f-worldview").value.length + " 字…"); },
      });
      toast("世界观已生成并保存");
    } catch (e) { toast(e.message, true); }
    box.style.display = "none";
    state.dirty = false;
    await loadProject(); renderSetting(view);
  };
  $("#btnSaveWv").onclick = async () => {
    await api(`/projects/${state.pid}`, { method: "PUT", body: JSON.stringify({ worldview: $("#f-worldview").value }) });
    state.dirty = false; toast("世界观已保存");
  };
  $("#btnGenChars").onclick = async () => {
    if (!curProject().worldview) { toast("请先生成或保存世界观", true); return; }
    if (chars.length && !confirm("将覆盖现有全部角色档案,确定?")) return;
    const box = $("#char-stream"); box.style.display = "block"; box.textContent = "生成中…";
    try {
      const bundle = await sse(`/projects/${state.pid}/generate/characters`, {}, {
        onDelta: t => { box.textContent = (box.textContent.slice(-160) + t).slice(-300); },
      });
      if (bundle) { state.bundle = bundle; }
      toast("角色已生成");
    } catch (e) { toast(e.message, true); }
    box.style.display = "none";
    await loadProject(); renderSetting(view);
  };
  $("#btnAddChar").onclick = () => {
    const tbody = $("#charTbl tbody");
    const empty = tbody.querySelector("td[colspan]");
    if (empty) empty.parentElement.remove();
    const tr = document.createElement("tr");
    tr.dataset.newrow = "1";
    tr.innerHTML = `
      <td><input type="text" data-cname placeholder="姓名"></td>
      <td><input type="text" data-crole placeholder="主角/反派…"></td>
      <td><textarea data-cprofile rows="2" placeholder="身份、性格、欲望、伤痕"></textarea></td>
      <td><input type="text" data-cstatus placeholder="开篇处境"></td>
      <td><button class="btn btn-sm btn-danger" data-delrow>删除</button></td>`;
    tbody.appendChild(tr);
    bindCharRows();
  };
  bindCharRows();
  $$("input,textarea", view).forEach(el => el.addEventListener("input", markDirty));
  $$("[data-fstatus]").forEach(el => el.onclick = async () => {
    const fid = el.dataset.fstatus;
    const cur = el.textContent === "未回收" ? "resolved" : "open";
    await api(`/projects/${state.pid}/foreshadowing/${fid}`, { method: "PUT", body: JSON.stringify({ status: cur }) });
    await loadProject(); renderSetting(view);
  });
  $$("[data-fdel]").forEach(el => el.onclick = async () => {
    if (!confirm("删除这条伏笔?")) return;
    await api(`/projects/${state.pid}/foreshadowing/${el.dataset.fdel}`, { method: "DELETE" });
    await loadProject(); renderSetting(view);
  });
}

function charRow(c) {
  return `<tr data-chid="${c.id}">
    <td><input type="text" data-cname value="${esc(c.name)}"></td>
    <td><input type="text" data-crole value="${esc(c.role)}"></td>
    <td><textarea data-cprofile rows="2">${esc(c.profile)}</textarea></td>
    <td><input type="text" data-cstatus value="${esc(c.status)}"></td>
    <td><button class="btn btn-sm btn-danger" data-cdel="${c.id}">删除</button></td>
  </tr>`;
}

function bindCharRows() {
  $$("[data-cdel]").forEach(b => b.onclick = async () => {
    if (!confirm("删除该角色?")) return;
    await api(`/projects/${state.pid}/characters/${b.dataset.cdel}`, { method: "DELETE" });
    await loadProject(); switchTab("setting");
  });
  $$("[data-delrow]").forEach(b => b.onclick = () => b.closest("tr").remove());
}

async function saveProjectInfo(silent) {
  const p = curProject();
  const body = {
    title: $("#f-title").value.trim() || p.title,
    genre: $("#f-genre").value.trim(),
    premise: $("#f-premise").value.trim(),
    style: $("#f-style").value.trim(),
    worldview: $("#f-worldview") ? $("#f-worldview").value : p.worldview,
    style_ref: $("#f-styleref") ? $("#f-styleref").value : (p.style_ref || ""),
  };
  await api(`/projects/${state.pid}`, { method: "PUT", body: JSON.stringify(body) });
  await loadProject();
  $("#projTitle").textContent = curProject().title;
  if (!silent) { state.dirty = false; toast("已保存"); }
  renderProjectList();
}

/* 设定变更影响分析:按关键词反查受影响章节(确定性扫描,零 token) */
async function openImpact(defaultKw) {
  openModal("设定变更影响分析(反查受影响章节)", `
    <p style="font-size:12.5px;color:var(--ink-soft);margin-bottom:10px;">
      改了世界观/角色/时间线之后,先反查哪些章节提到过它,再决定回改或级联重生成(写作页/大纲页)。</p>
    <label class="fld">关键词(角色名、地点、设定词,逗号分隔;扫描标题/概要/章纲/正文)</label>
    <div class="row">
      <input type="text" id="imp-q" value="${esc(defaultKw || "")}" placeholder="例如:陆铮, 玉佩" style="flex:1">
      <button class="btn btn-primary" id="imp-go">反查</button>
    </div>
    <div id="imp-result" style="margin-top:12px;"></div>
  `, close => close(), "关闭");
  const run = async () => {
    const kw = $("#imp-q").value.trim();
    if (!kw) { toast("请输入关键词", true); return; }
    $("#imp-result").innerHTML = "<p style='color:var(--ink-soft)'>扫描中…</p>";
    try {
      const r = await api(`/projects/${state.pid}/impact?query=${encodeURIComponent(kw)}`);
      const rows = r.affected || [];
      const stTxt = { planned: "计划", draft: "草稿", reviewed: "已审", final: "定稿" };
      $("#imp-result").innerHTML = `
        <p style="font-size:13px;margin-bottom:8px;">命中角色:${(r.characters_matched || []).map(esc).join("、") || "无"};受影响章节 <b>${rows.length}</b> 章。</p>
        ${rows.length ? `<table class="tbl"><thead><tr><th style="width:46px">#</th><th>标题</th><th style="width:70px">状态</th><th style="width:70px">命中</th><th style="width:150px">分布(标题/概要/章纲/正文)</th></tr></thead>
        <tbody>${rows.map(x => `<tr><td>${x.idx}</td><td>${esc(x.title)}</td><td>${stTxt[x.status] || x.status}</td><td><b>${x.hits}</b></td><td>${x.n_title}/${x.n_summary}/${x.n_beats}/${x.n_content}</td></tr>`).join("")}</tbody></table>`
        : `<p style="color:var(--ink-soft)">没有章节命中这些关键词。</p>`}`;
    } catch (e) { $("#imp-result").innerHTML = `<p style="color:var(--accent)">${esc(e.message)}</p>`; }
  };
  $("#imp-go").onclick = run;
  if (defaultKw) run();
}

/* ---------------- 大纲页 ---------------- */

function renderOutline(view) {
  const chapters = curChapters();
  const volumes = safeJSON(curProject().volumes) || [];
  view.innerHTML = `
    <div class="card">
      <h3>全书大纲 <span class="hint">逐章「目标-阻碍-转折」+ 章末钩子;每章都可再手工修改</span></h3>
      <div class="row">
        <label class="fld" style="margin:0">章数</label>
        <input type="number" id="ol-count" value="30" min="3" max="200" style="width:90px">
        ${chapters.length ? `<label style="font-size:13px"><input type="checkbox" id="ol-replace"> 覆盖现有章节</label>` : ""}
        <span style="flex:1"></span>
        <button class="btn" id="btnCascade" title="改了某章章纲/设定后,把它之后的受影响章节勾选出来批量重做(章纲→正文→定稿,已有正文自动快照)">⚡ 级联重生成</button>
        <button class="btn" id="btnAddCh">＋ 手动加章</button>
        <button class="btn btn-primary" data-lock id="btnGenOutline">✦ 生成全书大纲</button>
      </div>
      <div class="stream-box" id="ol-stream"></div>
      <div class="statusline" id="ol-status"></div>
      ${volumes.map(v => `<div class="vol-card"><b>${esc(v.title)}</b>(第 ${(v.chapters || ["?"])[0]} - ${(v.chapters || [0, "?"])[1]} 章)— ${esc(v.summary)}</div>`).join("")}
    </div>

    ${chapters.length ? `
    <div class="card">
      <h3>章节列表 <span class="hint">点击行选择章节;标题与概要可直接编辑</span></h3>
      <table class="tbl" id="olTbl">
        <thead><tr><th style="width:46px">#</th><th style="width:170px">标题</th><th>概要</th><th style="width:76px">状态</th><th style="width:60px"></th></tr></thead>
        <tbody>
          ${chapters.map(ch => `
            <tr data-chrow="${ch.id}" style="cursor:${ch.id === state.cid ? "auto" : "pointer"};${ch.id === state.cid ? "background:#fbf7ec;" : ""}">
              <td>${ch.idx}</td>
              <td><input type="text" data-otitle value="${esc(ch.title)}"></td>
              <td><textarea data-osummary rows="2">${esc(ch.summary)}</textarea></td>
              <td>${statusBadge(ch.status)}</td>
              <td><button class="btn btn-sm btn-danger" data-odel="${ch.id}">删除</button></td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>章纲(节拍)<span class="hint">选中章节:第 ${curChapter() ? curChapter().idx : "—"} 章</span></h3>
      <textarea id="f-beats" rows="6" placeholder="1. 场景一……&#10;2. 场景二……&#10;章末钩子:……">${esc(curChapter() ? curChapter().beats : "")}</textarea>
      <div class="stream-box" id="beats-stream"></div>
      <div class="row row-end" style="margin-top:10px;">
        <button class="btn" data-lock id="btnBeats2" ${curChapter() ? "" : "disabled"}>✦ 生成本章节拍</button>
        <button class="btn btn-primary" id="btnSaveBeats" ${curChapter() ? "" : "disabled"}>保存章纲</button>
      </div>
    </div>` : ""}
  `;

  $("#btnGenOutline").onclick = async () => {
    if (!curProject().worldview) { toast("请先生成世界观设定", true); return; }
    const count = parseInt($("#ol-count").value, 10) || 30;
    const replace = $("#ol-replace") ? $("#ol-replace").checked : false;
    if (replace && !confirm("将覆盖现有全部章节(含章纲),确定?")) return;
    const box = $("#ol-stream"); box.style.display = "block"; box.textContent = "";
    $("#ol-status").textContent = "生成中,请稍候…";
    try {
      const bundle = await sse(`/projects/${state.pid}/generate/outline`, { count, replace }, {
        onDelta: t => { box.textContent = (box.textContent + t).slice(-400); },
      });
      if (bundle) { state.bundle = bundle; state.cid = curChapters()[0] ? curChapters()[0].id : null; }
      toast("大纲已生成");
    } catch (e) { toast(e.message, true); $("#ol-status").textContent = ""; }
    box.style.display = "none";
    await loadProject(); renderOutline(view);
  };
  $("#btnAddCh").onclick = async () => {
    await api(`/projects/${state.pid}/chapters`, { method: "POST", body: "{}" });
    await loadProject(); renderOutline(view);
  };
  $$("[data-odel]").forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation();
    if (!confirm("删除该章?")) return;
    await api(`/projects/${state.pid}/chapters/${b.dataset.odel}`, { method: "DELETE" });
    if (state.cid === parseInt(b.dataset.odel, 10)) state.cid = null;
    await loadProject(); renderOutline(view);
  });
  $$("[data-chrow]").forEach(tr => {
    tr.onclick = (ev) => {
      if (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA" || ev.target.tagName === "BUTTON") return;
      if (!confirmDiscard()) return;
      state.cid = parseInt(tr.dataset.chrow, 10);
      renderOutline(view);
    };
  });
  $$("[data-otitle],[data-osummary]").forEach(el => el.addEventListener("input", markDirty));
  const saveRow = async (tr) => {
    await api(`/projects/${state.pid}/chapters/${tr.dataset.chrow}`, {
      method: "PUT",
      body: JSON.stringify({ title: $("[data-otitle]", tr).value, summary: $("[data-osummary]", tr).value }),
    });
  };
  $$("[data-chrow]").forEach(tr => {
    $("[data-otitle]", tr).onblur = () => saveRow(tr).then(() => { state.dirty = false; });
    $("[data-osummary]", tr).onblur = () => saveRow(tr).then(() => { state.dirty = false; });
  });
  const bindBeats = () => {
    $("#btnBeats2").onclick = genBeats;
    $("#btnSaveBeats").onclick = async () => {
      if (!curChapter()) return;
      await api(`/projects/${state.pid}/chapters/${state.cid}`, { method: "PUT", body: JSON.stringify({ beats: $("#f-beats").value }) });
      state.dirty = false; toast("章纲已保存");
      await loadProject();
    };
  };
  async function genBeats() {
    const box = $("#beats-stream"); box.style.display = "block"; box.textContent = "";
    $("#f-beats").value = "";
    try {
      const ch = await sse(`/projects/${state.pid}/chapters/${state.cid}/beats`, {}, {
        onDelta: t => { $("#f-beats").value += t; box.textContent = "生成中…"; },
      });
      if (ch) {
        const local = curChapters().find(c => c.id === ch.id);
        if (local) { local.beats = ch.beats; local.status = ch.status; }
        $("#f-beats").value = ch.beats || "";
      }
      toast("章纲已生成");
    } catch (e) { toast(e.message, true); }
    box.style.display = "none";
    await loadProject();
  }
  bindBeats();
  $("#btnCascade").onclick = openCascade;
}

/* 级联重生成:从选中章开始勾选下游章节,批量 章纲→正文→定稿(已有正文自动快照) */
function openCascade() {
  const chapters = curChapters();
  const src = curChapter();
  if (!src) { toast("请先选中一章作为级联起点", true); return; }
  const downstream = chapters.filter(c => c.idx >= src.idx);
  if (!downstream.length) { toast("没有可重做的章节", true); return; }
  openModal(`级联重生成(从第 ${src.idx} 章起)`, `
    <p style="font-size:12.5px;color:var(--ink-soft);margin-bottom:10px;">
      对勾选章节依次执行:章纲 → 正文 → 定稿。已有正文的章重写前自动快照,可在版本历史找回。</p>
    <div style="max-height:300px;overflow-y:auto;border:1px solid var(--line);border-radius:8px;padding:10px;">
      ${downstream.map(c => `
        <label style="display:block;font-size:13px;padding:3px 0;">
          <input type="checkbox" data-csc value="${c.id}" ${c.content ? "" : "checked"}>
          第${c.idx}章 ${esc(c.title)} ${c.content ? `· 已写 ${(c.content || "").length} 字` : "· 未写"}
        </label>`).join("")}
    </div>
    <label style="font-size:13px;margin-top:10px;display:block;">
      <input type="checkbox" id="csc-written"> 包含已有正文的章(把上面未勾选的已写章也一起重做)</label>
    <div class="statusline" id="csc-log" style="margin-top:8px;white-space:pre-wrap;"></div>
  `, async close => {
    const ids = $$("[data-csc]").filter(c => c.checked).map(c => parseInt(c.value, 10));
    if (!ids.length) { toast("未勾选任何章节", true); return; }
    const includeWritten = $("#csc-written").checked;
    close();
    const log = $("#ol-stream"); log.style.display = "block"; log.textContent = "级联任务启动…";
    try {
      const r = await sse(`/projects/${state.pid}/cascade`, { chapter_ids: ids, include_written: includeWritten }, {
        onEvent: e => {
          let line = "";
          if (e.event === "chapter_start") line = `[第${e.idx}章 ${e.title}] 开始…`;
          else if (e.event === "chapter_skipped") line = `[第${e.idx}章] 跳过:${e.message}`;
          else if (e.event === "chapter_done") line = `[第${e.idx}章] 完成 ✓`;
          else if (e.event === "chapter_error") line = `[第${e.idx}章] 失败:${e.message}`;
          else if (e.event === "aborted") line = `⛔ ${e.message}`;
          else if (e.event === "batch_done") line = `级联结束:${e.done}/${e.total} 章完成`;
          if (line) log.textContent = (log.textContent + "\n" + line).split("\n").slice(-14).join("\n");
        },
      });
      toast(`级联重生成完成:${(r && r.done) || 0} 章`);
    } catch (e) { toast(e.message, true); }
    log.style.display = "none";
    await loadProject(); renderOutline($("#view"));
  }, "开始重生成");
}

/* ---------------- 写作页 ---------------- */

function chapterOptions() {
  return curChapters().map(c =>
    `<option value="${c.id}" ${c.id === state.cid ? "selected" : ""}>第${c.idx}章 ${esc(c.title)} [${STATUS_TXT[c.status] || ""}]</option>`).join("");
}

function gateBadges(ch) {
  const g = ch && ch.gate ? safeJSON(ch.gate) : null;
  if (!g) return "";
  const issues = g.issues || [];
  const warn = issues.filter(i => i.level === "warn").length;
  const tip = issues.map(i => i.msg).join("\n") || "门禁检查通过";
  return `<span class="badge ${warn ? "b-中" : "b-resolved"}" title="${esc(tip)}">门禁:${g.words}/${g.target}字 · 人物${(g.char_hits || []).length}个${warn ? ` · ⚠${warn}` : " · ✓"}</span>`;
}

function renderWrite(view) {
  const ch = curChapter();
  view.innerHTML = `
    <div class="card">
      <div class="row">
        <select id="wsel" style="max-width:340px">${chapterOptions()}</select>
        <span id="w-status">${ch ? statusBadge(ch.status) : ""}</span>
        <span id="w-count" style="font-size:12px;color:var(--ink-soft)">${ch ? (ch.content || "").length + " 字" : ""}</span>
        <span id="w-gate">${ch ? gateBadges(ch) : ""}</span>
        <span style="flex:1"></span>
        <button class="btn btn-sm" id="btnPrev" ${ch && curChapters().indexOf(ch) > 0 ? "" : "disabled"}>← 上一章</button>
        <button class="btn btn-sm" id="btnNext" ${ch && curChapters().indexOf(ch) < curChapters().length - 1 ? "" : "disabled"}>下一章 →</button>
      </div>
      ${ch ? `
      <div class="row" style="margin-top:10px;padding-top:10px;border-top:1px dashed var(--line);">
        <input type="number" id="batch-count" value="3" min="1" max="50" style="width:76px" title="连续写作章数">
        <label style="font-size:12.5px"><input type="checkbox" id="batch-review"> 每章审校</label>
        <button class="btn btn-sm" data-lock id="btnBatch" title="对未写章节依次:章纲→正文(→审校)→定稿">⚡ 批量连写</button>
        <span style="flex:1"></span>
        <button class="btn btn-sm" id="btnRegate" title="对当前正文重跑一致性门禁(零 token),刷新徽章">重跑门禁</button>
        <button class="btn btn-sm" id="btnVersions">🕘 版本历史</button>
        <button class="btn btn-sm" id="btnSnapshot">保存快照</button>
      </div>
      <div class="statusline" id="batch-log"></div>
      <details class="ctx">
        <summary>查看本章将注入的上下文(前情 / 角色状态 / 相关旧情节 / 伏笔 / Lorebook)</summary>
        <div id="ctx-budget" style="font-size:12px;color:var(--ink-soft);padding:6px 8px;margin:6px 0;background:rgba(127,127,127,.08);border-radius:6px;"></div>
        <pre id="ctx-pre">点击 summary 展开时自动加载…</pre>
      </details>

      <label class="fld">本章概要</label>
      <textarea id="f-wsummary" rows="2">${esc(ch.summary)}</textarea>
      <label class="fld">章纲(节拍,生成正文时注入)</label>
      <textarea id="f-wbeats" rows="4">${esc(ch.beats)}</textarea>
      <div class="row row-end"><button class="btn btn-sm" id="btnSaveBeatsW">保存概要与章纲</button></div>

      <label class="fld">正文</label>
      <textarea id="f-content" rows="18" style="font-size:14.5px;line-height:1.9" placeholder="点击「✦ 生成正文」开始,或直接粘贴/撰写正文">${esc(ch.content)}</textarea>
      <div class="stream-box" id="w-stream"></div>
      <div class="row row-end" style="margin-top:10px;">
        <label style="font-size:12.5px" title="拉多份候选稿,门禁打分 + 主编重排,择优采用;落选稿存入版本历史">候选
          <input type="number" id="cand-count" value="1" min="1" max="4" style="width:52px"></label>
        <button class="btn" data-lock id="btnDraft">✦ 生成正文</button>
        <button class="btn" data-lock id="btnPolish" ${ch.content ? "" : "disabled"}>去 AI 味润色</button>
        <button class="btn" data-lock id="btnDeai" ${ch.content ? "" : "disabled"}
          title="第一步程序化定位 AI 味片段(零 token),第二步模型按定位清单逐条改写">✦ 两步去 AI 味(span 定位)</button>
        <button class="btn btn-primary" id="btnSaveContent">保存正文</button>
      </div>
      <div class="row" style="margin-top:8px;font-size:12px;color:var(--ink-soft);">
        <span>流程建议:章纲 → 生成正文 → 润色 → 审校页修订 → 定稿(更新摘要与人物状态)</span>
      </div>
      ` : `<p style="color:var(--ink-soft);padding:20px 0;">请先在大纲页建立章节。</p>`}
    </div>
  `;
  if (!ch) return;

  $("#wsel").onchange = () => { if (confirmDiscard()) { state.cid = parseInt($("#wsel").value, 10); renderWrite(view); } };
  $("#btnPrev").onclick = () => navChapter(-1, view);
  $("#btnNext").onclick = () => navChapter(1, view);

  const det = $(".ctx", view);
  det.addEventListener("toggle", async () => {
    if (det.open && !$("#ctx-pre").dataset.loaded) {
      try {
        const r = await api(`/projects/${state.pid}/chapters/${state.cid}/context`);
        $("#ctx-pre").textContent = r.context;
        $("#ctx-pre").dataset.loaded = "1";
        const b = r.budget || {};
        const budgetEl = $("#ctx-budget");
        if (b.layers) {
          const parts = b.layers.filter(l => l.used > 0)
            .map(l => `${l.label} ${l.used}/${l.quota}`).join(" · ");
          budgetEl.textContent = `预算 ${b.budget} 字 · 已用 ${b.used} 字 · 剩余 ${b.remaining} 字 · ${parts}`
            + (b.dropped_total ? ` · 裁掉 ${b.dropped_total} 条` : "");
          budgetEl.title = b.layers.filter(l => l.dropped_count)
            .map(l => `${l.label} 裁掉 ${l.dropped_count} 条:${(l.dropped || []).join("、")}`).join("\n");
        } else {
          budgetEl.textContent = "";
          budgetEl.title = "";
        }
      } catch (e) { $("#ctx-pre").textContent = "加载失败:" + e.message; }
    }
  });
  $$("input,textarea", view).forEach(el => el.addEventListener("input", () => { markDirty(); }));

  $("#btnSaveBeatsW").onclick = async () => {
    await api(`/projects/${state.pid}/chapters/${state.cid}`, {
      method: "PUT", body: JSON.stringify({ summary: $("#f-wsummary").value, beats: $("#f-wbeats").value }),
    });
    await loadProject(); state.dirty = false; toast("已保存");
  };
  $("#btnDraft").onclick = async () => {
    if (!confirmDiscard()) return;
    if ((ch.content || "").trim() && !confirm("本章已有正文,重新生成将覆盖(旧稿自动快照),确定?")) return;
    await api(`/projects/${state.pid}/chapters/${state.cid}`, {
      method: "PUT", body: JSON.stringify({ summary: $("#f-wsummary").value, beats: $("#f-wbeats").value }),
    });
    const nCand = parseInt($("#cand-count").value, 10) || 1;
    const useRank = nCand > 1;
    const ta = $("#f-content"); ta.value = ""; ta.readOnly = true;
    const box = $("#w-stream"); box.style.display = "block";
    try {
      const saved = await sse(
        useRank ? `/projects/${state.pid}/chapters/${state.cid}/draft/best` : `/projects/${state.pid}/chapters/${state.cid}/draft`,
        useRank ? { n: nCand } : {},
        {
          onDelta: t => {
            if (useRank) { box.textContent = (box.textContent + t).slice(-600); }
            else { ta.value += t; ta.scrollTop = ta.scrollHeight; $("#w-count").textContent = ta.value.length + " 字"; }
          },
        });
      if (saved) { ch.content = saved.content; ch.status = saved.status; ch.summary = saved.summary; }
      $("#w-status").innerHTML = statusBadge(ch.status);
      if (useRank && saved && saved.rank) {
        const r = saved.rank;
        toast(`重排完成:候选${r.best} 胜出${r.reason ? " — " + r.reason : ""}`);
        box.textContent = `已采用候选${r.best}。各候选评分:${Object.entries(r.scores || {}).map(([k, v]) => k + "=" + v).join("  ") || "(模型未给出)"}\n落选稿已存入版本历史,可随时找回。`;
      } else {
        toast(`正文已生成(${(ch.content || "").length} 字)`);
      }
    } catch (e) { toast(e.message, true); }
    box.style.display = "none"; ta.readOnly = false;
    await loadProject(); renderWrite(view);
  };
  $("#btnPolish").onclick = () => previewOverwrite(
    `/projects/${state.pid}/chapters/${state.cid}/polish`, "#w-stream", "润色稿(去 AI 味)", view);
  $("#btnDeai").onclick = () => previewOverwrite(
    `/projects/${state.pid}/chapters/${state.cid}/deai`, "#w-stream", "两步去 AI 味(span 定位改写)", view, null,
    r => { if (r && r.note) toast(r.note); });
  $("#btnSaveContent").onclick = async () => {
    await api(`/projects/${state.pid}/chapters/${state.cid}`, {
      method: "PUT", body: JSON.stringify({ content: $("#f-content").value }),
    });
    await loadProject(); state.dirty = false; toast("正文已保存");
    renderWrite(view);
  };
  $("#btnBatch").onclick = runBatch;
  $("#btnRegate").onclick = async () => {
    if (!(ch.content || "").trim()) { toast("本章无正文,无需门禁", true); return; }
    try {
      const g = await api(`/projects/${state.pid}/chapters/${state.cid}/gate`, { method: "POST", body: "{}" });
      ch.gate = JSON.stringify(g);
      $("#w-gate").innerHTML = gateBadges(ch);
      const warns = (g.issues || []).filter(i => i.level === "warn").length;
      toast(warns ? `门禁完成:${warns} 项 warn 级告警` : "门禁通过 ✓");
    } catch (e) { toast(e.message, true); }
  };
  $("#btnVersions").onclick = () => openVersions(view);
  $("#btnSnapshot").onclick = async () => {
    const note = prompt("版本备注:", "手动快照");
    if (note === null) return;
    await api(`/projects/${state.pid}/chapters/${state.cid}/versions`, {
      method: "POST", body: JSON.stringify({ note: note || "手动快照" }),
    });
    toast("已保存快照");
  };
}

/* 批量连写:章纲 → 正文 →(可选审校)→ 定稿,事件流实时显示 */
async function runBatch() {
  const count = parseInt($("#batch-count").value, 10) || 3;
  const review = $("#batch-review").checked;
  if (!confirm(`将连续写作 ${count} 章(章纲 → 正文${review ? " → 审校" : ""} → 定稿),消耗较多 API 额度,确定开始?`)) return;
  const log = $("#batch-log");
  log.textContent = "批量任务启动…";
  try {
    const r = await sse(`/projects/${state.pid}/batch`, { count, review_each: review }, {
      onEvent: e => {
        let line = "";
        if (e.event === "chapter_start") line = `[第${e.idx}章 ${e.title}] 开始…`;
        else if (e.event === "chapter_done") line = `[第${e.idx}章] 完成 ${e.chars} 字` + ((e.gate_issues || []).length ? ` ⚠${e.gate_issues.length} 项门禁警告` : " ✓ 门禁通过");
        else if (e.event === "chapter_error") line = `[第${e.idx}章] 失败:${e.message}`;
        else if (e.event === "aborted") line = `⛔ ${e.message}`;
        else if (e.event === "batch_done") line = `批量结束:${e.done}/${e.total} 章完成`;
        if (line) log.textContent = (log.textContent + "\n" + line).split("\n").slice(-14).join("\n");
      },
    });
    toast(`批量连写完成:${(r && r.done) || 0} 章`);
  } catch (e) { toast(e.message, true); }
  await loadProject();
  switchTab("write");
}

/* 版本历史:查看/恢复快照 */
async function openVersions(view) {
  openModal("章节版本历史", `<div id="ver-list">加载中…</div>`, close => close(), "关闭");
  const rows = await api(`/projects/${state.pid}/chapters/${state.cid}/versions`);
  const box = $("#ver-list");
  if (!box) return;
  box.innerHTML = rows.length ? `
    <table class="tbl">
      <thead><tr><th>时间</th><th>备注</th><th>字数</th><th></th></tr></thead>
      <tbody>${rows.map(v => `
        <tr><td>${esc(v.created_at)}</td><td>${esc(v.note)}</td><td>${v.chars}</td>
        <td><button class="btn btn-sm" data-vrestore="${v.id}">恢复此版本</button></td></tr>`).join("")}
      </tbody>
    </table>`
    : `<p style="color:var(--ink-soft)">还没有版本记录。重新生成/采用新稿前会自动快照,也可手动「保存快照」。</p>`;
  $$("[data-vrestore]", box).forEach(b => b.onclick = async () => {
    if (!confirm("恢复该版本?当前稿会先自动快照,不会丢失。")) return;
    await api(`/projects/${state.pid}/chapters/${state.cid}/versions/${b.dataset.vrestore}/restore`, { method: "POST", body: "{}" });
    closeModal();
    await loadProject();
    state.dirty = false;
    toast("已回滚到历史版本");
    renderWrite(view);
  });
}

async function navChapter(delta, view) {
  if (!confirmDiscard()) return;
  const ch = curChapter();
  const i = curChapters().indexOf(ch) + delta;
  if (i < 0 || i >= curChapters().length) return;
  state.cid = curChapters()[i].id;
  renderWrite(view);
}

/* ---------------- 审校页 ---------------- */

function renderReview(view) {
  const ch = curChapter();
  const review = ch && ch.review ? safeJSON(ch.review) : null;
  view.innerHTML = `
    <div class="card">
      <div class="row">
        <select id="rsel" style="max-width:340px">${chapterOptions()}</select>
        <span style="flex:1"></span>
        <button class="btn" data-lock id="btnReview" ${ch && (ch.content || "").trim() ? "" : "disabled"}>🔍 运行审校</button>
        <button class="btn" data-lock id="btnRevise" ${review ? "" : "disabled"}>✦ 按意见修订(生成新稿)</button>
        <button class="btn btn-primary" data-lock id="btnFinalize" ${ch && (ch.content || "").trim() ? "" : "disabled"}>✔ 定稿(更新摘要与状态)</button>
      </div>
      <div class="stream-box" id="r-stream"></div>
      <div id="revise-area"></div>
    </div>
    ${ch && (ch.content || "").trim() ? `
    <div class="card" id="review-result">
      ${review ? renderReviewResult(review) : `<p style="color:var(--ink-soft)">本章尚未审校。点击「运行审校」,获得评分、问题清单与修改建议。</p>`}
    </div>` : `<div class="card"><p style="color:var(--ink-soft)">本章还没有正文。</p></div>`}
  `;
  if (!ch) { $("#view").innerHTML = `<div class="card"><p style="color:var(--ink-soft)">请先在大纲页建立章节。</p></div>`; return; }

  $("#rsel").onchange = () => { if (confirmDiscard()) { state.cid = parseInt($("#rsel").value, 10); renderReview(view); } };
  $("#btnReview").onclick = async () => {
    const box = $("#r-stream"); box.style.display = "block"; box.textContent = "审校中,请稍候…";
    try {
      const review = await sse(`/projects/${state.pid}/chapters/${state.cid}/review`, {}, { onDelta: () => {} });
      ch.review = JSON.stringify(review);
      toast("审校完成");
    } catch (e) { toast(e.message, true); }
    box.style.display = "none";
    await loadProject(); renderReview(view);
  };
  $("#btnRevise").onclick = () => previewOverwrite(
    `/projects/${state.pid}/chapters/${state.cid}/revise`, "#r-stream", "修订稿(按审校意见)", view, "#revise-area");
  $("#btnFinalize").onclick = async () => {
    const box = $("#r-stream"); box.style.display = "block"; box.textContent = "定稿中:生成摘要 → 抽取人物状态与伏笔…";
    try {
      const r = await sse(`/projects/${state.pid}/chapters/${state.cid}/finalize`, {}, { onDelta: () => {} });
      if (r && r.bundle) state.bundle = r.bundle;
      const st = r && r.state || {};
      const nc = (st.characters || []).length, nf = (st.foreshadowing || []).length;
      toast(`已定稿。更新人物 ${nc} 项,伏笔变化 ${nf} 项`);
    } catch (e) { toast(e.message, true); }
    box.style.display = "none";
    await loadProject(); renderReview(view);
  };
}

function renderReviewResult(r) {
  const dims = r.dimensions || {};
  const issues = r.issues || [];
  const sugg = r.suggestions || [];
  return `
    <h3>审校结果</h3>
    <div class="score-wrap" style="margin:14px 0;">
      <div class="score-big">${r.overall != null ? r.overall : "—"}<small> / 100</small></div>
      <div class="dim">${Object.entries(dims).map(([k, v]) => `
        <div class="dim-name"><span>${esc(k)}</span><span>${v}</span></div>
        <div class="bar"><i style="width:${Math.max(0, Math.min(100, v || 0))}%"></i></div>`).join("")}
      </div>
    </div>
    <h3 style="margin-top:14px">问题清单(${issues.length})</h3>
    ${issues.map(i => `<div class="issue"><span class="badge b-${esc(i.severity || "低")}">${esc(i.severity || "低")}</span><span class="badge">${esc(i.type || "其他")}</span> ${esc(i.desc)}</div>`).join("") || `<p style="color:var(--ink-soft);font-size:13px;">未发现明显问题。</p>`}
    <h3 style="margin-top:14px">修改建议</h3>
    <ul class="sugg">${sugg.map(s => `<li>${esc(s)}</li>`).join("") || "<li>无</li>"}</ul>
  `;
}

/* 润色/修订/两步去AI味:先流式预览,再由作者决定是否采用 */
async function previewOverwrite(url, streamSel, title, view, areaSel, onDone) {
  const area = areaSel ? $(areaSel) : $("#revise-area");
  if (!area) return;
  if (!confirmDiscard()) return;
  area.innerHTML = `
    <div class="card" style="border-color:var(--gold)">
      <h3>${esc(title)} <span class="hint">预览中,未保存 —— 采用前原稿保持不变</span></h3>
      <textarea id="pv-text" rows="16" style="font-size:14px;line-height:1.85"></textarea>
      <div class="row row-end" style="margin-top:10px;">
        <button class="btn" id="pv-discard">放弃新稿</button>
        <button class="btn btn-primary" id="pv-adopt" disabled>采用新稿并保存</button>
      </div>
    </div>`;
  const pv = $("#pv-text");
  const box = $(streamSel);
  if (box) { box.style.display = "block"; box.textContent = "生成中…"; }
  let text = "";
  let result = null;
  try {
    result = await sse(url, {}, {
      onDelta: t => {
        text += t;
        pv.value = text;
        pv.scrollTop = pv.scrollHeight;
        if (box) box.textContent = text.length + " 字…";
      },
    });
    $("#pv-adopt").disabled = false;
    if (box) box.style.display = "none";
  } catch (e) {
    toast(e.message, true);
    area.innerHTML = "";
    if (box) box.style.display = "none";
    return;
  }
  if (onDone && result) onDone(result, box);
  // 生成接口返回结构化结果(如两步去AI味的 text/spans/note):用干净的最终文本覆盖流式拼接,
  // 避免过程状态行(如「[检测] 命中 N 处…」)混进预览稿被采用
  if (result && typeof result.text === "string") { pv.value = result.text; text = result.text; }
  $("#pv-adopt").onclick = async () => {
    await api(`/projects/${state.pid}/chapters/${state.cid}/adopt`, {
      method: "POST", body: JSON.stringify({ content: pv.value, note: title }),
    });
    state.dirty = false;
    toast("已采用新稿(原稿已自动快照,可在版本历史找回)");
    await loadProject();
    switchTab(state.tab);
  };
  $("#pv-discard").onclick = () => { area.innerHTML = ""; toast("已放弃新稿,原稿未动"); };
}

/* ---------------- 导出页 ---------------- */

async function renderExport(view) {
  const chapters = curChapters();
  const written = chapters.filter(c => (c.content || "").trim());
  const words = written.reduce((s, c) => s + (c.content || "").length, 0);
  let usage = { calls: 0, approx_tokens: 0 };
  try { usage = await api(`/projects/${state.pid}/usage`); } catch (e) { /* ignore */ }
  view.innerHTML = `
    <div class="card">
      <h3>作品统计</h3>
      <div class="row" style="font-size:14px;">
        <span class="badge">共 ${chapters.length} 章</span>
        <span class="badge">已写 ${written.length} 章</span>
        <span class="badge">约 ${words} 字</span>
        <span class="badge">模型调用 ${usage.calls} 次</span>
        <span class="badge">约 ${usage.approx_tokens} tokens(估算)</span>
      </div>
    </div>
    <div class="card">
      <h3>导出</h3>
      <div class="row">
        <button class="btn" id="exp-txt">导出 TXT</button>
        <button class="btn" id="exp-md">导出 Markdown</button>
        <button class="btn" id="exp-docx">导出 DOCX</button>
        <button class="btn" id="exp-epub">导出 EPUB</button>
      </div>
    </div>
    <div class="card">
      <h3>导入拆书 <span class="hint">导入已有 txt 小说,按「第X章」自动拆分,可在此基础上续写或研究结构</span></h3>
      <div class="row">
        <input type="file" id="imp-file" accept=".txt,.md" style="max-width:320px">
        <button class="btn" id="btnImport">导入并拆章</button>
      </div>
      <div class="statusline" id="imp-status"></div>
    </div>
  `;
  $("#exp-txt").onclick = () => window.open(`/api/projects/${state.pid}/export?format=txt`);
  $("#exp-md").onclick = () => window.open(`/api/projects/${state.pid}/export?format=md`);
  $("#exp-docx").onclick = () => window.open(`/api/projects/${state.pid}/export?format=docx`);
  $("#exp-epub").onclick = () => window.open(`/api/projects/${state.pid}/export?format=epub`);
  $("#btnImport").onclick = async () => {
    const f = $("#imp-file").files[0];
    if (!f) { toast("请先选择 txt/md 文件", true); return; }
    if (chapters.length && !confirm("导入将覆盖现有全部章节,确定?")) return;
    $("#imp-status").textContent = "读取文件中…";
    const text = await f.text();
    $("#imp-status").textContent = "拆章中…";
    try {
      const r = await api(`/projects/${state.pid}/import`, { method: "POST", body: JSON.stringify({ text }) });
      state.bundle = r.bundle;
      state.cid = curChapters()[0] ? curChapters()[0].id : null;
      toast(`已导入 ${r.count} 章`);
    } catch (e) { toast(e.message, true); }
    $("#imp-status").textContent = "";
    await loadProject(); renderExport(view);
  };
}

/* ---------------- 设置模态框 ---------------- */

function openSettings() {
  const s = state.settings;
  const gen = s.gen || {};
  let providers = [];
  api("/providers").then(list => { providers = list || []; fillPresetSelect(); }).catch(() => {});
  openModal("设置 · 模型档案", `
    <p style="font-size:12.5px;color:var(--ink-soft);margin-bottom:12px;">
      任何 OpenAI 兼容接口均可。从模板添加可一键填好 Base URL 与默认模型,只需再粘贴 API Key;
      不知道模型名时点「拉取模型」直接从服务拉列表。「强模型」负责大纲与正文等创意任务,「快模型」负责摘要与状态抽取。
      base_url 填 <code>mock://local</code> 即为离线演示模式。</p>
    <div class="row" style="margin-bottom:12px;">
      <select id="s-preset" style="max-width:280px"><option value="">— 从服务商模板添加 —</option></select>
      <button class="btn btn-sm" id="btnAddFromPreset">＋ 用模板添加档案</button>
      <span style="flex:1"></span>
      <button class="btn btn-sm" id="btnAddProfile">＋ 空白档案</button>
    </div>
    <div id="profiles">
      ${(s.profiles || []).map((p, i) => profileRow(p, i)).join("")}
    </div>
    <datalist id="dl-models"></datalist>
    <div class="grid3" style="margin-top:16px;">
      <div><label class="fld">强模型档案(大纲/正文/审校)</label>
        <select id="s-strong">${(s.profiles || []).map(p => `<option ${p.name === s.strong_profile ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>
      <div><label class="fld">快模型档案(摘要/状态抽取)</label>
        <select id="s-fast">${(s.profiles || []).map(p => `<option ${p.name === s.fast_profile ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>
      <div><label class="fld">Embedding 档案(可选,向量语义检索)</label>
        <select id="s-embed"><option value="">(不启用,用关键词 BM25)</option>
          ${(s.profiles || []).map(p => `<option ${p.name === s.embedding_profile ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>
    </div>
    <p style="font-size:12px;color:var(--ink-soft);margin-top:4px;">
      Embedding 档案需服务支持 /embeddings(如 OpenAI、硅基流动、智谱);启用后旧情节检索升级为向量余弦,异常自动回退 BM25。</p>
    <h3 style="margin-top:16px;font-size:14px;">生成参数</h3>
    <div class="grid2">
      <div><label class="fld">每章目标字数</label><input type="number" id="s-words" value="${gen.chapter_words || 3000}"></div>
      <div><label class="fld">上下文预算(字符)</label><input type="number" id="s-ctx" value="${gen.context_chars || 14000}"></div>
      <div><label class="fld">前情摘要窗口(章)</label><input type="number" id="s-window" value="${gen.summary_window || 6}"></div>
      <div><label class="fld">旧情节检索条数</label><input type="number" id="s-topk" value="${gen.retrieval_topk || 3}"></div>
    </div>
  `, async () => {
    const profiles = [];
    $$(".profile-row").forEach(row => {
      const name = $("[data-pname]", row).value.trim();
      if (!name) return;
      profiles.push({
        name,
        base_url: $("[data-purl]", row).value.trim(),
        api_key: $("[data-pkey]", row).value.trim(),
        model: $("[data-pmodel]", row).value.trim(),
        temperature: parseFloat($("[data-ptemp]", row).value) || 0.8,
        max_tokens: parseInt($("[data-pmt]", row).value, 10) || 8192,
      });
    });
    if (!profiles.length) { toast("至少保留一个模型档案", true); return; }
    const strong = $("#s-strong").value, fast = $("#s-fast").value;
    await api("/settings", {
      method: "PUT",
      body: JSON.stringify({
        profiles,
        strong_profile: profiles.some(p => p.name === strong) ? strong : profiles[0].name,
        fast_profile: profiles.some(p => p.name === fast) ? fast : profiles[profiles.length - 1].name,
        embedding_profile: profiles.some(p => p.name === $("#s-embed").value) ? $("#s-embed").value : "",
        gen: {
          chapter_words: parseInt($("#s-words").value, 10) || 3000,
          context_chars: parseInt($("#s-ctx").value, 10) || 14000,
          summary_window: parseInt($("#s-window").value, 10) || 6,
          retrieval_topk: parseInt($("#s-topk").value, 10) || 3,
        },
      }),
    });
    closeModal();
    await refreshSettings();
    toast("设置已保存");
  }, "保存设置");

  const fillPresetSelect = () => {
    const sel = $("#s-preset");
    if (!sel) return;
    sel.innerHTML = `<option value="">— 从服务商模板添加 —</option>` +
      providers.map((p, i) => `<option value="${i}">${esc(p.label)}</option>`).join("");
  };
  const addProfileRow = (p) => {
    const div = document.createElement("div");
    div.innerHTML = profileRow(p, Date.now());
    $("#profiles").appendChild(div.firstElementChild);
    bindProfileRows();
  };
  $("#btnAddProfile").onclick = () => addProfileRow({ name: "", base_url: "", api_key: "", model: "", temperature: 0.8, max_tokens: 8192 });
  $("#btnAddFromPreset").onclick = () => {
    const p = providers[parseInt($("#s-preset").value, 10)];
    if (!p) { toast("请先选择一个服务商模板", true); return; }
    addProfileRow({
      name: p.label, base_url: p.base_url, api_key: "", model: p.model || "",
      temperature: 0.8, max_tokens: 8192, _preset_note: p.note ? `${p.note}${p.key_url ? " · 取 Key: " + p.key_url : ""}` : "",
    });
  };
  bindProfileRows();
}

function profileRow(p, i) {
  return `<div class="profile-row" data-pi="${i}">
    <div class="grid4">
      <div><label class="fld">档案名</label><input type="text" data-pname value="${esc(p.name)}" placeholder="主力"></div>
      <div><label class="fld">Base URL</label><input type="text" data-purl value="${esc(p.base_url)}" placeholder="https://api.deepseek.com/v1"></div>
      <div><label class="fld">模型</label><input type="text" data-pmodel list="dl-models" value="${esc(p.model)}" placeholder="deepseek-chat"></div>
      <div><label class="fld">API Key</label><input type="password" data-pkey value="${esc(p.api_key)}" placeholder="sk-…(本地服务可留空)"></div>
    </div>
    <div class="grid3">
      <div><label class="fld">温度</label><input type="number" step="0.05" data-ptemp value="${p.temperature ?? 0.8}"></div>
      <div><label class="fld">max_tokens</label><input type="number" data-pmt value="${p.max_tokens ?? 8192}"></div>
      <div style="display:flex;align-items:flex-end;gap:8px;padding-bottom:2px;">
        <button class="btn btn-sm" data-pmodels title="从该服务拉取可用模型列表,点击下方模型输入框即可选择">拉取模型</button>
        <button class="btn btn-sm" data-ptest>测试</button>
        <button class="btn btn-sm btn-danger" data-pdel>删除</button>
      </div>
    </div>
    <div class="statusline" data-pstatus>${esc(p._preset_note || "")}</div>
  </div>`;
}

function bindProfileRows() {
  $$("[data-pmodels]").forEach(b => b.onclick = async () => {
    const row = b.closest(".profile-row");
    const st = $("[data-pstatus]", row);
    st.textContent = "拉取模型列表中…";
    try {
      const r = await api("/llm/models", {
        method: "POST",
        body: JSON.stringify({
          base_url: $("[data-purl]", row).value.trim(),
          api_key: $("[data-pkey]", row).value.trim(),
        }),
      });
      if (!r.ok) { st.textContent = "✗ " + r.error; return; }
      const dl = $("#dl-models");
      dl.innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
      st.textContent = `✓ 共 ${r.models.length} 个模型:点击右侧「模型」输入框查看下拉列表`;
    } catch (e) { st.textContent = "✗ " + e.message; }
  });
  $$("[data-ptest]").forEach(b => b.onclick = async () => {
    const row = b.closest(".profile-row");
    const st = $("[data-pstatus]", row);
    st.textContent = "测试中…";
    try {
      const name = $("[data-pname]", row).value.trim();
      const profiles = [];
      $$(".profile-row").forEach(r => profiles.push({
        name: $("[data-pname]", r).value.trim(),
        base_url: $("[data-purl]", r).value.trim(),
        api_key: $("[data-pkey]", r).value.trim(),
        model: $("[data-pmodel]", r).value.trim(),
        temperature: parseFloat($("[data-ptemp]", r).value) || 0.8,
        max_tokens: parseInt($("[data-pmt]", r).value, 10) || 8192,
      }));
      await api("/settings", { method: "PUT", body: JSON.stringify({ profiles, strong_profile: name, fast_profile: name, embedding_profile: state.settings.embedding_profile || "", gen: state.settings.gen }) });
      await refreshSettings();
      const r = await api("/llm/test", { method: "POST", body: JSON.stringify({ name }) });
      st.textContent = r.ok ? `✓ 连接成功:${r.reply}` : `✗ ${r.error}`;
    } catch (e) { st.textContent = "✗ " + e.message; }
  });
  $$("[data-pdel]").forEach(b => b.onclick = () => {
    if ($$(".profile-row").length <= 1) { toast("至少保留一个档案", true); return; }
    b.closest(".profile-row").remove();
  });
}

/* ---------------- 模态框 ---------------- */

function openModal(title, bodyHTML, onOk, okText) {
  const root = $("#modalRoot");
  root.innerHTML = `
    <div class="modal-mask">
      <div class="modal">
        <div class="modal-head"><h3>${esc(title)}</h3><button class="x">✕</button></div>
        <div class="modal-body">${bodyHTML}</div>
        <div class="modal-foot">
          <button class="btn" id="m-cancel">取消</button>
          <button class="btn btn-primary" id="m-ok">${esc(okText || "确定")}</button>
        </div>
      </div>
    </div>`;
  const close = () => { root.innerHTML = ""; };
  $(".x", root).onclick = close;
  $("#m-cancel").onclick = close;
  $(".modal-mask", root).onclick = (e) => { if (e.target.classList.contains("modal-mask")) close(); };
  $("#m-ok").onclick = () => onOk(close);
}

function closeModal() { $("#modalRoot").innerHTML = ""; }

function safeJSON(s) {
  try { return JSON.parse(s); } catch (e) { return null; }
}

boot();
