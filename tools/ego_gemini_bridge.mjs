// Gemini 网页版桥接(方案 A:ego-browser 驱动 Ego Lite 里的已登录会话)
//
// 固定 I/O 约定(避免命令行/环境变量编码问题):
//   读  E:/Development/Agents_Project/zcode_project/xiaoshuo/data/gemini_task/prompt.txt  提示词全文(UTF-8)
//   读  E:/Development/Agents_Project/zcode_project/xiaoshuo/data/gemini_task/model.txt   可选;存在则尝试切换模型,失败仅警告
//   写  E:/Development/Agents_Project/zcode_project/xiaoshuo/data/gemini_task/reply.txt   回复全文(UTF-8)
//
// 运行:ego-browser nodejs < tools/ego_gemini_bridge.mjs
//
// 2026-10-28 适配记录:
//   - Gemini 前端已出现新组件体系(chat-app/input-area-v2/rich-textarea),回复容器为
//     div.response-container > .response-content(开头含屏幕阅读器文本「Gemini 说」,需剥离);
//   - 新前端「发送」按钮点击无效,必须用 Enter 发送;
//   - 模式选择器按钮在部分页面变体(A/B)中不渲染,切模型是尽力而为。

import { readFile, writeFile } from "node:fs/promises";

const BASE = "E:/Development/Agents_Project/zcode_project/xiaoshuo/data/gemini_task";
const prompt = (await readFile(`${BASE}/prompt.txt`, "utf-8")).trim();
if (!prompt) { console.log("EMPTY_PROMPT"); process.exit(1); }

let model = "";
try { model = (await readFile(`${BASE}/model.txt`, "utf-8")).trim(); } catch {}

// 用户在对话里说「继续」= 明确要求恢复;显式接管任务空间
const spaces = await listTaskSpaces();
const mine = spaces.find(s => s.name === "castnovel-gemini");
if (!mine) { console.log("ERROR: task space not found"); process.exit(10); }
const task = await takeOverTaskSpace(mine.id);
console.log("took over task space", mine.id, "ownership:", task.ownership);
let page = task.page("p1");
const t0 = Date.now();

// ---------- 页面状态 ----------
async function readyState() {
  return page.evaluate(() => {
    const ed = [...document.querySelectorAll('div[contenteditable="true"]')]
      .some(e => e.offsetParent !== null);
    // 新前端回复容器;旧前端 model-response
    const v2 = document.querySelectorAll("div.response-container").length;
    const v1 = document.querySelectorAll("model-response").length;
    const stop = [...document.querySelectorAll("button,gem-icon-button")].some(b => {
      const l = (b.getAttribute("aria-label") || "") + " " + (b.textContent || "");
      return /stop|停止|中止/i.test(l) && b.offsetParent !== null;
    });
    let text = "";
    const conts = [...document.querySelectorAll("div.response-container")];
    if (conts.length) {
      const last = conts[conts.length - 1];
      const c = last.querySelector(".response-content");
      text = ((c ? c.innerText : last.innerText) || "").trim();
      text = text.replace(/^Gemini 说\s*/, "").trim();
    } else {
      const mrs = [...document.querySelectorAll("model-response")];
      if (mrs.length) {
        const last = mrs[mrs.length - 1];
        for (const s of [".markdown", "message-content", ".response-content"]) {
          const el = last.querySelector(s);
          if (el && (el.innerText || "").trim()) { text = el.innerText.trim(); break; }
        }
        if (!text) text = (last.innerText || "").trim();
      }
    }
    return { editor: ed, count: Math.max(v2, v1), busy: stop, text };
  });
}

