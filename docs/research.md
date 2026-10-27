# AI 写小说开源项目调研汇总(2026-10)

> 调研方法:通过 GitHub API 与仓库 README 实时抓取(非记忆),覆盖中英文生态约 15 个项目与 5 个学术系统。
> star 数为 2026-10-27 实查值。本文是 NovelForge(铸书)设计的直接依据,文末给出「痛点 → 设计对策」映射。

## 一、中文生态

### 1. YILING0013/AI_NovelGenerator — 6,349 ★(最热)
- **形态**:FastAPI + Next.js 16 / React 19 Web 工作台;旧版为 Python 桌面 GUI + 本地向量库。AGPL-3.0。
- **流水线**:想法 → 故事方向 → 阵营/人物/关系 → 分卷 → 篇/场景/章节计划 → 逐章正文 → 审阅保存;新版还有可视化工作流画布(React Flow),节点可指定模型。
- **一致性**:集中式设定库 + 章节记录(人物/关系/物品/世界规则变化);旧版为向量召回 + 全局摘要/角色状态/情节线滚动更新 + 独立一致性审校模块。
- **缺点**:必须 Docker 跑 MongoDB 副本集,部署重;刷新/重启不保证恢复生成;新旧版数据完全不兼容。

### 2. MaoXiaoYuZ/Long-Novel-GPT — 1,248 ★
- **形态**:Web(Docker 一键部署),OpenAI 兼容接口,实时显示 API 费用。无许可证文件。
- **流水线**:LDG 分层生成——50 章×200 字 → 1k 字剧情 → 2k 字正文,自上而下扩写;支持导入拆书、按修改意见改写、多窗口并行。
- **一致性**:以「剧情纲要」为中心状态,改稿后同步更新;检索相关正文片段作上下文。
- **缺点**:txt 导入解析乱码/报错集中;模型适配滞后;已近一年未推送,issue 无人修。

### 3. PenglongHuang/chinese-novelist-skill — 4,428 ★(增速最快)
- **形态**:Claude Code / coding agent 的 Skill 提示词包,非独立应用。
- **流水线**:三层递进问答 → 大纲/人物/写作计划 JSON → 串行或子 Agent 并行写章(写前分析 → 3000-5000 字 → 润色去 AI 味 → 字数检查 → 更新摘要)→ 自动校验不合格重写(≤3 轮)。
- **亮点**:提示词工程极精细(13 种悬念钩子、「展示而非讲述」、AI 味负面清单);逐章摘要记忆 + 断点续写。
- **缺点**:深绑 coding agent,长篇并行耗额度大(「额度比灵感先耗尽」);参数固定、灵活性有限。

### 4. EthanYoQ/AI-Novel-Writer(书童)— 1,928 ★(工程质量标杆)
- **形态**:Electron 桌面应用,本地优先(SQLite + 本地文件夹),WebDAV 备份。GPL-3.0。
- **流水线**:设定 → 架构(前提/角色/世界观/大纲)→ 章节蓝图(目标/冲突/钩子/出场人物)→ 草稿 → 审稿(意见逐条勾选)→ 修稿(差异对比、逐段确认、不直接覆盖)→ 定稿;支持 txt/md/epub 拆书仿写、批量创作(≤10 章)。
- **一致性**:上下文注入制(蓝图+相关角色+世界观+前文摘要+上一章全文);角色卡带当前状态;剧情/伏笔按章追踪;可选向量语义检索,退化时全文检索。
- **缺点**:安装包未签名;部分第三方 API 需独立适配器。

### 5. LiPu-jpg/Openwrite — 889 ★
- **形态**:DeepSeek Harness 插件(浏览器工作台),90 个 novel_* 领域工具。Apache-2.0。
- **亮点**:六域审稿 DAG(评审必须附正文原句证据);`auto:cheapest` 按价格目录自动选模;/canon 正典系统与 /learn 写作记忆;导出 Markdown/TXT/EPUB。
- **缺点**:强绑定特定 dsh 版本号;Windows 文件锁等工程问题密集。

