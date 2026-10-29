"""CDP Proxy 简易客户端（规避 shell 转义与 GBK 控制台编码问题）。

结果统一写入 tools/last_result.txt（UTF-8），终端只打印摘要。

用法：
  python tools/cdp.py targets
  python tools/cdp.py new <url_file>
  python tools/cdp.py navigate <target> <url_file>
  python tools/cdp.py eval <target> <js_file>
  python tools/cdp.py click <target> <selector_file>
  python tools/cdp.py info <target>
  python tools/cdp.py shot <target> <png>
  python tools/cdp.py scroll <target> [y]
  python tools/cdp.py close <target>
"""
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://localhost:3456"
OUT = Path(__file__).resolve().parent / "last_result.txt"


def post(path, body):
    req = urllib.request.Request(BASE + path, data=body.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8", "replace")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=300) as r:
        return r.read().decode("utf-8", "replace")


def save(text):
    OUT.write_text(text, encoding="utf-8")
    print(f"[ok] {len(text)} chars -> {OUT.name}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "targets":
        save(get("/targets"))
    elif cmd == "new":
        url = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
        save(post("/new", url))
    elif cmd == "navigate":
        url = Path(sys.argv[3]).read_text(encoding="utf-8").strip()
        save(post("/navigate?target=" + sys.argv[2], url))
    elif cmd == "eval":
        js = Path(sys.argv[3]).read_text(encoding="utf-8")
        save(post("/eval?target=" + sys.argv[2], js))
    elif cmd == "click":
        sel = Path(sys.argv[3]).read_text(encoding="utf-8").strip()
        save(post("/click?target=" + sys.argv[2], sel))
    elif cmd == "clickAt":
        sel = Path(sys.argv[3]).read_text(encoding="utf-8").strip()
        save(post("/clickAt?target=" + sys.argv[2], sel))
    elif cmd == "info":
        save(get("/info?target=" + sys.argv[2]))
    elif cmd == "shot":
        save(get("/screenshot?target=" + sys.argv[2] + "&file=" + urllib.parse.quote(sys.argv[3])))
    elif cmd == "scroll":
        save(get("/scroll?target=" + sys.argv[2] + "&y=" + (sys.argv[3] if len(sys.argv) > 3 else "3000")))
    elif cmd == "close":
        save(get("/close?target=" + sys.argv[2]))
    else:
        print("unknown command")
