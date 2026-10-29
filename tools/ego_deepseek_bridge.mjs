// DeepSeek 网页版桥接(方案 A:ego-browser 驱动 Ego Lite 里的已登录会话)
//
// I/O 约定与 Gemini 桥接保持一致,便于复用 make_revision_prompt/apply_revision:
//   读  .../data/gemini_task/prompt.txt  提示词全文(UTF-8)
//   写  .../data/gemini_task/reply.txt   模型回复全文(UTF-8)
// 运行:ego-browser nodejs < tools/ego_deepseek_bridge.mjs
//
// 2026-10-29 实测(chat.deepseek.com):
//   - 登录会话在 Ego 的 castnovel-deepseek 任务空间;首页 / 即新对话,textarea[name="search"] 为输入框;
//   - Enter 发送,成功标志是 URL 跳到 /a/chat/s/<id>;
//   - 最终答案在 .ds-assistant-message-main-content;DeepThink 的思考过程在另一个 .ds-markdown 块,需排除;
//   - 回复完成后助手消息下方出现 "Read aloud" 操作条;超长文以文本稳定 3 次作为兜底完成信号。

import { readFile, writeFile } from "node:fs/promises";

const BASE = "E:/Development/Agents_Project/zcode_project/xiaoshuo/data/gemini_task";
const prompt = (await readFile(`${BASE}/prompt.txt`, "utf-8")).trim();
if (!prompt) { console.log("EMPTY_PROMPT"); process.exit(1); }

const task = await taskSpace("castnovel-deepseek");
const page = task.page("p1");
const t0 = Date.now();
const TA = 'css:textarea[name="search"]';

async function state() {
  return page.evaluate(() => {
    const ta = document.querySelector('textarea[name="search"]');
    const mains = [...document.querySelectorAll(".ds-assistant-message-main-content")]
      .filter(e => e.offsetParent !== null);
    const last = mains[mains.length - 1];
    const stop = [...document.querySelectorAll("button")].some(b => {
      const s = (b.getAttribute("aria-label") || "") + " " +
                (b.getAttribute("title") || "") + " " + (b.innerText || "");
      return /stop|停止|中止/i.test(s) && b.offsetParent !== null;
    });
    const done = [...document.querySelectorAll("button")]
      .some(b => b.offsetParent !== null &&
        /read aloud|朗读/i.test((b.getAttribute("aria-label") || "") + " " + (b.innerText || "")));
    return {
      url: location.href,
      editor: !!ta && ta.offsetParent !== null,
      editorText: ta ? (ta.value || "") : "",
      text: last ? (last.innerText || "").trim() : "",
      count: mains.length,
      stop, done,
      body: (document.body.innerText || "").slice(-1500),
    };
  });
}

async function waitEditor(limit = 20) {
  for (let i = 0; i < limit; i++) {
    await page.waitForTimeout(1500);
    if ((await state()).editor) return true;
  }
  return false;
}

