# CastNovel API 速查(MCP 工具 + REST 端点)

## MCP 工具(mcp_server.py,stdio,共 22 个)

### 作品
| 工具 | 参数 | 说明 |
|---|---|---|
| `list_projects` | — | 列出作品(id/书名/题材/梗概),第一步通常调它 |
| `create_project` | title, genre?, premise?, style? | 新建作品 |
| `get_project_overview` | project_id, worldview_chars?=800 | 全貌:设定/角色/章节目录/伏笔/情节线 |
| `generate_worldview` | project_id | 生成世界观(强模型,30~90s),覆盖旧值 |
| `generate_characters` | project_id | 按世界观生成角色(覆盖) |
| `generate_outline` | project_id, count=30, replace?=False | 全书章纲;有正文时拒绝覆盖 |

### 章节
| 工具 | 参数 | 说明 |
|---|---|---|
| `generate_beats` | project_id, chapter_idx | 场景级章纲(节拍) |
| `generate_draft` | project_id, chapter_idx | 正文 + 门禁;返回预览,全文用 read_chapter |
| `generate_draft_best` | project_id, chapter_idx, n=2 | 2~4 份候选 → 门禁打分 → 主编重排择优;落选稿进版本历史 |
| `polish_chapter` | project_id, chapter_idx, adopt?=False | 去 AI 味润色;adopt=True 才落盘(自动快照) |
| `deai_rewrite_chapter` | project_id, chapter_idx, adopt?=False | 两步去 AI 味:span 定位(零 token)→ 按定位改写 |
| `review_chapter` | project_id, chapter_idx | 审校:总分/四维/问题清单 |
| `finalize_chapter` | project_id, chapter_idx | 定稿:摘要 + 状态/伏笔/情节线回写 |
| `read_chapter` | project_id, chapter_idx, with_content?=True | 单章详情含正文全文 |
| `update_chapter` | project_id, chapter_idx, title?/beats?/summary?/content? | 手工编辑(改正文自动快照) |
| `export_novel` | project_id, fmt=md/txt/docx/epub | 导出,返回文件绝对路径 |

### 独立盲审(写评分离)
| 工具 | 说明 |
|---|---|
| `export_review_prompt` | 导出评分卡 + 前情 + 正文全文;交给任意无历史模型盲审 |
| `submit_review` | review_json(overall/dimensions/issues/suggestions)+ reviewer 标识写回,与自评并存;定稿门槛盲审 ≥85 |

### 设定维护
| 工具 | 说明 |
|---|---|
| `list_lore_entries` / `upsert_lore_entry` / `delete_lore_entry` | Lorebook:关键词命中即注入上下文 |
| `search_impact` | 设定变更影响分析:按关键词反查受影响章节(零 token) |

## REST 端点(服务 `python main.py [端口]`,前缀 `/api`)

生成类 POST 均为 **SSE 流**:`data: {"type":"delta","text":…}`(过程)/ `{"type":"done","result":…}` / `{"type":"error","message":…}`;批量/级联为 `{"type":"progress","data":{event,…}}` 事件流。

| 端点 | 说明 |
|---|---|
| `GET/POST /projects`,`GET/PUT/DELETE /projects/{pid}` | 作品 CRUD;PUT 字段:title/genre/premise/style/worldview/style_ref |
| `POST /projects/{pid}/generate/worldview|characters|outline` | 设定生成(SSE;outline body {count, replace}) |
| `POST /projects/{pid}/chapters/{cid}/beats|draft|polish|review|revise|finalize|deai` | 单章流水线(SSE) |
| `POST /projects/{pid}/chapters/{cid}/draft/best` | 候选拉样重排,body {n: 2~4}(SSE,result.rank 含 best/scores/reason) |
| `POST /projects/{pid}/chapters/{cid}/adopt` | 采用新稿,body {content, note}(自动快照) |
| `GET/PUT /projects/{pid}/chapters/{cid}` / `GET /chapters/{cid}/context` | 读取/编辑;context 返回将注入的完整上下文 |
| `GET/POST /projects/{pid}/chapters/{cid}/versions[/{vid}/restore]` | 版本历史与回滚 |
| `POST /projects/{pid}/batch` | 批量连写 body {count, review_each}(SSE 事件流,熔断) |
| `POST /projects/{pid}/cascade` | 级联重生成 body {chapter_ids:[], include_written}(SSE 事件流) |
| `GET /projects/{pid}/impact?query=关键词` | 影响反查(确定性,零 token) |
| `GET/POST /projects/{pid}/lore`,`PUT/DELETE /projects/{pid}/lore/{lid}` | Lorebook CRUD |
| `PUT /projects/{pid}/plotlines/{plid}` / `PUT .../foreshadowing/{fid}` | 情节线/伏笔手工修正 |
| `GET /projects/{pid}/export?format=epub` | 导出下载 |
| `GET /settings`,`PUT /settings`,`POST /llm/test`,`POST /llm/models`,`GET /providers` | 模型配置:档案(强/快/Embedding)、连接测试、拉模型列表、14 家供应商模板 |

## 配置要点

- **模型档案**:OpenAI 兼容协议;`base_url` + `api_key` + `model`。Claude/Gemini 官方兼容端点、Ollama/LM Studio 本地服务均可;`mock://local` 为离线演示
- **强/快模型分工**:强模型跑大纲/正文/审校,快模型跑摘要/状态抽取,单章成本约降 40%
- **并发安全**:MCP Server 与 Web 服务共享同一 SQLite(WAL),可同时使用,无需先启动 Web
- **生成耗时**:正文 1~3 分钟/章;SSE 断流后服务端照常落库,重查即可