### 6. wfcz10086/AI-automatically-generates-novels — 965 ★
- **形态**:Flask + 守护进程无人值守长跑,四种输出(小说/短剧/剧本/分镜)。Apache-2.0。
- **亮点**:全场唯一把一致性做成**程序化校验**的——合同树(相邻节点出口==进口逐字段比对)+ 硬账台账(兵力钱粮程序记账)+ 评审只报带原句证据的问题 + 熔断(连续 5 章需人工即停);357 条单测。
- **缺点**:调用量惊人(顺利 27-37 次/章,返修密集约 80 次/章);Flask 开发服务器单机自用。

### 7. zy-zmc/tianming-novel-ai-writer(天命)— 453 ★(理论最系统,已半弃)
- **形态**:WPF 桌面(仅 Windows),Semantic Kernel。
- **亮点**:**15 维事实快照 → 12 类 CHANGES 变更声明 → 6 道生成门禁**(协议解析、ID 引用、快照一致、未知实体、外貌/地点核对、蓝图出场),「写到 3000 章依然连贯」;改设定可反查受影响章节;本地 ONNX 语义检索。
- **缺点**:CHANGES 中英映射 bug 自标「必须修」;源码丢失半弃维护;仅 Windows。

### 其他
- zenstory-ai/oh-story-claudecode(8,053 ★,Claude Code 中文网文技能包)、modoojunko/awesome-novel-agent(843 ★)、sigpanic/goink(482 ★,Go 桌面+本地语义检索)、huihuijiangqiang/moshu 墨枢(64 ★,Vue3+FastAPI+RAG)。

## 二、英文生态与学术系统

### 项目
| 项目 | star | 形态 | 核心设计 | 主要问题 |
|---|---|---|---|---|
| mshumer/gpt-author | 2,534 | Notebook | 候选情节→择优→章纲→顺次写章→EPUB | 无记忆/修订,上下文崩溃(issue 重灾区),已停更 |
| principia-ai/WriteHERE | 975 | CLI+Web | 异构递归规划:任务图混合检索/推理/写作子任务,边写边改计划 | 本地模型支持差;研究级健壮性;无正式license |
| aiwaves-cn/RecurrentGPT | 1,000 | CLI+Gradio | 语言即 LSTM:每步输出段落+短期记忆改写+下一步计划,长期记忆可检索 | OpenAI-only;长程漂移;已停更 |
| notnotype/neuro-book | 753 | 本地 IDE(Bun) | World Engine 事件溯源;伏笔「承诺账本」(plant/advance/payoff);llmlint 360 条 AI 味检测规则 | v2 重建中,旗舰功能未落地 |
| iLearn-Lab/NovelClaw | 380 | Web(FastAPI) | 可编辑记忆库 + memory_snapshot/character_setting 回写事件,run 产物全部落盘可查 | 文档营销味重,架构细节不透明 |
| raestrada/storycraftr | 175 | CLI+VSCode | 终端 REPL 驱动,全书中途插入章节(!iterate),本地 embedding | beta 期,命令式而非流水线 |
| hamishfromatech/bookgpt | 3 | Flask Web | Cursor 式 Agent 循环(计划→研究→写→改→精修)+ 章节版本 diff + 跨章一致性检查 | 几乎无采用者 |

### 学术方法(可落地的要点)
- **Re3**(EMNLP 2022):Plan→Draft→Rewrite→Edit;候选拉样+重排;实体一致性改写;大纲是可重载的状态。
- **DOC**(ACL 2023):**大纲粒度决定下限**——3 条粗纲管不住 3500 字;要细化到「每拍一个原子事实」,并维护「已覆盖清单」防漂移防重复。
- **RecurrentGPT**:最便宜可靠的长上下文方案——{短期摘要, 下一步计划, 检索到的相关旧摘要}每步随行。
- **WriteHERE**(EMNLP 2025 口头报告):规划不是一次性大纲,而是随写作进展动态增删子任务的任务图;把图暴露到 UI 是长篇项目可检查/可恢复的最佳设计。
- **Plan-and-Write**(AAAI 2019):「发生什么」(可编辑的符号化计划)与「怎么措辞」分离;采样多个计划供人选。

## 三、共性结论(设计的直接输入)

