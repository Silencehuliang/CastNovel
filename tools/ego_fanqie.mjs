// 番茄作家后台 ego-browser 驱动(CastNovel 平台发布/同步)
//
// I/O 约定(避免命令行/环境变量编码问题):
//   读  E:/Development/Agents_Project/zcode_project/xiaoshuo/data/publish_task/task.json
//       { action: "list"|"create"|"update",
//         space?, book_id, volume?, serial?, title?, body?, chapter_id?,
//         with_ids?(list:缺ID的行逐个点编辑读URL,慢), ai_flag?("yes"/"no",默认no),
//         dry?(只验证不提交:list不变;create=只校验分卷与按钮;update=只打开编辑页核对) }
//   写  E:/Development/Agents_Project/zcode_project/xiaoshuo/data/publish_task/result.json
//       { ok, action, rows?, chapter_id?, words?, row?, editor?, error?, log[] }
//
// 运行:ego-browser nodejs < tools/ego_fanqie.mjs
//
// 站点经验(2026-10 实测,详见 ~/.codebuddy/skills/Web Access…/site-patterns/fanqienovel.com.md):
//   - 编辑器 React+arco-design+ProseMirror;标题/章节号用原生 setter + input/change 事件;
//   - 正文必须 全选→等1秒→派发 paste→补一次 insertText/delete 触发事务,等「已保存」;
//   - 更新编辑页可能先渲染空壳(pm/标题框存在但内容为空):必须等标题/章节号/正文都载入
//     再粘贴,否则会往空编辑器里写(空壳只重载一次即可);
//   - 「是否使用AI」radio value=1是/2否,必须点 label;
//   - 新建章节会新开标签页;同一作品开多个编辑页会触发「继续编辑」弹窗,驱动保持单页策略;
//   - 列表字数/发布时间延迟更新(可能数分钟),不能作为提交成功判据;以「发布流程弹窗关闭」+
//     重新打开编辑页核对正文为准;

import { readFile, writeFile, mkdir } from "node:fs/promises";

const DIR = "E:/Development/Agents_Project/zcode_project/xiaoshuo/data/publish_task";
const LIST_URL = (book) => `https://fanqienovel.com/main/writer/chapter-manage/${book}?type=1`;
const EDIT_URL = (book, cid) => `https://fanqienovel.com/main/writer/${book}/publish/${cid}/`;
const NEW_URL = (book) => `https://fanqienovel.com/main/writer/${book}/publish/?enter_from=newchapter`;

let task;
try {
  task = JSON.parse(await readFile(`${DIR}/task.json`, "utf-8"));
} catch (e) {
  console.log("FATAL: cannot read task.json:", e.message);
  process.exit(1);
}

const log = [];
const result = { ok: false, action: task.action, log };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const AI_WANT = (task.ai_flag === "yes") ? "1" : "2";

function fail(msg) { result.error = String(msg); log.push("ERROR: " + msg); }

// ---------- 通用页面操作 ----------

async function gotoTolerant(page, url) {
  // SPA 首屏慢:超时不代表导航失败,后续自会轮询页面状态
  try { await page.goto(url); log.push(`goto ${url.slice(-46)}`); }
  catch (e) { log.push(`goto timeout(continue): ${url.slice(-46)}`); }
}

const JS_STATE = () => {
  const pm = document.querySelector(".ProseMirror");
  const ti = document.querySelector("input.serial-editor-input-hint-area");
  const si = document.querySelector("input.serial-input");
  const modals = [...document.querySelectorAll('[class*="modal-wrapper"]')]
    .map((m) => (m.innerText || "").replace(/\s+/g, " ").trim().slice(0, 120)).filter(Boolean);
  return {
    url: location.href,
    pm: !!pm, ti: !!ti, si: !!si,
    title: ti ? ti.value : "",
    serial: si ? si.value : "",
    len: pm ? pm.innerText.length : -1,
    modals,
  };
};

