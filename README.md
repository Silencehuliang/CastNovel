# CastNovel · 铸书 — AI 长篇小说创作工坊

**设定 → 大纲 → 章纲 → 正文 → 审校 → 定稿**,分层生成、全程可编辑、带一致性门禁的本地 Web 工作台。
基于对 GitHub 上 **40+ 个** AI 写小说开源项目与 Re3/DOC/WriteHERE/LongWriter 等学术系统的系统调研([调研报告](docs/research.md) · [总体设计](docs/design.md)),取各家之长、避各家之坑。

```bash
pip install -r requirements.txt
python main.py          # 浏览器打开 http://127.0.0.1:8000
```

> 无 API Key 也能跑:内置「离线演示」mock 模型,开箱即可体验全流程;配置真实 Key 后一键切换。

## 为什么是「更好的」

调研发现 2026 年头部项目的共识与痛点,逐条落实在本项目中:

| 调研发现 | CastNovel 的做法 |
|---|---|
| 纯向量检索/纯模型记忆都撑不住长篇一致性 | **三层记忆**:世界观+角色档案 → 滚动摘要+上一章结尾 → BM25 检索相关旧情节,外加**伏笔清单**,按预算组装每次生成的上下文 |
| 「结构化状态回写」正在取代向量派(天命/wfcz10086 的教训) | 章节**定稿时**用快模型抽取人物状态变化、伏笔埋设/回收、**情节线推进**,回写故事圣经,下一章自动携带最新状态 |
| 大纲粒度不足是质量下限(DOC 的核心结论) | 两级细化:逐章「目标-阻碍-转折+钩子」大纲 → 场景级章纲(节拍)→ 正文严格对拍 |
| 人工监督是刚需(书童模式最受欢迎) | 所有产物可编辑;润色/修订**先流式预览、确认后才采用**,原稿不被覆盖 |
| 一致性要靠确定性校验,不能只靠模型自觉(inkflow/oh-story/moshu 共识) | **程序化门禁**(零 token):字数达标率、角色覆盖检查、与前三章的 8-gram 重复率、**13 条去 AI 味 lint 规则**(套话/模板动作/论述腔/破折号配额),徽章化展示 |
| 长篇创作必然反复,改稿要能后悔(goink/书童) | **章节版本历史**:重生成/采用新稿前自动快照,一键回滚 |
| 批量生产需要熔断(wfcz10086) | **批量连写**:对未写章节依次 章纲→正文→(可选审校)→定稿,进度实时可见,连续 2 章失败自动熔断 |
| 去 AI 味是最受欢迎的润色能力(Humanizer-zh 20.9k★) | 润色提示词内置 AI 味负面清单(套话、排比腔、模板动作、形容词堆砌);支持**文风样本**注入模仿 |
| 强/快模型分工 + 费用透明是刚需 | **双模型档案**:强模型(大纲/正文/审校)、快模型(摘要/状态抽取);用量统计与 token 估算展示 |
| 拆书仿写是各家标配 | txt 导入按「第X章」正则自动拆章,可续写、可研结构;**EPUB/TXT/Markdown/DOCX** 四格式导出 |
| 部署复杂劝退(MongoDB/Docker/固定宿主) | **零外部服务**:SQLite 单文件 + 少量 pip 依赖,`python main.py` 即用 |
| 供应商锁定、硬编码模型名腐化 | OpenAI 兼容协议任意接入;**14 家供应商模板一键建档**、「拉取模型列表」免猜模型名,设置页在线增删与测试 |
| 生成黑盒、断流不可恢复 | 全部生成走 SSE 流式可见;生成在服务端线程完成,断流后结果照常落库 |

## 功能总览

