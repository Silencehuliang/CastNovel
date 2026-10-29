"""在番茄新建章节（空编辑页）并提交。用法：python tools/upload_new_chapter.py <target> <章号> <章节号>"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from app import db

PROXY = "http://localhost:3456"


def ev(target, js):
    req = urllib.request.Request(PROXY + "/eval?target=" + target, data=js.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw).get("value")
    except Exception:
        return raw


SERIAL = """(() => {
  const si = document.querySelectorAll('input.serial-input')[0];
  if (!si) return 'NO_SERIAL';
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(si, String(__SERIAL__));
  si.dispatchEvent(new Event('input', {bubbles:true}));
  si.dispatchEvent(new Event('change', {bubbles:true}));
  si.blur();
  return 'SERIAL:' + si.value;
})()"""

TITLE = """(() => {
  const ti = document.querySelector('input.serial-editor-input-hint-area');
  if (!ti) return 'NO_TITLE';
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(ti, __TITLE__);
  ti.dispatchEvent(new Event('input', {bubbles:true}));
  ti.dispatchEvent(new Event('change', {bubbles:true}));
  ti.blur();
  return 'TITLE:' + ti.value;
})()"""

PASTE = """(() => {
  const TEXT = __TEXT__;
  const pm = document.querySelector('.ProseMirror');
  if (!pm) return 'NO_PM';
  pm.focus();
  const dt = new DataTransfer();
  dt.setData('text/plain', TEXT);
  pm.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  return 'PASTE:' + pm.innerText.length + '/' + TEXT.length;
})()"""

NUDGE = """(() => {
  const pm = document.querySelector('.ProseMirror');
  if (!pm) return 'NO_PM';
  pm.focus();
  const a = pm.innerText.length;
  document.execCommand('insertText', false, '。');
  document.execCommand('delete', false, null);
  return 'NUDGE:' + a + '->' + pm.innerText.length;
})()"""

NEXT = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '下一步');
  if (!b) return 'NO_NEXT';
  b.click();
  return 'NEXT_OK';
})()"""

SUBMIT_TYPO = """(() => {
  const bs = [...document.querySelectorAll('button')].filter(x => (x.innerText||'').trim() === '提交');
  if (!bs.length) return 'NO_TYPO_DIALOG';
  bs[bs.length-1].click();
  return 'TYPO_SUBMITTED';
})()"""

CHOOSE_NO = """(() => {
  const modal = [...document.querySelectorAll('[class*="modal-wrapper"]')].find(m => (m.innerText||'').includes('是否使用AI'));
  if (!modal) return 'NO_MODAL';
  const radios = [...modal.querySelectorAll('input[type="radio"]')];
  const no = radios.find(r => r.value === '2');
  if (!no) return 'NO_RADIO';
  (no.closest('label') || no.parentElement).click();
  return 'NO_CHECKED:' + radios.map(r => r.value + '=' + r.checked).join(',');
})()"""

CONFIRM = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '确认提交');
  if (!b) return 'NO_CONFIRM';
  b.click();
  return 'CONFIRM_OK';
})()"""

CHECK = """(() => {
  const pm = document.querySelector('.ProseMirror');
  const modal = [...document.querySelectorAll('[class*="modal-wrapper"]')].map(m => (m.innerText||'').replace(/\\s+/g,' ').slice(0,90)).join(' | ');
  return JSON.stringify({ url: location.href.slice(-45), len: pm ? pm.innerText.length : -1, modal });
})()"""


def main():
    target, idx, serial = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    db.init()
    ch = [c for c in db.list_chapters(18) if c["idx"] == idx][0]
    title = ch["title"]
    body = (BASE / "data" / "upload" / f"{idx:02d}.txt").read_text(encoding="utf-8")

    print(f"[new ch{idx}] {title} serial={serial} body={len(body)}")
    print("  ", ev(target, SERIAL.replace("__SERIAL__", serial)))
    print("  ", ev(target, TITLE.replace("__TITLE__", json.dumps(title, ensure_ascii=False))))
    print("  ", ev(target, PASTE.replace("__TEXT__", json.dumps(body, ensure_ascii=False))))
    time.sleep(2.5)
    print("  ", ev(target, NUDGE))
    time.sleep(13)
    print("  ", ev(target, NEXT))
    time.sleep(3)
    print("  ", ev(target, SUBMIT_TYPO))
    time.sleep(2.5)
    if len(sys.argv) > 4 and sys.argv[4] == "dry":
        print("  [DRY]", ev(target, CHECK))
        return
    print("  ", ev(target, CHOOSE_NO))
    time.sleep(0.8)
    print("  ", ev(target, CONFIRM))
    time.sleep(5)
    print("  ", ev(target, CHECK))


if __name__ == "__main__":
    main()
