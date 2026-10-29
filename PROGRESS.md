# 《兵王穿成娇软花魁》修订进度接续说明

> 本文件用于上下文压缩后恢复工作。读完本文件即可继续，无需回看历史对话。

## 一、项目与目标

- 项目：`e:/Development/Agents_Project/zcode_project/xiaoshuo`（FastAPI + SQLite + 原生 JS 单页的 AI 长篇创作工坊 CastNovel）
- 作品 id=18，《兵王穿成娇软花魁》，正文源稿在 `data/rewrite/chNN.md`，数据库表 `chapters`（project_id=18）
- **当前任务（2026-10-28 用户指令）：方案 A 对话驱动全流程——前两卷（第 1-24 章）全部逐一重新盲评并修改至盲审分 ≥85，可反复进行，必须每章达标**
  - **写/评分离铁律（用户指定，不可违反）：写 = Gemini 网页版（ego-browser 桥接，`tools/ego_gemini_bridge.mjs`）；评 = 本地盲评子 agent。同一个「人」不得既写又评。主 agent 只做编排（拼 prompt、落盘、跑门禁、入库），不自己写正文、不自己评分。**
- 番茄：第一卷 12 章、第二卷 12 章已上传；第三卷「一车咸鱼半车硝」分卷已建但尚未上传

## 二、关键决策（不可随意更改）

1. **代词标准**：主角（花魁柳如烟／兵王赵铁柱）第三人称一律用 **「她」**；男性角色（萧景琰/沈墨/周承业/周崇/刘全/全公公等）用「他」；其他女性角色一律用**姓名**，不用代词。
   - **陆铮是女性**（缇骑司指挥使），全书用「她」。第 3 章原文「她跨出门槛」为准。
   - 已全书统一 986+ 处（段落级 210 + 跨段 776 + 陆铮专项），脚本 `tools/fix_pronoun_all.py`、`tools/fix_pronoun2.py`
   - 数据库备份：`data/novelforge.backup-20261028-*.db`
2. **门禁阈值**（`tools/write_chapter.py`，不通过则 exit 1）：
   - 对白段占比 ≤45%、台词字数占比 ≤38%、最长连续对白段 ≤5
   - 短应答（≤8 字）比例 ≤35% **且** 数量 ≤15 **且** 对白段总数 ≥25（对白少的章节不启用）
3. **评分门槛以盲审分为准**（≥85），作者自评分只作参考并存档，不作为定稿依据。
4. **正文不得残留**：`# 标题`、`---` 分节线、`** 加粗`。源稿保留 `# 第N章 标题` 首行（工具会剥离），数据库正文不含。
5. AI 标注：番茄「是否使用AI」全部选**否**（用户指定）。
6. 分卷：卷一「胭脂河底没人管」/ 卷二「白绫系结不是风」/ 卷三「一车咸鱼半车硝」/ 卷四「花魁披甲守雁门」/ 卷五「紫宸殿上念血书」。

## 三、工具链

| 工具 | 用途 |
|---|---|
| `tools/write_chapter.py <N>` | 从源稿写入数据库 + 跑门禁（含对话密度 4 项） |
| `tools/finalize_chapter.py <N>` | 定稿（读 `data/rewrite/chNN.meta.json` 的审校记录） |
| `tools/check_text.py [lo] [hi]` | 校对扫描：相邻重复段、代词混用、年份倒退、称谓混用 |
| `tools/analyze_dialogue.py` | 全书对话密度统计 |
| `tools/list_short.py <N>` | 列出某章短应答 |
| `tools/inspect_she.py <N>` | 抽查某章「她」的用法 |
| `tools/clean_content.py` | 清除正文里的 `---` 与 `**` |
| `tools/fix_pronoun.py` / `fix_pronoun2.py` | 代词统一（段落级 / 跨段级），加 `--write` 才写入 |
| `tools/diff_pronoun.py <N>` | 与备份逐段对比代词改动 |
| `tools/apply_blind_review.py <N> [file]` | 盲审结果入库（并存入 `independent_history`） |
| `tools/save_blind.py` | ⚠️ CodeBuddy 专用（读 CodeBuddy history），本会话不用；盲评 JSON 由主 agent 直接写 `data/rewrite/chNN.blind.json` |
| `tools/ego_gemini_bridge.mjs` | **方案 A 核心**：ego-browser 驱动 Ego Lite 已登录 Gemini 网页版对话。读 `data/gemini_task/prompt.txt`（+可选 `model.txt`），写 `data/gemini_task/reply.txt`。运行：`ego-browser nodejs < tools/ego_gemini_bridge.mjs` |
| `tools/apply_revision.py <N>` | 把 Gemini 修订稿（reply.txt）清洗后覆盖 `data/rewrite/chNN.md`（补 `# 第N章` 标题行、字数校验），之后跑 `tools/write_chapter.py <N>` 过门禁 |
| `tools/export_chapters.py <lo> <hi>` | 导出纯文本到 `data/upload/` |
| `tools/cdp.py` | CDP 代理客户端（eval/click/clickAt/navigate/screenshot/targets/close） |
| `tools/upload_volume.py <listTab> <lo> <hi>` | 批量新建章节并上传番茄 |
| `tools/upload_chapter.py <target> <N> <chapterId>` | 覆盖番茄上已存在章节 |
| `tools/delete_chapter.py <listTab> <N...>` | 删除番茄章节 |