async function editorReady(page, { deadlineMs = 45000, requireContent = false } = {}) {
  const t0 = Date.now();
  while (Date.now() - t0 < deadlineMs) {
    const st = await page.evaluate(JS_STATE);
    if (!/\/main\/writer\//.test(st.url) || /passport|login|sso\./.test(st.url)) {
      throw new Error("NOT_LOGIN:编辑页跳转到了登录页");
    }
    // 「有刚刚更新的章节,是否继续编辑」弹窗:点「继续」
    if (st.modals.some((m) => /刚刚更新|继续编辑/.test(m))) {
      const hit = await page.evaluate(() => {
        const m = [...document.querySelectorAll('[class*="modal-wrapper"]')]
          .find((m) => /刚刚更新|继续编辑/.test(m.innerText || ""));
        if (!m) return false;
        const b = [...m.querySelectorAll("button")].find((x) => /继续|确定/.test((x.innerText || "").trim()));
        if (b) { b.click(); return true; }
        return false;
      });
      if (hit) { log.push("handled continue-edit modal"); await sleep(1500); }
    }
    // 更新已有章节:必须等标题/章节号/正文都载入,避免往空壳编辑器里粘贴
    const loaded = !requireContent
      || (st.len > 20 && String(st.title).trim() && String(st.serial).trim());
    if (st.pm && st.ti && loaded) return st;
    await sleep(1200);
  }
  throw new Error(requireContent ? "EDITOR_CONTENT_NOT_LOADED:章节内容未载入" : "EDITOR_NOT_READY");
}

async function scrapeAllPages(page, bookId) {
  const all = [];
  for (let p = 0; p < 50; p++) {
    await waitListSettled(page);
    const rows = await page.evaluate(scrapeRowsJS(String(bookId)));
    all.push(...rows);
    log.push(`page ${p + 1}: ${rows.length} rows`);
    const next = await page.evaluate(() => {
      const cands = [...document.querySelectorAll(
        '.arco-pagination-item-next, [class*="pagination"] [class*="next"], li[aria-label*="next" i]'
      )];
      for (const b of cands) {
        if (b.getAttribute("aria-disabled") === "true" || /disabled/.test(b.className || "")) continue;
        const r = b.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) { b.click(); return true; }
      }
      return false;
    });
    if (!next) break;
    await sleep(2500);
  }
  const map = new Map();
  for (const r of all) map.set(r.serial, r);
  return [...map.values()].sort((a, b) => a.serial - b.serial);
}

async function waitListSettled(page, deadlineMs = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < deadlineMs) {
    const st = await page.evaluate(() => ({
      url: location.href,
      n: [...document.querySelectorAll("tr")]
        .filter((t) => /^第\s*\d+\s*章/.test((t.innerText || "").trim())).length,
      head: document.body ? (document.body.innerText || "").slice(0, 300) : "",
      empty: /暂无章节|还没有章节|暂无内容|没有章节|暂无作品|空空如也/.test(
        document.body ? document.body.innerText || "" : ""),
    }));
    if (/passport|sso\.|\/login/.test(st.url)) throw new Error("NOT_LOGIN:章节列表跳到了登录页");
    if (/扫码登录|请登录/.test(st.head)) throw new Error("NOT_LOGIN:页面要求登录");
    if (st.n > 0) return "rows";
    // 空卷:列表无行但页面已稳定(新建章节按钮可见 / 明确提示暂无章节)
    if (st.empty || /新建章节|新建目录/.test(st.head)) return "empty";
    if (/加载中|loading/i.test(st.head)) { await sleep(1200); continue; }
    await sleep(1200);
  }
  throw new Error("LIST_TIMEOUT:等不到章节行,url=" + (await page.url()));
}