// ---------- 1) 新对话 ----------
try { await page.goto("https://chat.deepseek.com/"); }
catch { console.log("goto: navigation timeout, continue"); }
if (!(await waitEditor())) {
  console.log("ERROR: editor not ready, url =", await page.url());
  process.exit(2);
}
for (let round = 0; round < 3; round++) {
  const u = await page.url();
  if (!/\/a\/chat\/s\//.test(u)) break;
  console.log("restored a conversation, starting new chat, url =", u);
  const clicked = await page.evaluate(() => {
    const els = [...document.querySelectorAll("div,button,a")]
      .filter(e => e.offsetParent !== null && (e.innerText || "").trim() === "New chat");
    const el = els[els.length - 1];
    if (!el) return false;
    (el.closest("button,a") || el).click();
    return true;
  });
  if (!clicked) await page.keyboard.press("Control+j");
  await page.waitForTimeout(2500);
}
if (/\/a\/chat\/s\//.test(await page.url())) {
  console.log("ERROR: cannot start a new chat, url =", await page.url());
  process.exit(6);
}

// 编辑器必须为空(防残留)
const st0 = await state();
if (st0.editorText.trim()) {
  console.log("WARN: editor has leftover text, clearing");
  await page.evaluate(() => {
    const ta = document.querySelector('textarea[name="search"]');
    if (!ta) return;
    ta.focus();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    setter.call(ta, "");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForTimeout(400);
}

// ---------- 2) 填入并发送 ----------
await page.click(TA, { label: "点击 DeepSeek 输入框" });
await page.fill(TA, prompt);
await page.waitForTimeout(600);
let val = await page.evaluate(() => (document.querySelector('textarea[name="search"]') || {}).value || "");
if (val.trim().length < Math.min(50, prompt.length)) {
  console.log("fill insufficient, fallback to keyboard.insertText");
  await page.evaluate(() => {
    const ta = document.querySelector('textarea[name="search"]');
    if (ta) { ta.focus(); ta.value = ""; }
  });
  await page.keyboard.insertText(prompt);
  await page.waitForTimeout(500);
  val = await page.evaluate(() => (document.querySelector('textarea[name="search"]') || {}).value || "");
}
if (!val.trim()) {
  console.log("ERROR: editor empty after fill");
  process.exit(5);
}
await page.press(TA, "Enter");

let accepted = false;
for (let i = 0; i < 30 && !accepted; i++) {
  await page.waitForTimeout(1000);
  const st = await state();
  if (/\/a\/chat\/s\//.test(st.url) || !st.editorText.trim()) accepted = true;
}
if (!accepted) {
  console.log("retry send: click send button");
  const clicked = await page.evaluate(() => {
    const ta = document.querySelector('textarea[name="search"]');
    if (!ta) return false;
    let p = ta;
    for (let i = 0; i < 6 && p.parentElement; i++) p = p.parentElement;
    const btns = [...p.querySelectorAll("button")].filter(b => b.offsetParent !== null);
    const b = btns[btns.length - 1];
    if (b) { b.click(); return true; }
    return false;
  });
  if (!clicked) await page.keyboard.press("Enter");
  for (let i = 0; i < 30 && !accepted; i++) {
    await page.waitForTimeout(1000);
    const st = await state();
    if (/\/a\/chat\/s\//.test(st.url) || !st.editorText.trim()) accepted = true;
  }
}
if (!accepted) {
  console.log("ERROR: message not accepted, url =", await page.url());
  process.exit(4);
}

// 确认页面上确实是本次 prompt(防旧对话串台)
const probe = prompt.replace(/\s+/g, " ").slice(0, 24);
let verified = false;
for (let i = 0; i < 20 && !verified; i++) {
  await page.waitForTimeout(1000);
  verified = await page.evaluate(q => {
    const msgs = [...document.querySelectorAll(".ds-message")];
    return msgs.some(e => (e.innerText || "").replace(/\s+/g, " ").includes(q));
  }, probe);
}
if (!verified) {
  console.log("ERROR: sent message not found on page, url =", await page.url());
  process.exit(7);
}
console.log("message verified on page");

// ---------- 3) 等回复稳定(长文最长 ~10 分钟) ----------
let text = "", stable = 0, pageErr = "";
for (let i = 0; i < 400; i++) {
  await page.waitForTimeout(1500);
  const st = await state();
  if (st.text === text) stable++; else { stable = 0; text = st.text; }
  const complete = st.count > 0 && text && !st.stop && (st.done || stable >= 3);
  if (complete && i >= 4) break;
  if (!text && i > 12) {
    const m = st.body.match(/(出了点问题|服务器繁忙|请求失败|达到上限|limit reached|Something went wrong)[^\n]{0,80}/i);
    if (m) { pageErr = m[0]; break; }
  }
}

await writeFile(`${BASE}/reply.txt`, text, "utf-8");
console.log(JSON.stringify({
  elapsed_s: Math.round((Date.now() - t0) / 1000),
  prompt_chars: prompt.length,
  reply_chars: text.length,
  page_error: pageErr || undefined,
}));
