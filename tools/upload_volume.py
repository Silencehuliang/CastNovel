"""批量把章节作为「新章节」上传到番茄当前分卷。

用法：python tools/upload_volume.py <列表页target> <起始章> <结束章>

前提：列表页已切到目标分卷（脚本每章都会校验）。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from app import db

PROXY = "http://localhost:3456"


def _req(path, body=None):
    url = PROXY + path
    data = body.encode("utf-8") if body is not None else None
    method = "POST" if data is not None else "GET"
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8", "replace")


def ev(target, js):
    raw = _req("/eval?target=" + target, js)
    try:
        return json.loads(raw).get("value")
    except Exception:
        return raw


def targets():
    return json.loads(_req("/targets"))


CUR_VOL = """(() => {
  const el = document.querySelector('.serial-select.flat-serial-select');
  return el ? (el.innerText || '').trim() : 'NONE';
})()"""

PICK_VOL = """(() => {
  const sel = document.querySelector('.serial-select.flat-serial-select');
  if (!sel) return 'NO_SEL';
  sel.click();
  const opt = [...document.querySelectorAll('.byte-select-option')].find(e => (e.innerText || '').includes('第二卷'));
  if (!opt) return 'NO_OPT';
  opt.click();
  return 'PICKED';
})()"""

NEW_CHAPTER = """(() => {
  const s = [...document.querySelectorAll('span')].find(e => (e.innerText || '').trim() === '新建章节');
  if (!s) return 'NO_SPAN';
  (s.closest('button') || s.parentElement).click();
  return 'NEW_OK';
})()"""

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
  return 'TITLE_OK';
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
  document.execCommand('insertText', false, '。');
  document.execCommand('delete', false, null);
  return 'NUDGE_OK';
})()"""

NEXT = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '下一步');
  if (!b) return 'NO_NEXT';
  b.click();
  return 'NEXT_OK';
})()"""

SUBMIT_TYPO = """(() => {
  const bs = [...document.querySelectorAll('button')].filter(x => (x.innerText||'').trim() === '提交');
  if (!bs.length) return 'NO_TYPO';
  bs[bs.length-1].click();
  return 'TYPO_OK';
})()"""

BASIC_CHECK = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '仅基础检测');
  if (!b) return 'NO_BASIC';
  b.click();
  return 'BASIC_OK';
})()"""

CANCEL_RISK = """(() => {
  const m = [...document.querySelectorAll('[class*="modal-wrapper"]')].find(x => (x.innerText||'').includes('内容风险检测'));
  if (!m) return 'NO_RISK';
  const b = [...m.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '取消');
  if (!b) return 'NO_CANCEL';
  b.click();
  return 'RISK_OK';
})()"""

CHOOSE_NO = """(() => {
  const modal = [...document.querySelectorAll('[class*="modal-wrapper"]')].find(m => (m.innerText||'').includes('是否使用AI'));
  if (!modal) return 'NO_AI_MODAL';
  const radios = [...modal.querySelectorAll('input[type="radio"]')];
  const no = radios.find(r => r.value === '2');
  if (!no) return 'NO_RADIO';
  (no.closest('label') || no.parentElement).click();
  return 'NO_CHECKED';
})()"""

CONFIRM = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '确认提交');
  if (!b) return 'NO_CONFIRM';
  b.click();
  return 'CONFIRM_OK';
})()"""


def ensure_volume(list_target):
    cur = ev(list_target, CUR_VOL)
    if "第二卷" in (cur or ""):
        return True
    ev(list_target, PICK_VOL)
    time.sleep(1.5)
    cur = ev(list_target, CUR_VOL)
    return "第二卷" in (cur or "")


def upload_one(list_target, idx):
    db.init()
    ch = [c for c in db.list_chapters(18) if c["idx"] == idx][0]
    title = ch["title"]
    body = (BASE / "data" / "upload" / f"{idx:02d}.txt").read_text(encoding="utf-8")

    ensure_volume(list_target)

    before = {t["targetId"] for t in targets()}
    ev(list_target, NEW_CHAPTER)
    time.sleep(4)

    new_tabs = [t for t in targets()
                if t["targetId"] not in before and "publish" in (t.get("url") or "")]
    if not new_tabs:
        print(f"  !! ch{idx} 未找到新建标签页")
        return False
    tab = new_tabs[0]["targetId"]

    print(f"  tab={tab[:8]} ", ev(tab, SERIAL.replace("__SERIAL__", str(idx))), end=" ")
    print(ev(tab, TITLE.replace("__TITLE__", json.dumps(title, ensure_ascii=False))), end=" ")
    print(ev(tab, PASTE.replace("__TEXT__", json.dumps(body, ensure_ascii=False))), end=" ")
    print(ev(tab, NUDGE))
    time.sleep(12)
    print("  ", ev(tab, NEXT), end=" ")
    time.sleep(3)
    print(ev(tab, SUBMIT_TYPO), end=" ")
    time.sleep(2.5)
    print(ev(tab, BASIC_CHECK), end=" ")
    time.sleep(2.5)
    print(ev(tab, CANCEL_RISK), end=" ")
    time.sleep(2)
    print(ev(tab, CHOOSE_NO), end=" ")
    time.sleep(1)
    print(ev(tab, CONFIRM))
    time.sleep(4)
    try:
        _req("/close?target=" + tab)
    except Exception:
        pass
    return True


def main():
    list_target = sys.argv[1]
    start, end = int(sys.argv[2]), int(sys.argv[3])
    for idx in range(start, end + 1):
        print(f"[ch{idx}]")
        ok = upload_one(list_target, idx)
        print(f"  -> {ok}")
        time.sleep(1.5)


if __name__ == "__main__":
    main()