## 四、盲评流程（必须遵守）

1. 用 `task` 调 `code-explorer` 子 agent 盲评，**prompt 必须写明**：
   - 只读指定章的 `data/rewrite/chNN.md`，禁止读 meta.json / blind*.json / 数据库 / 其他章节
   - 逐章独立评分，不跨章比较
   - 评分纪律：90+ 意味着几乎无可挑剔；发现 3 个以上中等问题则总分 ≤85
   - 输出严格 JSON
2. 盲评结果存 `data/rewrite/chNN.blind.json`，用 `apply_blind_review.py` 入库
3. 按 issues 全量落实后重跑盲评，直到 ≥85
4. **批量盲评一次 5 章**（一个 agent 调用审 5 章），比逐章省 5 倍往返

## 五、盲评分数轨迹

盲评结果已按章存档为 `data/rewrite/chNN.blind.json`（`tools/save_blind.py` 从 task 输出自动解析）。

| 章 | 首轮分 | 已修 | 待修 |
|---|---|---|---|
| 27 | 76（6 轮后 74） | 高危清零，门禁 PASS | 第 7 轮待跑 |
| 1 | 84 | 代词、引号体例、结尾总结、勒痕与十七丈矛盾、重复 | 复评 |
| 2 | 78 | — | 对话密度、战情复述、能力曲线断裂、结尾总结句 |
| 3 | 75 | **高危已修**（一年一个 vs 三个月三个、陆铮人称、日期） | 复评 |
| 4 | 78 | **高危已修**（小剪位置断裂统一为银簪、过肩摔力學、门牙磕桌） | 复评 |
| 5 | 74 | **高危已修**（训练量：三十步→一百二十步、五圈→二十圈、一炷香→两炷香） | 对话密度（短应答 30 个）、张府宴补现场、青黛动机 |
| 6 | 78 | — | 第279行「他」改「她」、138行缺空行、花九娘8轮问答、顶格断句、结尾点题 |
| 7 | 74 | — | **高危：231-235行整句复写**（账本重新捆上写了两遍） |
| 8 | 74 | — | **高危：76-78行抽屉机关与271-277行新线索未查** |
| 9 | 76 | — | **高危：周承业三次被写死**（182行、135行、229-231行矛盾） |
| 10 | 71 | — | **高危：沈墨行为不一致**（28行/42行/178行/190行/241行） |

全书门禁扫描：35 章中约 28 章 FAIL（对话密度超标为主）。

## 六、待办（按顺序）

1. 第 27 章跑第 7 轮盲评，达 85 则样板成立
2. 修第 7、8、9、10 章的 4 个高危（整句复写／抽屉机关线未查／周承业三次被写死／沈墨行为不一致）；修第 2、5、6 章
3. 批量盲评 11-15 章 → 修订 → 复评；随后 16-20、21-25、26-30、31-35
4. 复评已修的 1、3、4 章
4. 35 章全部 ≥85 后：
   - `tools/export_chapters.py 1 24` 重新导出（正文已清 `---`）
   - 抓番茄 24 个章节 ID 映射，写「更新已发布章节」脚本（走编辑页 → 粘贴 → 下一步 → 提交 → 选否 → 确认提交）
   - 同步第一、二卷
   - 第三卷 25-36 章写完后用 `tools/upload_volume.py` 上传