// ---------- 1) 回到新对话(优先 p1;若 p1 被占用或重定向异常,自动改用新页面) ----------
async function editorReady() {
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(2000);
    if ((await readyState()).editor) return true;
  }
  return false;
}
let ok = false;
try { await page.goto("https://gemini.google.com/app"); }
catch { console.log("goto: navigation timeout, continue"); }
ok = await editorReady();
if (!ok || !/\/app/.test(await page.url())) {
  console.log("p1 unavailable (user busy or redirected), opening a new page");
  page = await task.newPage();
  try { await page.goto("https://gemini.google.com/app"); }
  catch { console.log("new page goto timeout, continue"); }
  ok = await editorReady();
}
if (!ok) {
  console.log("ERROR: editor not ready, url =", await page.url());
  process.exit(2);
}
// 若恢复了上次会话则清场:URL 必须是精确 /app(新对话)
for (let round = 0; round < 3; round++) {
  const u = await page.url();
  if (!/\/app\/[0-9a-f]{6,}/.test(u)) break;   // 已在 /app 精确路径
  console.log("still on a conversation, clicking new-chat, url =", u);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("a,button,gem-icon-button")].find(el => {
      const l = (el.getAttribute("aria-label") || "") + " " + (el.textContent || "");
      return /发起新对话|new chat/i.test(l);
    });
    if (b) b.click();
  });
  let moved = false;
  for (let i = 0; i < 15 && !moved; i++) {
    await page.waitForTimeout(2000);
    moved = !/\/app\/[0-9a-f]{6,}/.test(await page.url());
  }
  if (!moved && round === 2) {
    console.log("ERROR: cannot return to a new chat, url =", await page.url());
    process.exit(6);
  }
}
if ((await readyState()).count > 0) {   // /app 视图却带着旧回复:再点一次新对话
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("a,button,gem-icon-button")].find(el => {
      const l = (el.getAttribute("aria-label") || "") + " " + (el.textContent || "");
      return /发起新对话|new chat/i.test(l);
    });
    if (b) b.click();
  });
  await page.waitForTimeout(2500);
}
// 编辑器必须是空的(防上一次残留)
if (await editorHasText()) {
  console.log("WARN: editor has leftover text, clearing");
  await page.evaluate(() => {
    const eds = [...document.querySelectorAll('div[contenteditable="true"]')].filter(e => e.offsetParent !== null);
    const ed = eds[eds.length - 1];
    if (ed) { ed.focus(); document.execCommand("selectAll", false, null); document.execCommand("delete", false, null); }
  });
  await page.waitForTimeout(500);
}

// ---------- 2) 切换模型(尽力而为,失败仅警告) ----------
if (model) {
  const picker = await page.evaluate(() =>
    [...document.querySelectorAll("button,gem-icon-button")]
      .some(b => /模式选择器|model picker/i.test(b.getAttribute("aria-label") || "")));
  if (!picker) {
    console.log("WARN: model picker not present in this UI variant, keep default model");
  } else {
    await page.click('loc=role:button[name*="模式选择器"]', { label: "打开模型选择器" });
    await page.waitForTimeout(1200);
    const okm = await page.evaluate(m => {
      const items = [...document.querySelectorAll('[role="menuitem"], [role="menuitemradio"]')];
      const hit = items.find(e => (e.innerText || "").includes(m));
      if (hit) { hit.click(); return true; }
      return false;
    }, model);
    if (okm) { await page.waitForTimeout(800); console.log("model switched to:", model); }
    else { console.log("WARN: model menu item not found:", model); await page.keyboard.press("Escape"); }
  }
}

// ---------- 3) 填入并发送(Enter;发送按钮在新前端无效) ----------
async function editorHasText() {
  return page.evaluate(() => {
    const eds = [...document.querySelectorAll('div[contenteditable="true"]')]
      .filter(e => e.offsetParent !== null);
    if (eds.length) return (eds[eds.length - 1].innerText || "").trim().length > 0;
    const ta = document.querySelector("rich-textarea");
    return ta ? (ta.innerText || "").trim().length > 0 : true;
  });
}

