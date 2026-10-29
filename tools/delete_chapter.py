"""删除番茄上指定章节号（按标题序号匹配行）。用法：python tools/delete_chapter.py <target> <章号> [章号...]"""
import json
import sys
import time
import urllib.request
from pathlib import Path

PROXY = "http://localhost:3456"


def ev(target, js):
    req = urllib.request.Request(PROXY + "/eval?target=" + target, data=js.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw).get("value")
    except Exception:
        return raw


CLICK_DEL = """(() => {
  const n = '__N__';
  const rows = [...document.querySelectorAll('tr')];
  const r = rows.find(x => (x.innerText || '').trim().startsWith('第' + n + '章'));
  if (!r) return 'NO_ROW';
  const d = r.querySelector('.icon-delete');
  if (!d) return 'NO_DEL';
  d.click();
  return 'DEL_CLICKED';
})()"""

CONFIRM = """(() => {
  const m = [...document.querySelectorAll('[class*="modal-wrapper"]')].find(x => (x.innerText||'').includes('确定删除'));
  if (!m) return 'NO_MODAL';
  const b = [...m.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '删除');
  if (!b) return 'NO_BTN';
  b.click();
  return 'DELETED';
})()"""

LIST = """(() => JSON.stringify([...document.querySelectorAll('tr')].map(e => (e.innerText||'').replace(/\\s+/g,' ').trim()).filter(t => /^第\\d+章/.test(t))))()"""


def main():
    target = sys.argv[1]
    nums = sys.argv[2:]
    for n in nums:
        print(f"[del ch{n}]", ev(target, CLICK_DEL.replace("'__N__'", "'" + n + "'")))
        time.sleep(1.8)
        print("   ", ev(target, CONFIRM))
        time.sleep(2.5)
    time.sleep(1.5)
    print(ev(target, LIST))


if __name__ == "__main__":
    main()