function scrapeRowsJS(bookId) {
  return () => {
    const out = [];
    for (const tr of document.querySelectorAll("tr")) {
      const text = (tr.innerText || "").trim();
      const m = text.match(/^第\s*(\d+)\s*章/);
      if (!m) continue;
      const serial = parseInt(m[1], 10);
      const lines = text.split("\n").map((s) => s.trim()).filter(Boolean);
      let title = "";
      for (let i = 0; i < lines.length; i++) {
        const lm = lines[i].match(/^第\s*\d+\s*章\s*(.*)$/);
        if (!lm) continue;
        title = (lm[1] || "").trim();
        if (!title && lines[i + 1] && !/^(编辑|删除|\d+\s*字)$/.test(lines[i + 1])) title = lines[i + 1];
        break;
      }
      let words = 0;
      const wm = text.match(/(\d+)\s*字/);
      if (wm) words = parseInt(wm[1], 10);
      else {
        for (const td of tr.querySelectorAll("td,span")) {
          const c = (td.innerText || "").trim();
          if (/^\d{3,}$/.test(c)) { words = parseInt(c, 10); break; }
        }
      }
      let cid = "";
      const a = tr.querySelector('a[href*="/publish/"]');
      if (a) {
        const hm = (a.getAttribute("href") || "").match(/\/publish\/(\d+)/);
        if (hm) cid = hm[1];
      }
      if (!cid) {
        // React props 里找 onClick 源码中的长数字(章节 ID;排除书 ID)
        outer:
        for (const el of [tr, ...tr.querySelectorAll("*")]) {
          for (const k of Object.keys(el)) {
            if (!k.startsWith("__reactProps$")) continue;
            const p = el[k];
            const src = p && p.onClick ? String(p.onClick) : "";
            for (const id of src.match(/\d{14,}/g) || []) {
              if (id !== bookId) { cid = id; break outer; }
            }
          }
        }
      }
      out.push({ serial, title, words, cid });
    }
    return out;
  };
}

async function volumeNames(page) {
  await page.evaluate(() => {
    const el = document.querySelector(".serial-select.flat-serial-select");
    if (el) el.click();
  });
  await sleep(900);
  const opts = await page.evaluate(() =>
    [...document.querySelectorAll(".byte-select-option")]
      .map((e) => (e.innerText || "").trim()).filter(Boolean)
  );
  await page.keyboard.press("Escape");
  await sleep(400);
  return opts;
}

async function ensureVolume(page, name) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const cur = await page.evaluate(() => {
      const el = document.querySelector(".serial-select.flat-serial-select");
      return el ? (el.innerText || "").trim() : "NONE";
    });
    if (cur !== "NONE" && cur.includes(name)) { log.push(`volume ok: ${cur}`); return; }
    if (cur === "NONE") { await sleep(1500); continue; }
    await page.evaluate(() => {
      const el = document.querySelector(".serial-select.flat-serial-select");
      if (el) el.click();
    });
    await sleep(900);
    const picked = await page.evaluate((n) => {
      const opt = [...document.querySelectorAll(".byte-select-option")]
        .find((e) => (e.innerText || "").includes(n));
      if (!opt) return "NO_OPT";
      opt.click();
      return "PICKED";
    }, name);
    if (picked !== "PICKED") {
      await page.keyboard.press("Escape");
      log.push(`volume option not found (attempt ${attempt + 1}): ${name}`);
      await sleep(1200);
      continue;
    }
    await sleep(1800);
  }
  const fin = await page.evaluate(() => {
    const el = document.querySelector(".serial-select.flat-serial-select");
    return el ? (el.innerText || "").trim() : "NONE";
  });
  if (fin.includes(name)) { log.push(`volume switched: ${fin}`); return; }
  throw new Error(`VOLUME_SWITCH_FAILED:要「${name}」实际「${fin}」`);
}

async function waitSaved(page, maxMs = 45000) {
  const t0 = Date.now();
  let sawSaving = false;
  while (Date.now() - t0 < maxMs) {
    const st = await page.evaluate(() => {
      const t = document.body ? document.body.innerText : "";
      const pm = document.querySelector(".ProseMirror");
      return { saving: /保存中|正在保存/.test(t), saved: /已保存/.test(t), len: pm ? pm.innerText.length : -1 };
    });
    if (st.saving) sawSaving = true;
    if (st.saved && !st.saving) return st;
    // 页面没有保存指示器:至少等 12 秒兜底再放行
    if (!sawSaving && Date.now() - t0 > 12000) return st;
    await sleep(1500);
  }
  return null;
}

async function setSerial(page, serial) {
  return page.evaluate((v) => {
    const si = document.querySelectorAll("input.serial-input")[0];
    if (!si) return "NO_SERIAL";
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(si, String(v));
    si.dispatchEvent(new Event("input", { bubbles: true }));
    si.dispatchEvent(new Event("change", { bubbles: true }));
    si.blur();
    return "SERIAL:" + si.value;
  }, serial);
}