await page.click('loc=role:textbox[name*="Gemini"]', { label: "点击输入框" });
await page.keyboard.insertText(prompt);
await page.waitForTimeout(700);
if (!(await editorHasText())) {
  // fallback:页面内 execCommand 逐段插入(Quill 只认受信/命令式输入)
  console.log("insertText empty, fallback to execCommand");
  const okc = await page.evaluate(t => {
    const eds = [...document.querySelectorAll('div[contenteditable="true"]')]
      .filter(e => e.offsetParent !== null);
    const ed = eds[eds.length - 1];
    if (!ed) return false;
    ed.focus();
    document.execCommand("selectAll", false, null);
    document.execCommand("delete", false, null);
    const paras = t.split(/\n{2,}/);
    paras.forEach((p, i) => {
      if (i) document.execCommand("insertLineBreak", false, null);
      p.split("\n").forEach((line, j) => {
        if (j) document.execCommand("insertLineBreak", false, null);
        if (line) document.execCommand("insertText", false, line);
      });
    });
    return (ed.innerText || "").trim().length > 0;
  }, prompt);
  if (!okc) {
    console.log("ERROR: both insertText and execCommand failed");
    process.exit(5);
  }
}
await page.waitForTimeout(500);
if (!(await editorHasText())) {
  console.log("ERROR: editor empty after fill");
  process.exit(5);
}
await page.press('loc=role:textbox[name*="Gemini"]', "Enter");

// 消息被接受的即时信号:输入框被清空;随后回复容器出现/URL 跳到 /app/<id>
let accepted = false;
for (let i = 0; i < 30 && !accepted; i++) {
  await page.waitForTimeout(2000);
  if (!(await editorHasText())) accepted = true;
  if (/\/app\/[0-9a-f]+/.test(await page.url())) accepted = true;
}
if (!accepted) {   // 重试一次 Enter
  console.log("retry send (Enter again)");
  await page.click('loc=role:textbox[name*="Gemini"]', { label: "重新聚焦输入框" }).catch(() => {});
  await page.press('loc=role:textbox[name*="Gemini"]', "Enter");
  for (let i = 0; i < 30 && !accepted; i++) {
    await page.waitForTimeout(2000);
    if (!(await editorHasText())) accepted = true;
    if (/\/app\/[0-9a-f]+/.test(await page.url())) accepted = true;
  }
}
if (!accepted) {
  console.log("ERROR: message not accepted, url =", await page.url());
  process.exit(4);
}
// 确认页面上的用户消息气泡确实是本次 prompt(防抓到旧对话回复)
const probe = prompt.replace(/\s+/g, " ").slice(0, 20);
let verified = false;
for (let i = 0; i < 20 && !verified; i++) {
  await page.waitForTimeout(2000);
  verified = await page.evaluate(q => {
    const qs = [...document.querySelectorAll("div.query-text, .user-query-bubble-with-background")];
    return qs.some(e => (e.innerText || "").replace(/\s+/g, " ").includes(q));
  }, probe);
}
if (!verified) {
  console.log("ERROR: sent message not found on page, url =", await page.url());
  process.exit(7);
}
console.log("message verified on page");

// ---------- 4) 等回复稳定(长文最长 ~9 分钟) ----------
let text = "", stable = 0, pageErr = "";
for (let i = 0; i < 360; i++) {
  await page.waitForTimeout(1500);
  const st = await readyState();
  if (st.text === text) stable++; else { stable = 0; text = st.text; }
  if (st.count > 0 && text && !st.busy && stable >= 2 && i >= 5) break;
  if (!text) {
    const body = await page.evaluate(() => document.body ? document.body.innerText : "");
    const m = body.match(/(出了点问题|发生错误|达到上限|limit reached|Something went wrong)[^\n]{0,80}/i);
    if (m && i > 12) { pageErr = m[0]; break; }
  }
}

await writeFile(`${BASE}/reply.txt`, text, "utf-8");
console.log(JSON.stringify({
  elapsed_s: Math.round((Date.now() - t0) / 1000),
  prompt_chars: prompt.length,
  reply_chars: text.length,
  page_error: pageErr || undefined,
}));
