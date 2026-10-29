"""Gemini 网页版桥接器 —— 把 gemini.google.com 的对话框变成 OpenAI 兼容 API。

背景
----
本项目的 `app/llm.py` 只认 OpenAI 协议（`/v1/chat/completions`，SSE 流式）。
本脚本在本地起一个小服务，用 Playwright 驱动一个**已登录 Google 账号的真实浏览器**，
把你的 prompt 打进去、把 Gemini 的回复原文抓回来，再按 OpenAI 协议吐出去。

因此 CastNovel 侧**零改动**：设置页新建一个档案，
Base URL 填 `http://127.0.0.1:8765/v1`、API Key 随便填（如 `web`）、模型名随便填（如 `gemini-web`）即可。

两种浏览器模式
--------------
- `attach`（推荐）：连你**自己启动**的 Chrome，无自动化痕迹、登录态是你日常的。
    chrome.exe --remote-debugging-port=9222 --user-data-dir="D:/chrome-cdp"
- `launch`：由本脚本启动并维护一个持久化 profile（`data/gemini-profile/`），
    首次运行 `login` 子命令手动登录一次，之后长期复用 Cookie。

用法
----
    python tools/gemini_bridge.py login            # 首次登录（launch 模式）
    python tools/gemini_bridge.py probe            # 体检：打印页面结构与选择器命中情况
    python tools/gemini_bridge.py ask "写一句话"    # 单次试跑，不启服务
    python tools/gemini_bridge.py serve            # 起服务，默认 127.0.0.1:8765

注意
----
- 网页版是**串行**的：所有请求排队执行（一把 asyncio 锁），并发请求会等待而不是报错。
- 每个请求都开**新对话**，上下文完全由本项目的提示词带入，避免跨章污染。
- 免费额度有人为限制（每小时/每天的对话数、图片数），批量连写 30 章会被限流，
  表现为回复变短或出现「你已达到上限」；脚本会把这段文字原样返回，注意甄别。
- Gemini 前端类名会变。`probe` 子命令列出了所有命中情况，改版后先跑它。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# FastAPI 在模块级导入：`from __future__ import annotations` 会让注解变成字符串，
# 局部导入的 Request 无法被 FastAPI 解析，会被误当成查询参数。
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_PROFILE_DIR = BASE_DIR / "data" / "gemini-profile"
DEFAULT_COOKIE_FILE = BASE_DIR / "data" / "gemini_cookies.json"
DEFAULT_CDP = "http://127.0.0.1:9222"
DEFAULT_MODEL = "gemini-web"
# gemini-webapi 要求 Python>=3.11，本项目主环境是 3.10，故用独立 venv 跑桥接
VENV_PY = BASE_DIR / "data" / "gemini-venv" / "Scripts" / "python.exe"

# ---------------- 页面脚本（改版后只需改这里） ----------------

# 输入框就绪判定：可见的 contenteditable / textarea
JS_READY = """() => {
  const eds = [...document.querySelectorAll('div[contenteditable="true"], textarea')];
  return eds.some(e => e.offsetParent !== null);
}"""

# 填入文本（保留段落）。Quill 只认 execCommand，直接改 textContent 会让发送按钮保持禁用。
JS_FILL = """(text) => {
  const eds = [...document.querySelectorAll('div[contenteditable="true"], textarea')]
              .filter(e => e.offsetParent !== null);
  const ed = eds[eds.length - 1];
  if (!ed) return {ok: false, err: 'input-not-found'};
  ed.focus();
  if (ed.tagName === 'TEXTAREA') {
    ed.value = text;
    ed.dispatchEvent(new Event('input', {bubbles: true}));
    return {ok: true, kind: 'textarea'};
  }
  document.execCommand('selectAll', false, null);
  document.execCommand('delete', false, null);
  const paras = text.split(/\\n{2,}/);
  paras.forEach((p, i) => {
    if (i) document.execCommand('insertLineBreak', false, null);
    p.split('\\n').forEach((line, j) => {
      if (j) document.execCommand('insertLineBreak', false, null);
      if (line) document.execCommand('insertText', false, line);
    });
  });
  return {ok: true, kind: 'contenteditable', len: (ed.innerText || '').length};
}"""

# 点发送按钮；找不到就交给键盘 Enter
JS_SEND = """() => {
  const cands = [...document.querySelectorAll('button')].filter(b => {
    const label = (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '');
    return /send|发送|提交/i.test(label) && !b.disabled && b.offsetParent !== null;
  });
  if (cands.length) { cands[cands.length - 1].click(); return {ok: true, how: 'button'}; }
  return {ok: false};
}"""

# 抓取最后一条回复 + 是否仍在生成 + 错误横幅
JS_EXTRACT = """() => {
  const nodes = [...document.querySelectorAll('model-response')];
  const stop = [...document.querySelectorAll('button')].some(b => {
    const label = (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '');
    return /stop|停止|中止/i.test(label) && b.offsetParent !== null;
  });
  let text = '', raw = '';
  if (nodes.length) {
    const last = nodes[nodes.length - 1];
    raw = (last.innerText || '').trim();
    const sels = ['.markdown', 'message-content', '.response-content', '.model-response-text'];
    for (const s of sels) {
      const el = last.querySelector(s);
      const t = el ? (el.innerText || '').trim() : '';
      if (t) { text = t; break; }
    }
    if (!text) text = raw;
    // 思考过程面板混进来时切掉
    const m = text.match(/^(显示思路|Show thinking|思考过程)[\\s\\S]{0,4000}?(?=\\n\\n)/);
    if (m && text.length > m[0].length + 40) text = text.slice(m[0].length).trim();
  }
  const body = document.body ? document.body.innerText : '';
  const err = body.match(/(Something went wrong|An error occurred|出了点问题|发生错误|达到上限|limit reached)[^\\n]{0,100}/i);
  return {count: nodes.length, text: text, raw_len: raw.length, busy: stop, error: err ? err[0] : ''};
}"""


class GeminiWebError(RuntimeError):
    pass


class GeminiWeb:
    """一个浏览器上下文 = 一个 Gemini 会话通道。所有调用串行。"""

    backend = "dom"

    def __init__(self, mode: str = "launch", cdp_url: str = DEFAULT_CDP,
                 profile_dir: Path | None = None, headless: bool = False,
                 channel: str = "chrome", reply_timeout: int = 600,
                 keep_chat: bool = False):
        self.mode = mode
        self.cdp_url = cdp_url
        self.profile_dir = Path(profile_dir or DEFAULT_PROFILE_DIR)
        self.headless = headless
        self.channel = channel
        self.reply_timeout = reply_timeout
        self.keep_chat = keep_chat
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._own_browser = False
        self.lock = asyncio.Lock()

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        if self.mode == "attach":
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
            except Exception as e:
                raise GeminiWebError(
                    f"连接 {self.cdp_url} 失败（{e}）。请先启动带调试端口的 Chrome：\n"
                    f'  chrome.exe --remote-debugging-port=9222 --user-data-dir="D:/chrome-cdp"\n'
                    f"或在没有 Chrome 的情况下改用 launch 模式。" ) from e
            self._ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            self._own_browser = False
        else:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            kwargs = dict(
                headless=self.headless,
                viewport={"width": 1440, "height": 960},
                locale="zh-CN",
                args=["--disable-blink-features=AutomationControlled"],
            )
            if self.channel:
                kwargs["channel"] = self.channel
            try:
                self._ctx = await self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), **kwargs)
            except Exception as e:
                raise GeminiWebError(
                    f"启动浏览器失败（{e}）。若未安装 Chrome，去掉 --channel 让 Playwright "
                    f"使用自带 Chromium（需先 playwright install chromium）。") from e
            self._own_browser = True
        self._page = await self._ensure_page()

    async def _ensure_page(self):
        for p in self._ctx.pages:
            if "gemini.google.com" in (p.url or ""):
                return p
        try:
            return await self._ctx.new_page()
        except Exception:
            if self._ctx.pages:
                return self._ctx.pages[0]
            raise GeminiWebError("浏览器上下文里拿不到任何标签页")

    async def close(self) -> None:
        # attach 模式：playwright.stop() 会连带关掉用户自己的 Chrome，这里什么都不做。
        # 驱动子进程随本进程退出被回收，CDP 连接自然断开，浏览器活得很好。
        if self.mode == "attach":
            return
        try:
            if self._own_browser and self._ctx:
                await self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    # ---------- 会话 ----------

    async def _open_new_chat(self) -> None:
        page = self._page
        await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=90_000)
        if "accounts.google.com" in (page.url or ""):
            raise GeminiWebError(
                "浏览器未登录 Google 账号。请先运行：python tools/gemini_bridge.py login")
        try:
            await page.wait_for_function(JS_READY, timeout=90_000)
        except Exception as e:
            raise GeminiWebError(
                f"等不到 Gemini 输入框（当前 URL：{page.url}）。可能是未登录、页面改版或被限流。"
                f"先跑 python tools/gemini_bridge.py probe 看看页面结构。") from e
        # /app 偶尔会恢复上次会话，若有历史回复则点「新对话」清场
        if not self.keep_chat:
            has_old = await page.evaluate("() => document.querySelectorAll('model-response').length")
            if has_old:
                clicked = await page.evaluate("""() => {
                  const b = [...document.querySelectorAll('button, a')].find(el => {
                    const l = (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '');
                    return /new chat|新对话|新聊天|发起新对话/i.test(l) && el.offsetParent !== null;
                  });
                  if (b) { b.click(); return true; } return false;
                }""")
                if clicked:
                    await page.wait_for_timeout(1200)
                    await page.wait_for_function(JS_READY, timeout=30_000)

    async def _submit(self, prompt: str) -> None:
        page = self._page
        res = await page.evaluate(JS_FILL, prompt)
        if not isinstance(res, dict) or not res.get("ok"):
            raise GeminiWebError(f"填入输入框失败：{res}")
        await page.wait_for_timeout(350)
        sent = await page.evaluate(JS_SEND)
        if not (isinstance(sent, dict) and sent.get("ok")):
            await page.keyboard.press("Enter")

    async def _wait_reply(self, n0: int, on_delta=None, timeout: int | None = None) -> str:
        page = self._page
        timeout = timeout or self.reply_timeout
        deadline = time.time() + timeout
        started = time.time()
        stable = 0
        last = ""
        emitted = ""
        last_err = ""
        while time.time() < deadline:
            try:
                st = await page.evaluate(JS_EXTRACT)
            except Exception:
                await asyncio.sleep(0.6)
                continue
            text = (st.get("text") or "").strip()
            busy = bool(st.get("busy"))
            if st.get("error"):
                last_err = st["error"]
            if text != last:
                last, stable = text, 0
            else:
                stable += 1
            if on_delta and len(text) > len(emitted):
                try:
                    on_delta(text[len(emitted):])
                except Exception:
                    pass
                emitted = text
            elif len(text) < len(emitted):
                emitted = text
            if (st.get("count", 0) > n0 and text and not busy
                    and stable >= 2 and time.time() - started > 2.0):
                return text
            if last_err and not text and time.time() - started > 15:
                raise GeminiWebError(f"Gemini 页面报错：{last_err}")
            await asyncio.sleep(0.45)
        if last:
            return last  # 超时但已有内容，尽量交回去，由上层判断字数
        raise GeminiWebError(
            f"等待回复超时（{timeout}s）。可能被限流、被要求人机验证，或页面改版。"
            + (f" 页面提示：{last_err}" if last_err else ""))

    async def ask(self, prompt: str, on_delta=None, timeout: int | None = None) -> str:
        """串行地向 Gemini 提一个问题，返回纯文本回复。"""
        if not prompt or not prompt.strip():
            raise GeminiWebError("prompt 为空")
        async with self.lock:
            if self._page is None or self._page.is_closed():
                self._page = await self._ensure_page()
            await self._open_new_chat()
            n0 = await self._page.evaluate(
                "() => document.querySelectorAll('model-response').length")
            await self._submit(prompt)
            return await self._wait_reply(n0, on_delta, timeout)


# ---------------- 后端二：cookie 逆向（gemini-webapi，推荐） ----------------

class GeminiCookieClient:
    """不用浏览器常驻：拿一次 cookie，直接调 Gemini 网页版内部 RPC。

    这比 DOM 抓取快一个数量级，也不受前端改版影响（没有选择器可坏）。
    依赖 gemini_webapi（AGPL-3.0，要求 Python>=3.11），故桥接跑在独立 venv 里。
    """

    backend = "cookie"

    def __init__(self, cookie_file=DEFAULT_COOKIE_FILE, model=None, proxy=None,
                 timeout=600, verbose=False):
        self.cookie_file = Path(cookie_file)
        self.model = model or None
        self.proxy = proxy
        self.timeout = timeout
        self.verbose = verbose
        self.lock = asyncio.Lock()
        self._client = None
        self.models = []
        self._page = None  # 仅为 /health 输出兼容

    async def start(self) -> None:
        try:
            from gemini_webapi import GeminiClient, set_log_level
        except ImportError as e:
            raise GeminiWebError(
                "未安装 gemini_webapi（需 Python>=3.11）。执行：\n"
                "  uv venv data/gemini-venv --python 3.13\n"
                '  uv pip install --python "data/gemini-venv/Scripts/python.exe" '
                'gemini_webapi fastapi "uvicorn[standard]" websockets') from e
        if not self.cookie_file.exists():
            raise GeminiWebError(
                f"找不到 {self.cookie_file}。先运行：python tools/gemini_bridge.py login")
        data = json.loads(self.cookie_file.read_text(encoding="utf-8"))
        psid = (data.get("__Secure-1PSID") or "").strip()
        psidts = (data.get("__Secure-1PSIDTS") or "").strip()
        if not psid:
            raise GeminiWebError("cookie 文件缺少 __Secure-1PSID，请重新运行 login")
        set_log_level("DEBUG" if self.verbose else "ERROR")
        self._client = GeminiClient(psid, psidts, proxy=self.proxy)
        await self._client.init(timeout=60, auto_refresh=True, verbose=self.verbose)
        try:
            self.models = list(await self._client.list_models())
        except Exception:
            self.models = []

    async def ask(self, prompt: str, on_delta=None, timeout: int | None = None) -> str:
        if not prompt or not prompt.strip():
            raise GeminiWebError("prompt 为空")
        if self._client is None:
            raise GeminiWebError("客户端未初始化")
        limit = timeout or self.timeout
        async with self.lock:
            out: list[str] = []

            async def _run():
                async for chunk in self._client.generate_content_stream(prompt, model=self.model):
                    t = getattr(chunk, "text", "") or ""
                    if not t:
                        continue
                    out.append(t)
                    if on_delta:
                        try:
                            on_delta(t)
                        except Exception:
                            pass

            last_err = None
            for attempt in range(3):
                out.clear()
                try:
                    await asyncio.wait_for(_run(), timeout=limit)
                    break
                except asyncio.TimeoutError as e:
                    raise GeminiWebError(f"等待回复超时（{limit}s）") from e
                except Exception as e:  # 限流/网络抖动重试，鉴权与模型错误直接抛出
                    last_err = e
                    name = type(e).__name__
                    if "Auth" in name or "ModelInvalid" in name:
                        raise GeminiWebError(f"{name}: {e}") from e
                    if attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
            else:
                raise GeminiWebError(f"Gemini 调用失败：{last_err}")
            text = "".join(out).strip()
            if not text:
                raise GeminiWebError("Gemini 返回了空内容（可能被限流，或该模型当前账号不可用）")
            return text

    async def close(self) -> None:
        try:
            if self._client:
                await self._client.close()
        except Exception:
            pass


def find_chrome() -> str:
    cands = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for p in cands:
        try:
            if p.exists():
                return str(p)
        except Exception:
            continue
    return "chrome.exe"


def cdp_alive(cdp_url: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=2) as r:
            r.read()
        return True
    except Exception:
        return False


async def capture_cookies(cdp_url: str = DEFAULT_CDP) -> dict:
    """通过 CDP 从已登录的 Chrome 读取 google 域 cookie（只需一次）。"""
    import urllib.request
    import websockets

    if not cdp_alive(cdp_url):
        raise GeminiWebError(
            f"连不上 {cdp_url}。先运行：python tools/gemini_bridge.py chrome")
    with urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=5) as r:
        info = json.loads(r.read().decode("utf-8"))
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    raise GeminiWebError(f"读取 cookie 失败：{msg['error']}")
                items = msg.get("result", {}).get("cookies", [])
                break
    return {c["name"]: c["value"] for c in items if "google" in (c.get("domain") or "")}


def _ensure_runtime(backend: str) -> None:
    """cookie 后端需要 Python>=3.11 + gemini_webapi；用主环境（3.10）启动时自动切到 venv。"""
    if backend != "cookie":
        return
    try:
        import gemini_webapi  # noqa: F401
        return
    except ImportError:
        pass
    if os.getenv("GEMINI_BRIDGE_REEXEC") or not VENV_PY.exists():
        raise SystemExit(
            "cookie 后端需要 gemini_webapi（Python>=3.11）。先准备运行时：\n"
            "  uv venv data/gemini-venv --python 3.13\n"
            '  uv pip install --python "data/gemini-venv/Scripts/python.exe" '
            'gemini_webapi fastapi "uvicorn[standard]" websockets')
    os.environ["GEMINI_BRIDGE_REEXEC"] = "1"
    os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])


# ---------------- 提示词拼接（无 system role，塞进首段） ----------------

def messages_to_prompt(messages: list[dict], json_mode: bool = False) -> str:
    system = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") != "system"]
    parts = []
    if system:
        parts.append("【系统指令｜必须严格遵守】\n" + system.strip())
    if len(turns) <= 1:
        for m in turns:
            parts.append((m.get("content") or "").strip())
    else:
        for m in turns:
            tag = "用户" if m.get("role") == "user" else "助手"
            parts.append(f"【{tag}】\n" + (m.get("content") or "").strip())
        parts.append("【用户】\n请针对最后一次用户输入作答。")
    body = "\n\n".join(p for p in parts if p)
    if json_mode:
        body += ("\n\n【输出格式｜硬性要求】只输出一个合法 JSON 对象，不要任何解释、"
                 "不要 Markdown 代码围栏之外的文字。")
    return body


# ---------------- OpenAI 兼容 HTTP 层 ----------------

def _chunk(cid: str, model: str, created: int, delta: dict, finish=None) -> str:
    payload = {
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def build_app(driver_provider):
    """driver_provider(): 返回已 start 的 GeminiWeb 实例（延迟获取）。"""
    app = FastAPI(title="gemini-web-bridge", version="0.1")

    @app.get("/health")
    async def health():
        try:
            d = await driver_provider()
            page = getattr(d, "_page", None)
            return {"ok": True, "backend": getattr(d, "backend", "?"),
                    "page": page.url if page else None, "lock": d.lock.locked()}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [
            {"id": DEFAULT_MODEL, "object": "model", "owned_by": "google-web"},
        ]}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "body 不是合法 JSON"}}, status_code=400)
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return JSONResponse({"error": {"message": "messages 不能为空"}}, status_code=400)
        json_mode = bool(body.get("response_format"))
        model = body.get("model") or DEFAULT_MODEL
        stream = bool(body.get("stream"))
        prompt = messages_to_prompt(messages, json_mode)

        try:
            driver = await driver_provider()
        except Exception as e:
            return JSONResponse({"error": {"message": str(e)}}, status_code=503)

        cid = "chatcmpl-" + uuid.uuid4().hex[:16]
        created = int(time.time())

        if not stream:
            try:
                text = await driver.ask(prompt)
            except Exception as e:
                return JSONResponse({"error": {"message": str(e)}}, status_code=502)
            return {
                "id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        async def gen():
            queue: asyncio.Queue = asyncio.Queue()
            holder: dict = {}

            async def worker():
                try:
                    holder["text"] = await driver.ask(
                        prompt, on_delta=lambda d: queue.put_nowait(("delta", d)))
                except Exception as e:  # noqa: BLE001
                    queue.put_nowait(("error", str(e)))
                finally:
                    queue.put_nowait(("done", None))

            task = asyncio.create_task(worker())
            yield _chunk(cid, model, created, {"role": "assistant", "content": ""})
            try:
                while True:
                    kind, val = await queue.get()
                    if kind == "delta":
                        yield _chunk(cid, model, created, {"content": val})
                    elif kind == "error":
                        yield _chunk(cid, model, created,
                                     {"content": f"\n[gemini-bridge 错误] {val}"}, "stop")
                        break
                    else:
                        yield _chunk(cid, model, created, {}, "stop")
                        break
            finally:
                if not task.done():
                    try:
                        await asyncio.wait_for(task, timeout=5)
                    except Exception:
                        task.cancel()
                yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


# ---------------- CLI ----------------

def _make_driver(args):
    if args.backend == "cookie":
        return GeminiCookieClient(
            cookie_file=Path(args.cookie_file) if args.cookie_file else DEFAULT_COOKIE_FILE,
            model=args.model,
            proxy=args.proxy,
            timeout=args.timeout,
            verbose=args.verbose,
        )
    return GeminiWeb(
        mode=args.mode,
        cdp_url=args.cdp,
        profile_dir=Path(args.profile_dir) if args.profile_dir else DEFAULT_PROFILE_DIR,
        headless=args.headless,
        channel=args.channel,
        reply_timeout=args.timeout,
        keep_chat=args.keep_chat,
    )


def cmd_chrome(args) -> int:
    """确保有一个带调试端口、可登录 Google 的 Chrome（cookie 后端的取 cookie 用）。"""
    if cdp_alive(args.cdp):
        print(f"Chrome 调试端口已就绪：{args.cdp}")
        return 0
    exe = find_chrome()
    if not Path(exe).exists():
        print("找不到 Chrome，请手动安装后重试，或用 --cdp 指向已有调试端口")
        return 1
    port = args.cdp.rstrip("/").rsplit(":", 1)[-1]
    import subprocess

    subprocess.Popen([exe, f"--remote-debugging-port={port}",
                      f"--user-data-dir={args.chrome_profile}"])
    for _ in range(30):
        time.sleep(0.5)
        if cdp_alive(args.cdp):
            print(f"已启动 Chrome（{exe}），监听 {args.cdp}")
            print("请在该窗口登录 Google 并打开 https://gemini.google.com/app，然后运行 login")
            return 0
    print("Chrome 已启动但调试端口未就绪（可能该 profile 已有进程在跑，需先全部关闭）")
    return 1


def cmd_profile(args) -> int:
    """把「Gemini 网页版」档案写进 CastNovel 的设置（幂等，不改动其它档案）。"""
    from app import db as appdb
    from app.config import load_settings, save_settings

    appdb.init()
    s = load_settings(appdb)
    name = "Gemini 网页版"
    prof = {
        "name": name,
        "base_url": f"http://127.0.0.1:{args.port}/v1",
        "api_key": "web",
        "model": DEFAULT_MODEL,
        "temperature": 0.85,
        "max_tokens": 8192,
    }
    profiles = [p for p in s.get("profiles", []) if p.get("name") != name] + [prof]
    save_settings(appdb, {
        "profiles": profiles,
        "strong_profile": s.get("strong_profile"),
        "fast_profile": s.get("fast_profile"),
        "gen": s.get("gen"),
    })
    print(f"已写入档案「{name}」：{prof['base_url']} / key=web / model={DEFAULT_MODEL}")
    print("去侧栏「⚙ 设置」点「测试」；通过后把强模型切到它即可。")
    print("注意：强模型切过去后，快模型（摘要/状态抽取）建议保留原 API 模型，网页版排队很慢。")
    return 0


def cmd_login(args) -> int:
    if args.backend == "cookie":
        async def go():
            cookies = await capture_cookies(args.cdp)
            if not cookies.get("__Secure-1PSID"):
                raise SystemExit(
                    "浏览器里没找到 __Secure-1PSID：请先在调试端口的 Chrome 里登录 "
                    "https://gemini.google.com/app ，再重跑 login")
            keep = ("__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC",
                    "SID", "HSID", "SSID", "APISID", "SAPISID")
            out = {k: v for k, v in cookies.items() if k in keep}
            path = Path(args.cookie_file) if args.cookie_file else DEFAULT_COOKIE_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            # 立刻验证 cookie 是否真能用
            d = GeminiCookieClient(cookie_file=path, model=args.model,
                                   proxy=args.proxy, verbose=args.verbose)
            try:
                await d.start()
                names = [getattr(m, "model_name", None) or str(m) for m in d.models][:10]
                print(f"[login] cookie 已保存到 {path}")
                print(f"[login] 验证通过，可用模型：{names or '（未取到列表，默认模型可用）'}")
            finally:
                await d.close()

        asyncio.run(go())
        return 0

    async def go():
        d = _make_driver(args)
        await d.start()
        page = d._page
        await page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
        print("[login] 浏览器已打开，请在窗口里完成 Google 登录。")
        print("[login] 登录完成后回到这里按回车……")
        await asyncio.get_running_loop().run_in_executor(None, input)
        await page.goto(GEMINI_URL, wait_until="domcontentloaded")
        await page.wait_for_function(JS_READY, timeout=120_000)
        print("[login] 已进入 Gemini，登录态已保存在", d.profile_dir)
        if args.mode == "attach":
            print("[login] attach 模式下登录态保存在你自己的 Chrome 里，无需额外操作。")
        await d.close()

    asyncio.run(go())
    return 0


def cmd_probe(args) -> int:
    if args.backend == "cookie":
        async def go():
            d = _make_driver(args)
            await d.start()
            try:
                info = {
                    "backend": "cookie",
                    "cookie_file": str(d.cookie_file),
                    "models": [getattr(m, "model_name", None) or str(m) for m in d.models][:20],
                }
                try:
                    info["account"] = str(await d._client.account_status())
                except Exception as e:
                    info["account"] = f"（取不到：{e}）"
                print(json.dumps(info, ensure_ascii=False, indent=2))
            finally:
                await d.close()

        asyncio.run(go())
        return 0

    async def go():
        d = _make_driver(args)
        await d.start()
        page = d._page
        if not (page.url or "").startswith("https://gemini.google.com"):
            await page.goto(GEMINI_URL, wait_until="domcontentloaded")
        # SPA 首屏慢，最多等 20s 让输入框出现；等不到也照样打印现场
        ready = True
        try:
            await page.wait_for_function(JS_READY, timeout=20_000)
        except Exception:
            ready = False
        await page.wait_for_timeout(1200)
        info = await page.evaluate("""() => ({
          url: location.href,
          title: document.title,
          editors: [...document.querySelectorAll('div[contenteditable="true"], textarea')]
                     .map(e => e.tagName + ':' + (e.className || '')),
          responses: document.querySelectorAll('model-response').length,
          buttons: [...document.querySelectorAll('button')]
                     .map(b => (b.getAttribute('aria-label') || b.textContent || '').trim())
                     .filter(Boolean).slice(0, 30),
          body: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 400),
        })""")
        info["input_ready"] = ready
        info["extract"] = await page.evaluate(JS_EXTRACT)
        info["extract"]["text"] = (info["extract"].get("text") or "")[:300]
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if not ready:
            print("\n[提示] 输入框没出现：多半是没登录 Google，或停在人机验证。"
                  "在这个 Chrome 窗口里登录并打开 gemini.google.com，然后重跑 probe。")
        await d.close()

    asyncio.run(go())
    return 0


def cmd_ask(args) -> int:
    async def go():
        d = _make_driver(args)
        await d.start()
        t0 = time.time()
        try:
            text = await d.ask(args.prompt)
        finally:
            await d.close()
        print(f"--- 回复（{len(text)} 字，{time.time() - t0:.1f}s）---")
        print(text)

    asyncio.run(go())
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    state: dict = {}

    async def provider() -> GeminiWeb:
        d = state.get("driver")
        if d is None:
            d = _make_driver(args)
            await d.start()
            state["driver"] = d
        return d

    app = build_app(provider)

    @app.on_event("shutdown")
    async def _shutdown():
        d = state.get("driver")
        if d:
            await d.close()

    print(f"[bridge] 服务地址 http://{args.host}:{args.port}/v1  "
          f"（后端 {args.backend}）")
    print(f"[bridge] CastNovel 设置页：Base URL=http://127.0.0.1:{args.port}/v1  "
          f"API Key=web  模型={DEFAULT_MODEL}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini 网页版 → OpenAI 兼容桥接器")
    ap.add_argument("cmd", nargs="?", default="serve",
                    choices=["serve", "login", "probe", "ask", "profile", "chrome"])
    ap.add_argument("prompt", nargs="*", help="ask 子命令的问题")
    ap.add_argument("--backend", default="cookie", choices=["cookie", "dom"],
                    help="cookie=逆向内部 RPC（推荐，无需浏览器）；dom=Playwright 抓页面（兜底）")
    ap.add_argument("--cookie-file", default=None, help="cookie 文件路径")
    ap.add_argument("--model", default=None,
                    help="cookie 后端指定模型名，如 gemini-3-pro；留空用账号默认")
    ap.add_argument("--proxy", default=None, help="cookie 后端的 HTTP 代理")
    ap.add_argument("--mode", default="launch", choices=["launch", "attach"])
    ap.add_argument("--cdp", default=DEFAULT_CDP, help="取 cookie / attach 用的 CDP 地址")
    ap.add_argument("--chrome-profile", default="D:/chrome-cdp",
                    help="chrome 子命令启动时使用的独立 profile 目录")
    ap.add_argument("--profile-dir", default=None, help="dom 后端的浏览器 profile 目录")
    ap.add_argument("--channel", default="chrome",
                    help="浏览器通道：chrome / msedge / 留空使用 Playwright 自带 Chromium")
    ap.add_argument("--headless", action="store_true", help="无头模式（易被风控，不建议）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=int, default=600, help="单次等待回复的秒数")
    ap.add_argument("--keep-chat", action="store_true", help="复用同一对话（默认每次新对话）")
    ap.add_argument("--verbose", action="store_true", help="打印底层库日志")
    args = ap.parse_args()
    if args.channel in ("", "none", "chromium"):
        args.channel = None
    if args.cmd == "ask":
        args.prompt = " ".join(args.prompt) if args.prompt else ""
        if not args.prompt:
            ap.error("ask 需要一个问题文本")
    if args.cmd in ("serve", "login", "probe", "ask"):
        _ensure_runtime(args.backend)
    return {"login": cmd_login, "probe": cmd_probe, "ask": cmd_ask, "serve": cmd_serve,
            "profile": cmd_profile, "chrome": cmd_chrome}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