5. 番茄上传注意事项：
   - 新建章节会**新开标签页**，在列表页点「新建章节」（span 的父级 button）
   - 发布设置里选「否」（**弹窗内 input[type=radio] value="2"**，value=1 是「是」；必须点 label，直接改 checked 无效）
   - 「内容检测方式」弹窗选「仅基础检测」；「内容风险检测」弹窗点「取消」
   - 列表字数**延迟更新**，提交后需刷新 5-10 秒

## 七、番茄站点经验（已写入 skill 笔记）

`C:\Users\silence\.codebuddy\skills\Web Access（浏览器自动化）\references\site-patterns\fanqienovel.com.md`
含：编辑器只认「全选 → 等 1 秒 → 派发 paste → 补一次 insertText/delete」才保存；只改标题会假成功；分卷切换要点**整行**（`.editor-volume-list-item`）变橙。

## 八、代码库注意事项

- MCP server(`mcp_server.py`)v0.3 共 **22 个工具**(新增 `generate_draft_best` 候选拉样重排、`deai_rewrite_chapter` 两步去AI味、`search_impact` 影响反查、`list/upsert/delete_lore_entry` Lorebook);CodeBuddy 侧需**重启 castnovel server** 才能看到新工具
- 测试务必用 `CASTNOVEL_FORCE_MOCK=1 python main.py <port>` 启动(强制离线,库里配了真实 Key 也不会烧额度);本机 `data/novelforge.db` 的 strong/fast 档案是「discovery」真实模型
- `app/stages.py` 有私有函数 `_ctx_parts(pid, idx, settings)` 返回第 4 项即 context(已含 Lorebook 注入与可选向量检索)
- `db.update_chapter` 的 allowed 字段:idx/title/beats/summary/content/status/review/gate
- 项目侧技能包在 `skills/castnovel/`,改完跑 `python tools/install_skill.py` 重新安装到 ~/.workbuddy/skills 等

## 九、方案 A：ego-browser + Gemini 网页版桥接（2026-10-28 实测可用）

**分工铁律**：写 = Gemini（ego 桥接）；评 = 本地盲评子 agent（无历史、无锚定）；主 agent 只编排。

**桥接技术要点**（`tools/ego_gemini_bridge.mjs`，任务空间名 `castnovel-gemini`，页面 p1）：
- Gemini 前端 2026-10 已出现新组件体系：`chat-app`/`input-area-v2`/`rich-textarea`；回复在 `div.response-container > .response-content`，正文前有屏幕阅读器文本「Gemini 说」需剥离；旧 `model-response` 结构仍随机出现，脚本双兼容
- 新前端**点「发送」按钮无效**，必须 `press Enter`；消息被接受的标志是 URL 跳到 `/app/<id>` 或回复容器出现
- Quill 编辑器只认受信输入：`click` 输入框 → `keyboard.insertText(prompt)`（长文可用）
- 部分页面变体（A/B）无「模式选择器」按钮（出现「升级到 Google AI Pro」按钮时），切模型尽力而为、失败仅警告；默认模型 Flash-Lite
- SPA 首屏慢：`goto` 15s 超时不算失败（导航已提交），耐心轮询 editor ready（最长 ~80s）
- 每次请求开新对话（`goto /app` 即新对话；若有旧恢复则点「发起新对话」），上下文全靠 prompt 带入
- `data/gemini_task/`：`prompt.txt`（输入）/`model.txt`（可选模型，如「3.1 Pro」）/`reply.txt`（输出）

