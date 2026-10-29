"""把一章正文与标题写入番茄编辑页并提交（AI 标注选“否”）。

用法：python tools/upload_chapter.py <target_id> <章号> <章节ID>
"""
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
        obj = json.loads(raw)
        return obj.get("value")
    except Exception:
        return raw


def nav(target, url):
    req = urllib.request.Request(PROXY + "/navigate?target=" + target, data=url.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8", "replace")


SEL = """(() => {
  const pm = document.querySelector('.ProseMirror');
  if (!pm) return 'NO_PM';
  pm.scrollIntoView({block:'center'});
  pm.focus();
  document.execCommand('selectAll', false, null);
  const s = window.getSelection();
  return 'SEL:' + ((s.toString()||'').length);
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

TITLE = """(() => {
  const ti = document.querySelector('input.serial-editor-input-hint-area');
  if (!ti) return 'NO_TITLE_INPUT';
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(ti, __TITLE__);
  ti.dispatchEvent(new Event('input', {bubbles:true}));
  ti.dispatchEvent(new Event('change', {bubbles:true}));
  ti.blur();
  return 'TITLE:' + ti.value;
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
  if (!modal) return 'NO_PUBLISH_MODAL';
  const radios = [...modal.querySelectorAll('input[type="radio"]')];
  const no = radios.find(r => r.value === '2');
  if (!no) return 'NO_RADIO';
  const wrap = no.closest('label') || no.parentElement;
  wrap.click();
  return 'NO_CHECKED:' + radios.map(r => r.value + '=' + r.checked).join(',');
})()"""

CONFIRM = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '确认提交');
  if (!b) return 'NO_CONFIRM';
  b.click();
  return 'CONFIRM_OK';
})()"""

SAVE_STATUS = """(() => {
  const t = document.body.innerText;
  const pm = document.querySelector('.ProseMirror');
  const st = /保存中|正在保存/.test(t) ? 'SAVING' : (/已保存/.test(t) ? 'SAVED' : 'UNKNOWN');
  return st + ':' + (pm ? pm.innerText.length : -1);
})()"""

CHECK_LEN = """(() => {
  const pm = document.querySelector('.ProseMirror');
  return 'LEN:' + (pm ? pm.innerText.length : -1) + '/' + (__TARGET__);
})()"""

CHECK = """(() => {
  const pm = document.querySelector('.ProseMirror');
  const modal = [...document.querySelectorAll('[class*="modal-wrapper"]')].map(m => (m.innerText||'').replace(/\\s+/g,' ').slice(0,80)).join(' | ');
  return JSON.stringify({ url: location.href.slice(-40), len: pm ? pm.innerText.length : -1, modal });
})()"""


def main():
    target = sys.argv[1]
    idx = int(sys.argv[2])
    cid = sys.argv[3]

    db.init()
    ch = [c for c in db.list_chapters(18) if c["idx"] == idx][0]
    title = ch["title"]
    body = (BASE / "data" / "upload" / f"{idx:02d}.txt").read_text(encoding="utf-8")

    print(f"[ch{idx}] {title} body={len(body)}")
    nav(target, f"https://fanqienovel.com/main/writer/7701211068684569625/publish/{cid}/")
    time.sleep(3.5)

    print("  ", ev(target, SEL))
    time.sleep(0.8)
    print("  ", ev(target, PASTE.replace("__TEXT__", json.dumps(body, ensure_ascii=False))))
    time.sleep(2.5)
    print("  ", ev(target, TITLE.replace("__TITLE__", json.dumps(title, ensure_ascii=False))))
    time.sleep(1.5)
    print("  ", ev(target, NUDGE))
    print("  ", ev(target, SAVE_STATUS))
    time.sleep(13)
    print("  ", ev(target, SAVE_STATUS))
    print("  ", ev(target, CHECK_LEN.replace("__TARGET__", str(len(body)))))

    print("  ", ev(target, NEXT))
    time.sleep(2.5)
    print("  ", ev(target, SUBMIT_TYPO))
    time.sleep(2.5)
    print("  ", ev(target, CHOOSE_NO))
    time.sleep(0.8)
    print("  ", ev(target, CONFIRM))
    time.sleep(5)
    print("  ", ev(target, CHECK))


if __name__ == "__main__":
    main()
