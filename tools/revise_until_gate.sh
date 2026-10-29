#!/bin/bash
# 一章的完整修订循环:make → bridge → apply → write;门禁 FAIL 自动提取细节重试。
# 用法: bash tools/revise_until_gate.sh <N> [max_rounds=4]
set -u
N=$1
MAX=${2:-4}
PY="E:/Development/Env/Python3.10.11/python.exe"
cd "$(dirname "$0")/.."

for r in $(seq 1 "$MAX"); do
  echo "=== 第${N}章 修订轮 $r ==="
  PYTHONIOENCODING=utf-8 "$PY" tools/make_revision_prompt.py "$N" || exit 1
  ego-browser nodejs < tools/ego_gemini_bridge.mjs || { echo "[bridge failed]"; exit 1; }
  PYTHONIOENCODING=utf-8 "$PY" tools/apply_revision.py "$N" || { echo "[apply rejected]"; exit 1; }
  if PYTHONIOENCODING=utf-8 "$PY" tools/write_chapter.py "$N"; then
    echo "=== 第${N}章 gate PASS (round $r) ==="
    exit 0
  fi
  # 门禁 FAIL:提取连对白/短应答清单并入 blind.json
  PYTHONIOENCODING=utf-8 "$PY" - "$N" <<'PYEOF'
import json, re, sys
idx = int(sys.argv[1])
t = open(f'data/rewrite/ch{idx:02d}.md', encoding='utf-8').read()
paras = [p.strip() for p in t.split('\n') if p.strip()]
IS = re.compile(r'^\s*["「]')
dial = [bool(IS.match(p)) for p in paras]
best = cur = 0; start = bs = 0
for i, x in enumerate(dial):
    if x:
        if cur == 0: start = i
        cur += 1
        if cur > best: best, bs = cur, start
    else: cur = 0
run_text = paras[bs:bs+best] if best else []
shorts = []
ratio_lines = 0
for p in paras:
    if not IS.match(p): continue
    ratio_lines += 1
    content = ''.join(re.findall(r'["「](.*?)["」]', p, re.S))
    if len(content.strip()) <= 8: shorts.append(p[:44])
p2 = f'data/rewrite/ch{idx:02d}.blind.json'
dd = json.loads(open(p2, encoding='utf-8').read())
dd['issues'] = [i for i in dd['issues'] if '程序门禁' not in i.get('desc','')]
if best > 5:
    dd['issues'].append({'type': '对白', 'severity': '高', 'desc': '程序门禁A:最长连续对白 ' + str(best) + ' 段(上限 5):' + json.dumps(run_text, ensure_ascii=False) + ' 。在连续对白之间插入动作/神态/环境叙述打断,连续 ≤5 段。'})
if len(shorts) > 15:
    dd['issues'].append({'type': '对白', 'severity': '高', 'desc': '程序门禁B:' + str(len(shorts)) + ' 个乒乓短应答段:' + json.dumps(shorts, ensure_ascii=False) + ' 。改法:(a)叙述引导——整段改为动作/神态开头,台词保留引号放句中,如「"没有。"」→「杏儿摇头,声音发紧:"没有。"」;(b)并入相邻完整句;(c)纯应答改成动作叙述。改完后「引号开头且内容 ≤8 字」的段落 ≤10 个。台词本身必须始终带英文双引号。'})
# 台词字数占比超标:给出最长的对白段,要求转写成叙述
import re as _re
spoken = sum(len(m) for p in paras for m in _re.findall(r'["「](.*?)["」]', p, re.S))
chars = len(_re.sub(r'\s', '', t))
if chars and spoken / chars > 0.38:
    longs = sorted(((p, len(''.join(_re.findall(r'["「](.*?)["」]', p, re.S)))) for p in paras if IS.match(p)), key=lambda x: -x[1])[:4]
    dd['issues'].append({'type': '对白', 'severity': '高', 'desc': '程序门禁C:台词字数占全文 ' + str(round(spoken / chars * 100, 1)) + '%(上限 38%)。把下面几段台词最长的对白改成「叙述转述」(不保留引号台词,改写成叙述描写对话内容与双方神态):' + json.dumps([p[:40] for p, _ in longs], ensure_ascii=False) + ' 。至少转写其中 3 段,其余内容不动。'})
open(p2, 'w', encoding='utf-8').write(json.dumps(dd, ensure_ascii=False, indent=2))
print(f'[gate-fail] run={best} shorts={len(shorts)} issues updated')
PYEOF
done
echo "=== 第${N}章 ${MAX} 轮后仍未过门禁 ==="
exit 1
