---
name: castnovel
description: 用 CastNovel(铸书)工坊创作 AI 长篇小说:设定→大纲→章纲→正文→审校→定稿→导出全流程。当用户想写小说、续写章节、改稿润色、导出成书,或提到 CastNovel/铸书/我的作品时使用本技能。
version: 1.0.0
metadata: 'author: CastNovel requires: CastNovel 仓库(mcp_server.py 或 python main.py)'
license: MIT
---

# CastNovel 铸书 · AI 长篇小说创作技能

把小说创作流水线交给 Agent 驱动:你负责编排与决策,工坊负责记忆一致性、确定性门禁与版本管理。

## 第一步:选择接入方式

按顺序探测,取第一个可用者,后续所有操作走同一条通道:

1. **MCP 工具**(首选):若当前会话可见 `list_projects`、`generate_draft` 等工具,直接用 MCP 工具通道(见 `references/api.md` 工具表)。无需启动任何服务。
2. **REST API**:探测本机服务 `curl -s http://127.0.0.1:8000/api/projects`。通了就走 REST(端点表见 `references/api.md`)。
3. **启动服务**:`python main.py [端口]` 后用 REST;生成类接口为 SSE 流,解析方式见 `references/api.md`。

若模型未配置(测试调用报错或返回模拟文本),引导用户完成一次配置即可:设置页(或 `PUT /api/settings`)选一家供应商填 Key;或改用 `mock://local` 档案离线演示。

## 创作黄金路径

**新书从零到一**:
`create_project`(书名/类型/一句话梗概,越具体越好)→ `generate_worldview` → 人工确认/修改 → `generate_characters` → `generate_outline`(20~60 章)→ 逐章循环:章纲 → 正文 → 润色 → 审校 → 定稿 → 全部完成后 `export_novel`(epub/txt/md/docx)。

**单章写作循环**(每章重复):
1. `generate_beats`(场景级节拍;已有则跳过)
2. `generate_draft` 生成正文 —— 关键章节用 `generate_draft_best`(n=2~4 候选拉样重排,质量下限更高,耗时约 n 倍)
3. 看**门禁结果**(字数/角色覆盖/重复率/AI 味):有 warn 先解决再继续
4. `review_chapter` 审校;评分低或问题多时 `deai_rewrite_chapter` / 修订
5. 满意后 `finalize_chapter`(摘要 + 人物状态/伏笔/情节线回写,下一章自动携带)

**改稿不覆盖原则**:润色/去 AI 味/修订默认只返回新稿预览;采用时加 `adopt=True`(自动快照旧稿,可回滚)。绝不直接覆盖正文。

**写评分离**(高质量项目):让另一个无历史的模型盲审 —— `export_review_prompt` 导出评分卡与正文 → 交给独立模型 → 输出 JSON 用 `submit_review` 写回。定稿门槛以盲审分为准(≥85),与作者自评分并存互不覆盖。

## 设定维护

- **Lorebook**:重要设定(信物、规则、人物细节)写入 `upsert_lore_entry`(关键词, 内容);生成时命中关键词自动注入上下文,适合"必须遵守"的硬设定
- **设定变更影响分析**:改设定前先 `search_impact`(关键词)反查受影响章节,评估回改范围
- **级联重生成**:章纲/设定改动后,受影响章节用 REST `POST /api/projects/{pid}/cascade`(MCP 无对应工具时引导用户在 Web 大纲页点「⚡ 级联重生成」)

## 质量纪律

- 门禁告警(尤其 warn 级)是硬约束:字数不足目标 80%、全文未出现关键角色、与近三章重复率超 12%、AI 味命中,都要处理后再进入下一章
- 批量连写(`POST /api/batch`)连续 2 章失败自动熔断;失败后先修问题再重试,不要无脑重跑
- 单章正文 1~3 分钟属正常,不要在生成中途重复发起请求
- 所有生成结果先 `read_chapter` 检查再定稿;版本历史永远可回滚,大胆试错

## 输出约定

- 长文(正文全文)不要整段贴回给用户;给章节定位 + 字数 + 门禁/评分摘要即可,用户要读时再展示
- 每完成一章主动汇报:章号、字数、门禁结果、审校分、伏笔/情节线变化
- 导出后返回文件绝对路径