async function setTitle(page, title) {
  return page.evaluate((v) => {
    const ti = document.querySelector("input.serial-editor-input-hint-area");
    if (!ti) return "NO_TITLE";
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(ti, v);
    ti.dispatchEvent(new Event("input", { bubbles: true }));
    ti.dispatchEvent(new Event("change", { bubbles: true }));
    ti.blur();
    return "TITLE:" + ti.value;
  }, title);
}

async function fillBody(page, body) {
  await page.evaluate(() => {
    const pm = document.querySelector(".ProseMirror");
    pm.focus();
    document.execCommand("selectAll", false, null);
  });
  await sleep(1100);
  const paste = await page.evaluate((TEXT) => {
    const pm = document.querySelector(".ProseMirror");
    if (!pm) return "NO_PM";
    pm.focus();
    const dt = new DataTransfer();
    dt.setData("text/plain", TEXT);
    pm.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    return "PASTE:" + pm.innerText.length + "/" + TEXT.length;
  }, body);
  log.push(paste);
  await sleep(1200);
  // 触发 ProseMirror transaction,自动保存才会真正执行
  const nudge = await page.evaluate(() => {
    const pm = document.querySelector(".ProseMirror");
    if (!pm) return "NO_PM";
    pm.focus();
    document.execCommand("insertText", false, "。");
    document.execCommand("delete", false, null);
    return "NUDGE:" + pm.innerText.length;
  });
  log.push(nudge);
  return paste;
}

async function clickBtnByText(page, text, { last = false, visibleOnly = true } = {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const hit = await page.evaluate(({ text, last, visibleOnly }) => {
      let bs = [...document.querySelectorAll("button")]
        .filter((x) => (x.innerText || "").trim() === text);
      if (visibleOnly) {
        const vis = bs.filter((x) => x.offsetParent !== null);
        if (vis.length) bs = vis;
      }
      if (!bs.length) return false;
      (last ? bs[bs.length - 1] : bs[0]).click();
      return true;
    }, { text, last, visibleOnly });
    if (hit) { log.push(`click ${text}`); return true; }
    await sleep(1600);
  }
  log.push(`click MISS: ${text}`);
  return false;
}

async function chooseAI(page) {
  let r = "LOOP_NOT_RUN";
  for (let attempt = 0; attempt < 3; attempt++) {
    r = await page.evaluate((want) => {
      const modal = [...document.querySelectorAll('[class*="modal-wrapper"]')]
        .find((m) => (m.innerText || "").includes("是否使用AI"));
      if (!modal) return "NO_AI_MODAL";
      const radios = [...modal.querySelectorAll('input[type="radio"]')];
      const target = radios.find((x) => x.value === want);
      if (!target) return "NO_RADIO";
      (target.closest("label") || target.parentElement).click();
      return "CHECKED:" + radios.map((x) => x.value + "=" + x.checked).join(",");
    }, AI_WANT);
    if (r.startsWith("CHECKED") && r.includes(`${AI_WANT}=true`)) { log.push("ai=" + AI_WANT); return true; }
    await sleep(1200);
  }
  log.push("ai radio MISS: " + r);
  return false;
}

async function waitSubmitted(page) {
  for (let i = 0; i < 20; i++) {
    await sleep(1500);
    const st = await page.evaluate(() => {
      const ai = [...document.querySelectorAll('[class*="modal-wrapper"]')]
        .some((m) => (m.innerText || "").includes("是否使用AI"));
      const t = document.body ? document.body.innerText : "";
      return { ai, ok: /发布成功|提交成功|操作成功/.test(t) };
    });
    if (!st.ai) return st;
  }
  return null;
}

async function waitNewTab(ts, beforeIds, timeoutMs = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const t = (await ts.tabs()).find((x) => !beforeIds.has(x.targetId));
    if (t) return t;
    await sleep(1000);
  }
  return null;
}

// ---------- 主流程 ----------