- **设定页**:梗概/文风(含风格预设)、**文风样本**、世界观生成、角色档案表(手工+AI)、伏笔登记簿、**情节线追踪**、**Lorebook 关键词触发设定**、**设定变更影响反查**
- **大纲页**:一键生成全书章纲(卷+章+钩子),逐章标题/概要可编辑,手动增删章,**级联重生成**(改章纲后勾选下游章节批量重做,旧稿自动快照)
- **写作页**:章纲节拍生成、**上下文透视**、正文流式生成、**候选拉样+主编重排**(2~4 选 1)、**一致性门禁徽章**(字数/人物覆盖/重复率)、去 AI 味润色、**两步去 AI 味(span 定位改写)**、**版本历史与回滚**、**批量连写**(可熔断)
- **审校页**:评分卡(总分+四维)、带定位的问题清单、修改建议、按意见修订(预览→采用)、**独立盲审接入**(导出评分卡/回写结果,写评分离)、定稿(自动更新摘要+人物状态+伏笔+情节线)
- **导出页**:**EPUB**/TXT/Markdown/DOCX,txt 拆书导入,作品统计与模型用量
- **设置页**:**14 家供应商模板一键建档**(DeepSeek/GLM/Qwen/Kimi/Claude/Gemini/OpenAI/OpenRouter/硅基流动/火山方舟/MiniMax/Groq/Ollama/LM Studio)、「拉取模型列表」免猜模型名、多模型档案(名称/BaseURL/Key/模型/温度/max_tokens)、强/快/**Embedding** 模型指定、生成参数(章字数/上下文预算/摘要窗口/检索条数)、连接测试

## 架构

```
web/                     单页前端(原生 JS,无构建)
app/
  config.py              设置合并(.env < UI)、14 家供应商预设、强/快模型解析与降级
  db.py                  SQLite 存储(项目/角色/章节/伏笔/情节线/版本/用量/日志/Lorebook/向量缓存)
  llm.py                 OpenAI 兼容客户端:流式、JSON 容错解析、重试、模型列表拉取、embeddings、mock
  memory.py              三层记忆与上下文组装(字符二元组 BM25 / 可选向量余弦 / Lorebook 命中注入)
  prompts.py             全部中文提示词模板
  lint.py                确定性去 AI 味 lint(13 规则 + span 级定位,零 token)
  stages.py              流水线编排(生成/门禁/审校/修订/定稿/批量连写/候选拉样重排/级联/导入)
  routes.py              FastAPI 路由(生成类接口统一 SSE)
  export.py              TXT/Markdown/DOCX/EPUB 导出
tests/e2e_smoke.py       端到端冒烟测试(mock 离线全流程,40 步断言)
mcp_server.py            MCP Server(stdio),把流水线暴露给 CodeBuddy / WorkBuddy(22 个工具)
mcp.json                 MCP 客户端配置模板
skills/castnovel/        Agent 技能包(SKILL.md + API 速查),tools/install_skill.py 一键安装
tools/gemini_bridge.py   浏览器版 Gemini 桥接:把网页额度包成 OpenAI 兼容 API(免 API Key)
```

**流水线**:世界观 → 角色 → 全书大纲 → 章纲(节拍)→ 正文(+门禁)→ 润色 → 审校 → 修订 → 定稿(摘要 + 状态回写),每一步的产物都可手工编辑后继续。

## 快速开始

1. **安装**:`pip install -r requirements.txt`(Python 3.10+;DOCX 导出需要 python-docx,已含在 requirements 中)
2. **启动**:`python main.py [端口]`,默认 http://127.0.0.1:8000
3. **配置模型**:侧栏「⚙ 设置」→「＋ 用模板添加档案」选一家服务商(Base URL/模型名自动填好)→ 粘贴 API Key → 「测试」→ 保存;不知道模型名就点「拉取模型」直接从服务拉列表
   - 内置 14 家模板:DeepSeek / 智谱 GLM / 通义千问 / Kimi / OpenAI / Anthropic Claude / Google Gemini / OpenRouter / 硅基流动 / 火山方舟(豆包) / MiniMax / Groq / Ollama 本地 / LM Studio 本地(Claude 与 Gemini 走官方 OpenAI 兼容端点)
   - Ollama / LM Studio 免 Key;也可复制 `.env.example` 为 `.env` 预置
4. **创作**:新建作品 → 写一句话梗概 → 生成世界观(改到满意)→ 生成角色 → 生成大纲(30 章起步)→ 逐章「章纲 → 生成正文 → 润色 → 审校 → 修订 → 定稿」→ 导出

**推荐模型分工**:强模型用能力较强的 chat 模型(写作质量主要取决于它);快模型用便宜快速的模型(摘要/状态抽取),单章成本可降低约 40%。想要旧情节检索更准,可在设置里指定 **Embedding 档案**(需服务支持 `/embeddings`,如 OpenAI/硅基流动/智谱),异常自动回退 BM25。

## 用浏览器里的 Gemini 驱动全流程（不要 API Key）

不想买 API Key，只想用自己已登录的 [gemini.google.com](https://gemini.google.com/) 额度来写小说？项目内置 `tools/gemini_bridge.py`：它在本地起一个 **OpenAI 兼容小服务**。

**两条实现路径**，默认走第一条：

| 后端 | 原理 | 需要浏览器常驻 | 稳定性 |
|---|---|---|---|
| `cookie`（默认，推荐） | 复用 [gemini-webapi](https://github.com/HanaokaYuzu/Gemini-API) 逆向 Gemini 网页版内部 RPC（`batchexecute`），拿一次 Cookie 即可 | ❌ 不需要 | 高，无 DOM 选择器可坏 |
| `dom`（兜底） | Playwright 驱动浏览器，模拟人打字、抓页面 DOM | ✅ 必须开着 | 低，前端改版即失效 |

```
CastNovel(app/llm.py) ──OpenAI 协议──▶ gemini_bridge.py:8765 ──内部 RPC──▶ gemini.google.com
                                                                （cookie 后端：无浏览器）
```

项目侧**代码零改动**：流水线、门禁、审校、定稿、导出全部照旧，MCP Server 同样生效，只是把「模型后端」换成网页版。

### 1. 准备运行时

`gemini-webapi` 要求 **Python ≥ 3.11**（本项目主环境是 3.10），所以桥接跑在独立 venv 里。桥接启动时若发现自己不在该 venv，会**自动切过去**，你不用记两个解释器：

```bash
uv venv data/gemini-venv --python 3.13
uv pip install --python "data/gemini-venv/Scripts/python.exe" gemini_webapi fastapi "uvicorn[standard]" websockets
```

没有 uv 的话：`py -3.13 -m venv data/gemini-venv && data\gemini-venv\Scripts\pip install ...`。

### 2. 一次性登录 + 取 Cookie

```bash
python tools/gemini_bridge.py chrome     # 自动找到 Chrome 并以独立 profile 启动（D:/chrome-cdp）
```

在弹出的窗口里**登录 Google**、打开 <https://gemini.google.com/app>，然后：

```bash
python tools/gemini_bridge.py login      # 通过 CDP 读取 cookie，写入 data/gemini_cookies.json 并立即验活
```

看到「验证通过，可用模型：…」就成了，**之后可以把 Chrome 关掉**，桥接不再需要浏览器。

> Cookie 等同于登录凭证，`data/gemini_cookies.json` 已被 gitignore，别外传。
> 用独立 profile（而不是你日常 Chrome）取 Cookie，可避免内部刷新把浏览器端顶下线。

### 3. 起服务

```bash
python tools/gemini_bridge.py serve --port 8765
python tools/gemini_bridge.py ask "只回复四个字:连接成功"    # 试跑一次
python tools/gemini_bridge.py probe                       # 看账号状态与可用模型
curl http://127.0.0.1:8765/health
```

### 4. 在 CastNovel 里启用

```bash
python tools/gemini_bridge.py profile      # 写入 Base URL=http://127.0.0.1:8765/v1 / key=web / model=gemini-web
```

也可侧栏「⚙ 设置」→「+ 新增档案」手工填同样三项。然后：

- 点该档案的「测试」，应回「连接成功」；
- **强模型**设为「Gemini 网页版」（大纲 / 正文 / 审校）；**快模型**建议仍用原 API 或「离线演示」——摘要/状态抽取调用次数多，网页版额度不经用。

> 全新数据库下该档案已内置在 `app/config.py`；已有设置以库里为准，所以用上面的命令或手工加。
> 想指定模型：`serve --model gemini-3-pro`（可用名字见 `probe` 输出）。

### 5. 已知限制（务必先看）

| 项 | 说明 |
|---|---|
| 额度 | 免费版有小时/天级别的对话次数限制；批量连写 30 章会被限流，`gemini-webapi` 会抛 `UsageLimitExceededError` 并被桥接报成明确错误 |
| 串行 | 网页版同一账号一次只能跑一个会话，桥接器用锁排队，**并发请求等待而不是失败** |
| 输出长度 | 网页版单次回复有上限（约万级字符），超长章节会被截断，门禁会显示字数不达标 |
| Cookie 失效 | 账号改密、异地登录、长期不用都会导致失效，重跑 `login` 即可；`__Secure-1PSIDTS` 由库自动刷新 |
| 风控 | 高频自动化可能触发 Google 风控（`TemporarilyBlockedError`），批量前先小样本试跑 |
| 条款 | 逆向网页版接口可能违反 Google 服务条款；`gemini-webapi` 为 **AGPL-3.0**，作为独立进程经 HTTP 调用，注意合规 |

### 6. 兜底：DOM 后端

Cookie 方式被封或暂时不可用时，可以退回浏览器驱动：

```bash
pip install playwright
python tools/gemini_bridge.py serve --backend dom --mode attach --cdp http://127.0.0.1:9222
python tools/gemini_bridge.py probe --backend dom     # 打印页面结构，改版后先跑它
```

所有选择器集中在 `gemini_bridge.py` 顶部（`JS_FILL` / `JS_SEND` / `JS_EXTRACT`），改版后改这三段即可。注意该后端**浏览器必须一直开着**。

### 7. 排查

- `找不到 data/gemini_cookies.json` → 还没 `login`。
- `AuthError` / 400 → Cookie 过期，重跑 `login`。
- `UsageLimitExceededError` → 撞额度上限，等冷却或换账号/Cookie。
- 503 → 桥接服务没起；先看启动窗口里的报错。
- 生成内容明显异常（空、极短）→ 先 `ask` 单条试跑，确认是桥接问题还是提示词问题。

## 接入 CodeBuddy / WorkBuddy

两种方式,可叠加使用:

### 方式一:安装 Agent 技能(推荐,让 Agent 懂怎么用)

项目自带标准 Agent Skills 格式的技能包(`skills/castnovel/`:SKILL.md 创作方法论 + API 速查)。一键安装到本机技能目录:

```bash
python tools/install_skill.py          # 自动装到 ~/.workbuddy/skills、~/.codebuddy/skills 等检测到的目录
python tools/install_skill.py --dir 某目录   # 或手动指定
python tools/install_skill.py --remove      # 移除
```

装完重启 WorkBuddy / CodeBuddy,对话里直接说「**写一部都市悬疑小说**」「**给《xx》续写第 5 章,写完审校定稿**」「**导出 epub**」,Agent 会按技能里的黄金路径驱动整条流水线(新书从零到一 / 单章循环 / 写评分离盲审 / 设定维护 / 导出)。

### 方式二:配置 MCP Server(把流水线变成 Agent 的工具)

项目内置 **MCP Server**(`mcp_server.py`,stdio 传输)。CodeBuddy(IDE / CLI)与 WorkBuddy(腾讯 AI 工作台)都支持 MCP,配置同一份 `mcpServers` JSON 即可。

**原理**:MCP Server 进程内直接调用 `app.stages`,与 Web 界面共享同一个 SQLite(WAL)。因此**不需要先启动 `python main.py`**,两个入口可同时使用。

复制项目根 `mcp.json`,把 `args` 里的脚本路径改成你本机的绝对路径(Windows 若 `python` 未进 PATH,`command` 换成解释器完整路径):

```json
{
  "mcpServers": {
    "castnovel": {
      "type": "stdio",
      "command": "python",
      "args": ["E:/path/to/xiaoshuo/mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" },
      "description": "CastNovel 铸书 · AI 长篇小说创作工坊"
    }
  }
}
```

- **CodeBuddy**:侧边对话面板右上 **Settings → MCP → Add MCP**,粘贴上面的 JSON → 保存,状态变绿后点 **Try to Run** 验证。
- **WorkBuddy**:侧栏 **插件 → 右上角 MCP 服务器 → 配置**,粘贴 JSON → 保存,状态 🟢 即成功;命令行版可用 `/mcp` 查看状态与诊断。

#### 提供的工具(22 个)

| 分组 | 工具 |
|---|---|
| 作品 | `list_projects`、`create_project`、`get_project_overview` |
| 设定 | `generate_worldview`、`generate_characters`、`generate_outline` |
| 章节 | `generate_beats`、`generate_draft`、`generate_draft_best`(候选拉样重排)、`polish_chapter`、`deai_rewrite_chapter`(两步去 AI 味)、`review_chapter`、`finalize_chapter` |
| 读写 | `read_chapter`、`update_chapter`、`export_novel` |
| 盲审 | `export_review_prompt`、`submit_review` |
| 设定维护 | `list_lore_entries`、`upsert_lore_entry`、`delete_lore_entry`、`search_impact`(影响反查) |

### 3. 对话示例

> 「列出我的作品,给《雨港铜钱》补第 4 章:先出 3 份候选挑最好的,再两步去 AI 味,跑审校,评分低于 80 就按意见重写,定稿后导出 epub。」

Agent 会依次调用 `list_projects` → `get_project_overview` → `generate_draft_best` → `deai_rewrite_chapter(adopt=True)` → `review_chapter` → `finalize_chapter` → `export_novel`。

**约定**:章节用章号(`chapter_idx`)定位;生成类工具耗时长(正文约 1~3 分钟,候选重排约 n 倍),返回的是门禁结果 + 截断预览,全文用 `read_chapter` 取;`polish_chapter` / `deai_rewrite_chapter` 默认不写库,加 `adopt=True` 才落盘且自动快照旧稿。

### 4. 让 CodeBuddy 参与本项目的开发

除了把项目当工具调用，也可以让 CodeBuddy 直接改这个仓库：新建 `.codebuddy/rules/` 目录并放入一份规则（技术栈 FastAPI + 原生 JS 单页、生成类接口统一 SSE、润色/修订不直接覆盖原稿、提示词集中在 `app/prompts.py`），CodeBuddy 会在 Craft/Plan 模式下自动遵循。

## 测试

```bash
# 必须带 CASTNOVEL_FORCE_MOCK:强制全流程离线,即使库里有真实 Key 也绝不消耗额度
CASTNOVEL_FORCE_MOCK=1 python main.py 8901 &   # Windows CMD: set CASTNOVEL_FORCE_MOCK=1 再启动
python tests/e2e_smoke.py http://127.0.0.1:8901
```

测试以内置 mock 模型离线运行,40 步断言覆盖从建项到导出的全流程与 v0.3 新特性(Lorebook 注入、影响反查、候选重排、两步去 AI 味、级联重生成、向量检索路径),不消耗任何 API 额度。

## Roadmap

- [x] v0.2:一致性门禁(含 13 条 AI 味 lint)、版本历史+回滚、批量连写(熔断)、情节线、文风样本、EPUB 导出
- [x] v0.3:span 级检测+带例改写的两步去 AI 味管线(arXiv:2409.14509)
- [x] v0.3:生成候选拉样+重排(Re3 的 Sample-Rank 思路;落选稿进版本历史作非正典分支)
- [x] v0.3:Lorebook 式关键词触发注入(SillyTavern/NovelAI 的 World Info 机制)
- [x] v0.3:设定变更影响分析(天命的「反查受影响章节」,零 token)+ 级联重生成
- [x] v0.3:可选向量语义检索(配置 Embedding 档案自动启用,异常回退 BM25)
- [x] v0.3:14 家供应商模板一键接入 + 模型列表拉取 + Agent 技能包(WorkBuddy/CodeBuddy)
- [ ] 读者认知状态(读者已知/期待/误认,与作者真相分离)

## License

MIT