1. **一致性技术路线已换代**:纯向量检索派 → 「结构化状态回写 + 程序门禁」派(天命、wfcz10086、新版 AI_NovelGenerator、NovelClaw)。共识:**不能指望模型记忆,也不能只靠检索**。
2. **人工监督是刚需**:连 Long-Novel-GPT 都自述「用户监督下达到签约门槛」;书童的「审稿意见逐条确认、修稿逐段确认不直接覆盖」是最受欢迎的交互。
3. **最受欢迎功能**(按 star 增速与 issue 反推):拆书仿写导入、去 AI 味润色、便宜模型任务分工(大纲用 Flash、正文用 Pro)+ 费用显示、本地模型(Ollama)支持。
4. **用户最大痛点**:部署复杂(MongoDB/Docker/固定版本宿主)、txt 编码解析、生成中断不可恢复、API 重试重复计费、「OpenAI 兼容」各家的实际差异。
5. **形态趋势**:2024-2025 是「Python 桌面 GUI + 向量库」;2026 分化为 coding agent Skill、精品桌面应用、程序化流水线三条路线;头部项目全部在 2026 年内诞生或重写。

## 四、NovelForge(铸书)的对策映射

| 调研发现的问题/最佳实践 | NovelForge 的设计 |
|---|---|
| 不能只靠模型记忆/只靠检索 | **三层记忆**:世界观+角色档案(静态)→ 滚动摘要+上一章结尾(序列)→ 字符二元组 BM25 检索旧情节(检索),外加**待回收伏笔清单**注入每次正文生成 |
| 结构化状态回写 | 章节定稿时用快模型抽取**人物状态变化 + 伏笔埋设/回收**,回写故事圣经,下一章自动携带最新状态 |
| 细纲粒度不足(DOC) | 大纲(章章「目标-阻碍-转折」+钩子)→ 章纲节拍(场景级)两级细化,正文严格对拍生成 |
| 人工监督(书童模式) | 全流程产物可编辑;润色/修订**先预览再采用**,原稿不被直接覆盖 |
| 去 AI 味(chinese-novelist-skill / neuro-book) | 润色提示词内置 AI 味负面清单(套话/排比/模板动作/形容词堆砌) |
| 模型分工与成本(AI_NovelGenerator / Openwrite) | 强/快双档案分档(创意 vs 摘要抽取),用量按字符统计并估算 token 展示 |
| 拆书仿写(各家标配) | txt 导入按「第X章」正则拆章,导入即可续写/研结构 |
| 部署复杂劝退 | **零外部服务**:pip 依赖只有 fastapi/uvicorn/openai/dotenv,SQLite 单文件,`python main.py` 即用 |
| 断不可恢复 / 生成黑盒 | 生成在服务端线程完成,断流后结果照常落库;所有 SSE 过程文本在 UI 可见 |
| 供应商锁定/硬编码模型 | OpenAI 兼容协议任意接入,设置页可增删档案并在线测试;内置 mock 模式离线跑通全流程(也用于自动化测试) |

## 五、NovelForge 刻意不做的(v1 范围控制)

- 可视化工作流画布(AI_NovelGenerator 新版的路线)——工程量大,收益对单人作者有限;
- MongoDB/向量数据库——BM25 对中文摘要在几千章内足够,零运维;
- 无人值守全自动整书(wfcz10086 路线)——成本与质量不可控,违背「人为主编」定位;
- EPUB 导出——txt/md/docx 已覆盖主流发布流程,EPUB 列入 Roadmap。

## 来源

- https://github.com/YILING0013/AI_NovelGenerator
- https://github.com/MaoXiaoYuZ/Long-Novel-GPT
- https://github.com/PenglongHuang/chinese-novelist-skill
- https://github.com/EthanYoQ/AI-Novel-Writer
- https://github.com/LiPu-jpg/Openwrite
- https://github.com/zy-zmc/tianming-novel-ai-writer
- https://github.com/wfcz10086/AI-automatically-generates-novels
- https://github.com/mshumer/gpt-author
- https://github.com/principia-ai/WriteHERE
- https://github.com/aiwaves-cn/RecurrentGPT
- https://github.com/notnotype/neuro-book
- https://github.com/iLearn-Lab/NovelClaw
- https://github.com/raestrada/storycraftr
- https://github.com/hamishfromatech/bookgpt
- https://github.com/yangkevin2/emnlp22-re3-story-generation (Re3)
- https://github.com/yangkevin2/doc-story-generation (DOC)
- https://arxiv.org/abs/1811.05701 (Plan-and-Write)
- https://arxiv.org/abs/2503.08275 (WriteHERE)
- star 数均由 GitHub API 于 2026-10-27 实查;个别未能核实的字段已在原文标注。