**逐章循环 SOP**（每章重复直到盲审 ≥85）：
1. **盲评**：子 agent 只读 `data/rewrite/chNN.md`（严禁读 meta/blind/其他章/数据库），输出严格 JSON → 主 agent 拆开存 `data/rewrite/chNN.blind.json` → `python tools/apply_blind_review.py <N>` 入库。批量一次 5 章；复评可以单章一批
2. **≥85**：达标，记入下表；**<85**：进入修订
3. **修订**（写 = Gemini）：
   a. 备份：`cp data/rewrite/chNN.md data/rewrite/backup/chNN.r<K>.md`
   b. 构造修订 prompt 写入 `data/gemini_task/prompt.txt`（删除 `model.txt` 用默认模型）。模板：角色（资深网文作者）+ 代词标准（主角用「她」；男性角色用「他」；其他女性角色一律姓名；陆铮是女性用「她」）+ 硬性要求（最小改动、字数 2400-4000、禁 #/---/**、对白段占比 ≤45%、台词字数 ≤38%、连续对白 ≤5 段、短应答合并、结尾不总结不点题、只输出正文全文不要任何解释）+ 盲评 issues（含门禁问题）+ 原文全文
   c. `ego-browser nodejs < tools/ego_gemini_bridge.mjs`
   d. `python tools/apply_revision.py <N>` 清洗落盘 → `python tools/write_chapter.py <N>` 写库+门禁（FAIL 则把门禁输出并入 issues 回到 b）
4. **复评**：新一轮盲评 agent（全新、无历史）→ 回到 1
5. 每批完成更新下表与「五、盲评分数轨迹」

**进度表（前两卷 1-24 章）**：评 = 最新一轮盲审分。达标线 ≥85。

| 章 | 首评 | 复评 | 状态 |
|---|---|---|---|
| 1 | 84 | 87 | ✅ 达标 |
| 2 | 82 | 83 | 按复评意见再修(椅子距离矛盾/称谓体例/楼规来源) |
| 3 | 85 | 84 | 按复评意见修(陆铮人称/体能跳变/宴期口径) |
| 4 | 79 | 82 | 按复评意见再修(铜牌来历/宴期时间线) |
| 5 | 84 | 83 | 按复评意见再修(青黛视角滑移/三更衔接) |
| 6 | 86 | - | ✅ 达标 |
| 7 | 85 | - | ✅ 达标 |
| 8 | 83 | 82 | 按复评意见再修(翻译腔对白/女性人称) |
| 9 | 78 | 83 | 按复评意见再修(500 两账目/人称/结尾复述) |
| 10 | 79 | 84 | 按复评意见再修(归类句主宾颠倒/落帕动机) |
| 11 | 76 | 79 | **高危:与第 8 章死亡时间冲突**;の字/素心台词重复 |
| 12 | 77 | 84 | 按复评意见再修(薄瓷警报矛盾/皂靴脚印表述) |
| 13 | 82 | - | 已修(4 轮),门禁 PASS,待复评 |
| 14 | 84 | - | 修订 prompt 已生成,等浏览器 |
| 15 | 83 | - | 待修,等浏览器 |
| 16 | 79 | - | 待修,等浏览器 |
| 17 | 84 | - | 待修,等浏览器 |
| 18 | 83 | - | 待修,等浏览器 |
| 19 | 84 | - | 待修,等浏览器 |
| 20 | 78 | - | 待修,等浏览器 |
| 21 | 84 | - | 待修,等浏览器 |
| 22 | 82 | - | 待修,等浏览器 |
| 23 | 79 | - | 待修,等浏览器 |
| 24 | 78 | - | 待修,等浏览器 |

**当前阻塞(2026-10-28):用户正在 Ego Lite 浏览器上使用 Gemini(goto /app 被重定向到营销页,三个 tab 均被占用),桥接礼让暂停。用户空下来后对每章执行:python tools/make_revision_prompt.py <N> && ego-browser nodejs < tools/ego_gemini_bridge.mjs && python tools/apply_revision.py <N> && python tools/write_chapter.py <N>;门禁 FAIL 时提取门禁细节并入 chNN.blind.json 重修;修完发新一轮盲评 agent(5 章一批)复评,循环至 ≥85。**

桥接工程备忘:Gemini 会把 ASCII 引号写成中文弯引号(apply_revision 已归一化 U+201C/201D→");拒答(「爱莫能助」)在 prompt 模板加【背景说明】虚构声明后解决;用户占用浏览器时 goto /app 被重定向到营销页(检测到即礼让);短应答门禁顽固章用「叙述引导+原话保留」形态改造有效;11 章修订曾引入与 8 章的跨章死亡时间矛盾(批量盲评 8-12 时发现)——跨章一致性依赖批量盲评暴露。
