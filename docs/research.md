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

---

# 第二轮调研(2026-10-28 补充)

> 应「调研更全面」的要求扩大扫描面。本轮覆盖中文第二梯队与邻近生态 11 个新项目(全部 GitHub 实查验证),
> 最大发现:**生态地位第二、第三的项目(inkos 10.8k★、webnovel-writer 7.7k★)在第一轮名单之外**,
> 以及一张 205 个 skill 的全景清单(UnderleveledBuilder/awesome-novel-skills)。

## 六、中文第二梯队(11 个新项目)

| 项目 | star | 形态 | 一句话核心 |
|---|---|---|---|
| [Narcooo/inkos](https://github.com/Narcooo/inkos) | ~10.8k | TS monorepo,Studio/TUI/CLI | 故事状态为 Zod 校验的规范 JSON,伏笔四态,`detect` AIGC 检测与 `forecast` 非正典分支对比 |
| [zenstory-ai/oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode) | 8,059 | Claude Code skills(13 skill+7 agent) | 「文件即记忆」:`_tracking-state.json` 唯一权威 + 12KiB 上下文状态卡 + 「作者真相 vs 读者已知」双时间线 + hooks 写入门禁 |
| [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) | 7,717 | Claude Code 插件 + CLI + Dashboard | **契约→提交事实账本**:新事实只能经 CHAPTER_COMMIT 入账,摘要/索引/向量全是只读派生投影,可审计可重放 |
| [modoojunko/awesome-novel-agent](https://github.com/modoojunko/awesome-novel-agent) | 843 | 9 agent 分工 | 24 套题材画像,去 AI 味规则按题材组织,style-distiller 独立成 agent |
| [sigpanic/goink](https://github.com/sigpanic/goink) | 485 | Wails Go 桌面(<60MB) | 六类结构化状态(含**读者认知**、关系有向图、层级地图);AI 编辑先出 Diff 批准才写入;本地 ONNX 语义搜索;风格蒸馏成 Skill |
| [weike-zhang/ai-novel-studio](https://github.com/weike-zhang/ai-novel-studio) | 235 | Next.js+Go 互动小说引擎 | **「模型只能提案,验证器裁决」**——状态补丁经校验才提交 |
| [huihuijiangqiang/moshu(墨枢)](https://github.com/huihuijiangqiang/moshu) | 64 | Vue3+FastAPI+pgvector | **Token 预算显式管理**;检索排除当前/未来章与过时摘要;**确定性规则优先守卫**(账目由代码算,LLM 不负责算术结论);转义低信任边界防 prompt 注入 |
| [real-Elysia886/inkflow(墨流)](https://github.com/real-Elysia886/inkflow) | 37 | FastAPI 三栏工作台 | **双层审计**:代码层零 token 硬规则(死人不能复活/消耗品台账/破折号每章上限)+ LLM 层打分,不合格自动重写 |
| [ynnyh/jarvis-write(藏山)](https://github.com/ynnyh/jarvis-write) | 15 | Tauri+FastAPI+PWA | **时间性设定圣经**(事实绑定章节有效区间);**级联大纲引擎**(改章纲→影响分析→勾选→级联重生成);去 AI 味量化闭环(定点重写→复测→收敛/回滚) |
| [myyimu/ai-novel-diagnosis](https://github.com/myyimu/ai-novel-diagnosis) | 22 | Next.js+NestJS 诊断台 | 不做生成做诊断:结论必须带原文证据;V1→V2 复诊对比,复发问题沉淀方法卡;可摄入提示词归因 |
| [y1064709321/tianyan(天衍)](https://github.com/y1064709321/tianyan) | 0 | TS+Hono+SQLite | 36 维审计 + 扫榜(番茄字体本地反解);128k 窗口 75% 触发滚动摘要 |

邻近高分项目:[op7418/Humanizer-zh](https://github.com/op7418/Humanizer-zh)(20.9k★,中文去 AI 味 31 检查点)、[Nanako0129/sepia](https://github.com/Nanako0129/sepia)(3.5k★)、[lieflat-less-ai-tone](https://github.com/larashero3-dotcom/lieflat-less-ai-tone)(4.1k★,283 万字语料验证的 11 项 AI 特征)、jiaw-Zh/long-novel-writer(六层摘要冻结)。

## 七、第二轮的核心洞察

1. **生态已收敛出共识架构**:状态库/事实账本 + 生成门禁 + 去 AI 味 lint + 方法论 Skill 化。差异化正从「能不能写长」转向「**记忆是否可审计、门禁是否确定性**」。
2. **三种一致性哲学并存**:
   - 账本派(webnovel-writer):事实只能经提交入账,投影只读、可重放;
   - 时间轴派(jarvis-write/goink):事实绑定章节有效区间,可查询「第 N 章时的状态」;
   - 门禁派(oh-story/wfcz10086/inkflow):写前状态卡约束、写后确定性校验拦截。
3. **去 AI 味从「提示词技巧」进化为「可测量的工程」**:本地 lint 给行号(blocking/advisory 分级)→ 定点重写 → 复测 → 收敛/回滚的闭环;社区已有 Humanizer-zh 等可复用检查点。
4. **「读者认知」是一等状态**(goink/oh-story/inkflow 不约而同):单独追踪读者已知/期待/误认,与作者真相分离——控制悬念与反转时机的唯一系统化方案。
5. **前置链路被补齐**:扫榜 → 拆文 → 开书成为标配(oh-story/tianyan/fanqie-radar),写作之前先解决「写什么」。

## 八、第二轮调研对 NovelForge 的输入

已直接落地为 v0.2(见 [design.md](design.md)):
- inkflow/oh-story 的**双层审计** → 程序化一致性门禁(字数/人物覆盖/章节间重复率,零 token);
- 书童/goink 的**版本保护** → 章节版本历史 + 自动快照 + 回滚;
- wfcz10086 的**熔断** → 批量连写连续 2 章失败自动中止;
- goink 的**风格蒸馏**输入 → 文风样本注入(简化版);
- 天命/inkos 的**多线叙事状态** → 情节线追踪(开启/推进/完结);
- DaisyWriter 的**下游发布** → EPUB 导出。

规划吸收(v0.3+,见 design.md 路线图):确定性 lint 规则集(借 Humanizer-zh)、读者认知状态、级联大纲影响分析、非正典分支对比(inkos forecast)、扫榜拆文前置链路。

---

# 第三轮调研(2026-10-28 补充):2024-2026 学术前沿

> 聚焦「不训练模型也能落地的编排技术」,全部经 arXiv/GitHub 实查验证。

## 九、关键论文与可落地要点

### 长输出(突破 2k 字墙)
- **LongWriter / AgentWrite**(THUDM, ICLR 2025, [arXiv:2408.07055](https://arxiv.org/abs/2408.07055)):模型有效输出长度被 SFT 数据上限卡死,而非能力上限;**AgentWrite 零训练编排**——规划器输出带逐段字数预算的分节计划,逐段写作(各段携带大纲+前段滚动摘要),实测 2 万字连贯输出。
- **Self-Lengthen**([arXiv:2410.23933](https://arxiv.org/abs/2410.23933)):短稿→迭代拆分扩写→自举,"先短后扩"对任何 API 模型可用。
- **SuperWriter**(ACL 2026, [arXiv:2506.04180](https://arxiv.org/abs/2506.04180)):计划-写作-精修三段间插入显式反思步骤,7B 敌过大模型。

### 长篇 Agent 框架
- **StoryWriter**(CIKM 2025, [arXiv:2506.16445](https://arxiv.org/abs/2506.16445)):**事件粒度**规划(非章节粒度)+「相对当前事件的历史压缩」优于朴素滚动摘要。
- **Agents' Room**(DeepMind, ICLR 2025, [arXiv:2410.02603](https://arxiv.org/abs/2410.02603)):4 规划 agent 写共享草稿板 + 5 写作 agent 按弗赖塔格金字塔分工,**确定性编排**即可,无需 agent 间协商。
- **CreAgentive**([arXiv:2509.26461](https://arxiv.org/abs/2509.26461)):类型无关的「故事原型」——人物/事件/环境三元组知识图谱,故事逻辑与文笔分离;千章成本 <$1/100章。
- **NstAgent**(清华, 2026.9, [arXiv:2609.35759](https://arxiv.org/abs/2609.35759))★最有参考价值:训练免**双向叙事状态**——人物快照+过去事件+**未来义务(未回收伏笔/未兑现承诺)**;写手只见大纲+状态+检索证据,**永不喂原文**;每章一次原子 JSON 更新;10 万字一致性显著领先,成本 $0.13-0.17/万字。
- **DOME**(NAACL 2025):大纲不冻结——记忆更新可触发大纲增删重排,写作前对记忆库做时间冲突分析。

### 一致性评估
- **ConStory-Bench/Checker**(Microsoft, 2026.3, [arXiv:2603.05890](https://arxiv.org/abs/2603.05890)):5 类 19 亚型一致性错误分类学;**两步检查管线**(按类抽取可疑片段 → 两两矛盾核验带证据链),零训练可复刻;错误随长度线性增长,事实细节与时间线错误占主导。
- **LitBench**([arXiv:2507.00769](https://arxiv.org/abs/2507.00769)):评委 LLM 与人类一致率仅 ~73%,**成对比较远比绝对打分可靠**。
- **StoryAlign**(2026, [arXiv:2605.04831](https://arxiv.org/abs/2605.04831)):通用 reward 模型对小说仅 ~66% 准确率;best-of-N + 成对评委是不训练的替代。
- **WritingBench**(NeurIPS 2025):先从任务派生评审细则,再逐条打分,评委一致性显著提升。

### 去 AI 味(最重要的反直觉结论)
- **"Can AI Writing Be Salvaged?"**([arXiv:2409.14509](https://arxiv.org/abs/2409.14509)):18 位 MFA 编辑实测——**抽象风格指令("避免陈词滥调")完全无效**;有效的是**两步 span 级管线**:带标注例句的片段检测 → 按类目改写(定义+25 个专家改写例);编辑 74% 是替换、18% 是删除(偏删原则);禁止整段自我润色(无效)。
- **风格模仿**([arXiv:2509.24930](https://arxiv.org/abs/2509.24930)):**把样章作为"续写上文"注入(99.9% 风格一致)远胜"模仿 X 风格"指令**(few-shot 最高 23.5 倍提升);提示策略比模型大小更重要。

### 记忆(超越朴素 RAG)
- **SCORE**([arXiv:2503.23512](https://arxiv.org/abs/2503.23512)):最便宜的实用记忆 = 「关键条目状态表 + 每集一段摘要」,同时用于检索与矛盾校验。
- **Narrative World Model**([arXiv:2607.05577](https://arxiv.org/abs/2607.05577)):类型化时间状态图,回答"第 12 章时谁知道秘密"类多跳问题;关键在**边类型**(知识/状态随时间),而非通用实体共现。

## 十、学术调研对 NovelForge 的输入

- **NstAgent 的「未来义务」**与本项目伏笔账本同构 → 设计文档 5.2 获学术背书,并确认"永不喂原文、只喂状态+检索证据"路线正确;
- **AgentWrite 字数预算** → 门禁字数检查的强化方案(逐节拍字数预算)列入后备;
- **ConStory-Checker 两步矛盾检查** → 列入 v0.3(与确定性 lint 并列的模型层检查);
- **Span 级去 AI 味 + 续写式文风样本** → 文风样本注入实现方式获实证支持(作为续写上下文而非风格指令);lint 规则升级为「检测+带例改写」两步;
- **成对评委 > 绝对评分** → 审校评分卡保留(用户可读),候选择优改用 pairwise。

来源:上述 arXiv 链接;汇总清单 [Awesome-Story-Generation](https://github.com/yingpengma/Awesome-Story-Generation)。

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