async function main() {
  await mkdir(DIR, { recursive: true });
  const spaceName = task.space || "castnovel-fanqie";
  let ts;
  try { ts = await taskSpace(spaceName); }
  catch (e) {
    const all = await listTaskSpaces();
    const mine = all.find((s) => s.name === spaceName);
    if (!mine) throw new Error(`TASK_SPACE_FAIL:${e.message || e}`);
    ts = await takeOverTaskSpace(mine.id);
    log.push(`took over space ${mine.id}`);
  }
  result.spaceId = ts.spaceId;
  log.push(`space ${ts.spaceId} (${spaceName})`);

  // 主页面:始终用 p1;丢失则补建
  let p1;
  const tabs = await ts.tabs();
  if (tabs.some((t) => t.label === "p1")) p1 = ts.page("p1");
  else { p1 = await ts.newPage(); log.push("p1 recreated"); }

  // 清扫上次异常留下的编辑页标签(避免「继续编辑」弹窗连锁)
  for (const t of await ts.tabs()) {
    if (t.label && t.label !== p1.label && /\/main\/writer\/.+\/publish\//.test(t.url || "")) {
      try { await ts.page(t.label).close(); log.push(`closed stale editor ${t.label}`); } catch {}
    }
  }

  const book = String(task.book_id);
  if (!book) throw new Error("NO_BOOK_ID");

  if (task.action === "list") {
    await gotoTolerant(p1, LIST_URL(book));
    await waitListSettled(p1);
    // 列表页按分卷过滤:遍历每个分卷抓全量,再按序号去重
    const vols = await volumeNames(p1);
    result.volumes = vols;
    result.warnings = [];
    log.push("volumes: " + JSON.stringify(vols));
    const all = [];

    const collect = async (label) => {
      const state = await waitListSettled(p1);
      if (state === "empty") { log.push(`volume empty: ${label}`); return; }
      const got = await scrapeAllPages(p1, book);
      for (const r of got) r.volume = label;
      all.push(...got);
    };

    if (vols.length <= 1) {
      try { await collect("(default)"); }
      catch (e) { result.warnings.push(`(default): ${e.message}`); log.push(`list failed: ${e.message}`); }
    } else {
      for (const v of vols) {
        try {
          await ensureVolume(p1, v);
          await collect(v);
        } catch (e) {
          // 空卷/站点跳转异常:退回列表页重试一次,仍失败则记警告继续其它分卷
          log.push(`volume retry: ${v} -> ${e.message}`);
          try {
            await gotoTolerant(p1, LIST_URL(book));
            await waitListSettled(p1);
            await ensureVolume(p1, v);
            await collect(v);
          } catch (e2) {
            result.warnings.push(`${v}: ${e2.message}`);
            log.push(`volume failed: ${v} -> ${e2.message}`);
            try { await gotoTolerant(p1, LIST_URL(book)); await waitListSettled(p1); } catch {}
          }
        }
      }
    }
    const map = new Map();
    for (const r of all) map.set(r.serial, r);
    const rows = [...map.values()].sort((a, b) => a.serial - b.serial);
    result.rows = rows;
    if (task.with_ids) {
      const missing = rows.filter((r) => !r.cid);
      log.push(`rows missing cid: ${missing.length}`);
      let curVol = "";
      for (const r of missing.slice(0, task.max_click || 40)) {
        if (r.volume && r.volume !== "(default)" && r.volume !== curVol) {
          try { await ensureVolume(p1, r.volume); curVol = r.volume; }
          catch (e) { log.push(`with_ids volume switch failed ${r.volume}: ${e.message}`); continue; }
        }
        const before = new Set((await ts.tabs()).map((t) => t.targetId));
        const clicked = await page_clickEdit(p1, r.serial);
        let tab = clicked ? await waitNewTab(ts, before, 8000) : null;
        let cid = "";
        if (tab) {
          const pg = tab.label ? ts.page(tab.label) : await ts.adopt(tab.page);
          for (let i = 0; i < 10 && !cid; i++) {
            const m = ((await pg.url()) || "").match(/\/publish\/(\d+)/);
            if (m) cid = m[1]; else await sleep(1000);
          }
          try { await pg.close(); } catch {}
        } else if (clicked) {
          // 少数入口是同页跳转:直接读当前页 URL,再回列表
          for (let i = 0; i < 6 && !cid; i++) {
            const m = ((await p1.url()) || "").match(/\/publish\/(\d+)/);
            if (m) cid = m[1]; else await sleep(800);
          }
          if (cid) {
            await gotoTolerant(p1, LIST_URL(book));
            await waitListSettled(p1);
            curVol = "";
          }
        }
        r.cid = cid;
        log.push(`cid ch${r.serial}: ${clicked} -> ${cid || "FAIL"}`);
        await sleep(600);
      }
    }
    result.ok = true;
    return;
  }

  if (task.action === "create") {
    await gotoTolerant(p1, LIST_URL(book));
    await waitListSettled(p1);
    if (task.volume) await ensureVolume(p1, String(task.volume));
    if (task.dry) {
      const hasBtn = await p1.evaluate(() => {
        const s = [...document.querySelectorAll("span")].find((e) => (e.innerText || "").trim() === "新建章节");
        return !!s;
      });
      result.ok = true; result.dry = true;
      result.volume_ok = true; result.new_chapter_button = hasBtn;
      log.push("dry: volume+button verified, not creating");
      return;
    }
    const before = new Set((await ts.tabs()).map((t) => t.targetId));
    const clicked = await p1.evaluate(() => {
      const s = [...document.querySelectorAll("span")].find((e) => (e.innerText || "").trim() === "新建章节");
      if (!s) return false;
      (s.closest("button") || s.parentElement).click();
      return true;
    });
    if (!clicked) throw new Error("NO_NEW_CHAPTER_BUTTON");
    const tab = await waitNewTab(ts, before);
    if (!tab) throw new Error("NEW_TAB_TIMEOUT:点新建章节后没有新标签页");
    const ep = tab.label ? ts.page(tab.label) : await ts.adopt(tab.page);
    log.push(`editor tab ${ep.label}`);
    try {
      const st0 = await editorReady(ep);
      log.push(`editor url ${st0.url.slice(-50)}`);
      if (task.serial) log.push(await setSerial(ep, task.serial));
      log.push(await setTitle(ep, task.title));
      const paste = await fillBody(ep, task.body);
      const ratio = (() => { const m = String(paste).match(/PASTE:(\d+)\/(\d+)/); return m ? (+m[1]) / (+m[2]) : 0; })();
      if (ratio && ratio < 0.8) throw new Error(`PASTE_RATIO_LOW:${paste}`);
      const saved = await waitSaved(ep);
      log.push("saved: " + JSON.stringify(saved));
      if (!saved || saved.len < 20) throw new Error(`SAVE_UNVERIFIED:${JSON.stringify(saved)}`);
      const nextOk = await clickBtnByText(ep, "下一步");
      if (!nextOk) throw new Error("NO_NEXT_BUTTON");
      await sleep(2500);
      await clickBtnByText(ep, "提交", { last: true });   // 错别字提示弹窗
      await sleep(2500);
      await clickBtnByText(ep, "仅基础检测");             // 仅新建章节有
      await sleep(2000);
      if (!(await chooseAI(ep))) log.push("WARN: AI 弹窗未勾选,尝试继续");
      await sleep(800);
      const confirmOk = await clickBtnByText(ep, "确认提交");
      if (!confirmOk) throw new Error("NO_CONFIRM_BUTTON");
      const sub = await waitSubmitted(ep);
      log.push("submitted: " + JSON.stringify(sub));
      if (!sub) throw new Error("SUBMIT_UNVERIFIED:确认提交后发布流程未结束");
      let cid = "";
      const m = ((await ep.url()) || "").match(/\/publish\/(\d+)/);
      if (m) cid = m[1];
      // 回列表核对(等字数延迟)
      await sleep(4000);
      await gotoTolerant(p1, LIST_URL(book));
      await sleep(2000);
      const rows = await scrapeAllPages(p1, book);
      const row = rows.find((r) => r.serial === Number(task.serial))
        || rows.find((r) => r.title && r.title === task.title);
      result.row = row || null;
      if (!cid && row) cid = row.cid;
      result.chapter_id = cid || "";
      result.words = row ? row.words : 0;
      result.ok = !!(cid || row);
      if (!result.ok) fail("SUBMIT_UNVERIFIED:列表里找不到新章节,请人工核对");
    } finally {
      try { await ep.close(); } catch {}
    }
    return;
  }

  if (task.action === "update") {
    const cid = String(task.chapter_id || "");
    if (!cid) throw new Error("NO_CHAPTER_ID:该章没有平台章节ID,先 pull 或改用 create");
    await gotoTolerant(p1, EDIT_URL(book, cid));
    let st;
    try {
      st = await editorReady(p1, { requireContent: true, deadlineMs: 30000 });
    } catch (e) {
      // SPA 偶发空壳:整页重载一次再等
      log.push(`editor not loaded, reload once: ${e.message}`);
      try { await p1.reload(); } catch {}
      st = await editorReady(p1, { requireContent: true, deadlineMs: 40000 });
    }
    result.editor = st;
    log.push(`editor ${JSON.stringify({ serial: st.serial, title: st.title, len: st.len })}`);
    const serialMismatch = task.serial && st.serial && String(st.serial) !== String(task.serial);
    if (serialMismatch) throw new Error(`SERIAL_MISMATCH:平台第 ${st.serial} 章,本地第 ${task.serial} 章`);
    if (task.dry) {
      result.match = (!task.title || st.title === task.title) && !serialMismatch;
      result.ok = result.match;
      result.dry = true;
      log.push(`dry: opened, title_match=${result.match}, platform_len=${st.len}`);
      if (!result.ok) fail(`DRY_MISMATCH:平台标题「${st.title}」与本地「${task.title}」不一致`);
      return;
    }
    if (task.serial && task.set_serial) log.push(await setSerial(p1, task.serial));
    log.push(await setTitle(p1, task.title));
    const paste = await fillBody(p1, task.body);
    const m = String(paste).match(/PASTE:(\d+)\/(\d+)/);
    const ratio = m ? (+m[1]) / (+m[2]) : 0;
    if (ratio && ratio < 0.8) throw new Error(`PASTE_RATIO_LOW:${paste}`);
    const saved = await waitSaved(p1);
    log.push("saved: " + JSON.stringify(saved));
    if (!saved || saved.len < 20) throw new Error(`SAVE_UNVERIFIED:${JSON.stringify(saved)}`);
    const nextOk = await clickBtnByText(p1, "下一步");
    if (!nextOk) throw new Error("NO_NEXT_BUTTON");
    await sleep(2500);
    await clickBtnByText(p1, "提交", { last: true });
    await sleep(2500);
    if (!(await chooseAI(p1))) log.push("WARN: AI 弹窗未勾选,尝试继续");
    await sleep(800);
    const confirmOk = await clickBtnByText(p1, "确认提交");
    if (!confirmOk) throw new Error("NO_CONFIRM_BUTTON");
    const sub = await waitSubmitted(p1);
    log.push("submitted: " + JSON.stringify(sub));
    if (!sub) throw new Error("SUBMIT_UNVERIFIED:确认提交后发布流程未结束");
    await sleep(3000);
    await gotoTolerant(p1, LIST_URL(book));
    await sleep(2000);
    const rows = await scrapeAllPages(p1, book);
    const row = rows.find((r) => r.serial === Number(task.serial) && Number(task.serial) > 0)
      || rows.find((r) => r.title && r.title === task.title);
    result.row = row || null;
    result.words = row ? row.words : 0;
    result.ok = !!row;
    if (!result.ok) fail("SUBMIT_UNVERIFIED:列表里没找到该章,请人工核对");
    return;
  }

  throw new Error(`UNKNOWN_ACTION:${task.action}`);
}

async function page_clickEdit(page, serial) {
  return page.evaluate((n) => {
    const tr = [...document.querySelectorAll("tr")].find((x) => {
      const m = (x.innerText || "").trim().match(/^第\s*(\d+)\s*章/);
      return m && parseInt(m[1], 10) === n;
    });
    if (!tr) return false;
    const ic = tr.querySelector(".icon-edit")
      || [...tr.querySelectorAll("*")].find((e) => (e.innerText || "").trim() === "编辑" && !e.children.length);
    if (!ic) return false;
    ic.click();
    return true;
  }, serial);
}

try {
  await main();
} catch (e) {
  fail(e && e.message ? e.message : e);
} finally {
  await writeFile(`${DIR}/result.json`, JSON.stringify(result, null, 2), "utf-8");
  console.log(JSON.stringify({ ok: result.ok, error: result.error || undefined, spaceId: result.spaceId }));
}
