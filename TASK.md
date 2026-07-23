# TASK.md — Traceable Research Agent 改造任务书

> 版本：v1.0　创建日期：2026-07-23
> 依据：`TraceableResearchAgent项目改造方案v2.md` + Claude Code 补充建议
> 状态：进行中

---

## 目录

1. [项目目标](#一项目目标)
2. [改造总体原则](#二改造总体原则)
3. [Phase 1：记忆模块确定性部分 + 配置快照](#三phase-1记忆模块确定性部分--配置快照)
4. [Phase 2：全文抓取管道 + 子查询扇出](#四phase-2全文抓取管道--子查询扇出)
5. [Phase 3：报告质量与 RAG 工程](#五phase-3报告质量与-rag-工程)
6. [Phase 4：用户画像提取 + 记忆面板](#六phase-4用户画像提取--记忆面板)
7. [Phase 5：迭代深化 + 行内引用 + LLM 蒸馏 + 冲突仪表板](#七phase-5迭代深化--行内引用--llm-蒸馏--冲突仪表板)
8. [Phase 6：工程增强 + 演示脚本](#八phase-6工程增强--演示脚本)
9. [数据模型变更汇总](#九数据模型变更汇总)
10. [风险登记册](#十风险登记册)
11. [Phase 验收总览](#十一phase-验收总览)
12. [任务执行记录](#十二任务执行记录)

---

## 一、项目目标

将 Traceable Research Agent 从「审计骨架强、调研深度弱」升级为：

> **一个调研深度对标 GPT Researcher、且每一条结论和每一条用户记忆都可审计的开源深度研究方案。**

核心策略：把 GPT Researcher 的调研深度嫁接到本项目的审计骨架上，不做重构。

---

## 二、改造总体原则

1. **保留并放大审计骨架**：trace → evidence → provenance → conflict 四层不动，所有新能力必须接入 trace 体系。
2. **离线优先**：默认确定性可离线运行，LLM/外部服务为显式开启的增强项。
3. **增量演进**：每张表走 Alembic 迁移，模块对齐现有目录风格，不做破坏性重构。
4. **限制显性化**：凡是已知能力边界（抓取成功率、冷启动、离线降级），写进报告和 trace——限制本身就是审计能力的一部分。
5. **每次提交前做复合验证**：语法检查 → 单元测试 → 相关 smoke 检查 → 确认无敏感文件 → 提交。

---

## 三、Phase 1：记忆模块确定性部分 + 配置快照

> 预估：2-3 天　状态：待开始

### 3.1 数据模型（Alembic 0004_memory_schema）

新增三张表：

```
ConversationSession        # 对话窗口
  session_id (PK)
  tenant_id, user_id
  title                    # 首轮任务自动摘要生成
  created_at, updated_at

ChatTurn                   # 会话内一轮交互
  turn_id (PK)
  session_id (FK)
  role                     # user / agent
  content                  # 用户输入 or 报告最终回答摘要
  run_id (FK → agent_runs, 可空)   # ★ 挂到现有 run/trace 体系
  created_at

UserMemory                 # 跨会话用户记忆
  memory_id (PK)
  tenant_id, user_id
  kind                     # profile / preference / fact / interest
  extraction_method        # rule / llm / manual（标注提取方式比 kind 更有审计价值）
  content                  # 例："用户偏好中文 Markdown 报告，关注 Agent/RAG 方向"
  confidence               # 0-1；rule 提取给低分，用户确认后置 1.0
  status                   # pending / active / superseded / expired
  source_session_id (FK)   # 从哪个会话蒸馏来的
  source_run_id (FK)       # ★ 从哪次 run 蒸馏来的 → 接入 provenance 叙事
  valid_until              # 时效性事实的过期时间
  created_at, updated_at
```

同时 `agent_runs` 表增加两列：
- `session_id`（可空）——把一个会话下的多次调研串起来
- `run_config_snapshot`（JSON TEXT）——run 创建时自动写入 `settings.get_safe_runtime_config_summary()` 输出，把可审计从"过程可审计"扩展到"配置可审计"

### 3.2 模块结构

```
app/memory/
  models.py        # 上述三张表
  store.py         # CRUD + 按 (tenant, user) 过滤，对齐 trace/store.py 风格
  extractor.py     # 记忆蒸馏：规则提取 + 可选 LLM 提取（默认关）
  retriever.py     # 记忆召回：关键词 + 可选向量（复用 app/rag 的 embedding 后端）
  policy.py        # 注入预算、冲突处理、过期清理、冷启动默认行为
```

### 3.3 读写路径

**读路径（任务创建时）：**
1. `POST /api/tasks` 支持 `session_id` 参数
2. memory retriever 召回：本会话最近 N 轮摘要 + 该用户 active 状态的画像/偏好
3. 注入 planner 上下文
4. 召回动作写入 trace：新增 `memory_recall` 事件类型，记录 memory_id 列表和注入字符数

**写路径（run 完成时）：**
1. run 状态变 completed → 生成 ChatTurn
2. 触发 extractor：规则层先提取稳定偏好，LLM 层（可选）蒸馏画像
3. 新记忆以 `pending` 状态落库，等待用户确认

### 3.4 冷启动行为（显性化）

- 新用户无历史 run 时，记忆召回返回空是正常行为，`memory_recall` trace 记录 `recalled=0, reason="cold_start"`
- planner 和 Reporter 不得因记忆为空产生任何兜底文案或幻觉
- Streamlit 记忆面板显示「完成 3 次调研后，系统将开始为您总结偏好」进度提示
- 规则提取器设最小样本门槛：同一偏好信号出现 ≥2 次才生成 pending 记忆

### 3.5 新增 API

```
POST   /api/sessions                 # 创建会话
GET    /api/sessions                 # 当前用户的会话列表
GET    /api/sessions/{id}/turns      # 会话内对话与关联 run
GET    /api/memory                   # 当前用户的记忆列表（含 pending）
POST   /api/memory/{id}/confirm      # 确认 / 拒绝待确认记忆
DELETE /api/memory/{id}              # 删除单条记忆（写 trace）
DELETE /api/memory                   # 清空画像（写 trace）
```

### 3.6 `memory_search` 工具注册

把记忆召回注册为 Tool Registry 的只读工具 `memory_search`。planner 可以把"查历史"作为计划步骤，记忆调用天然进入 trace/审计/HITL 体系。

### 3.7 验收标准

- [ ] 三张表迁移成功，Alembic 升级到 0004
- [ ] `agent_runs` 含 `session_id` 和 `run_config_snapshot` 列
- [ ] 同一对话框内问"对比上次调研的 X 和这次的 Y"，agent 自动关联历史 run
- [ ] 新用户空召回有 `cold_start` trace 记录
- [ ] `memory_search` 工具出现在 `GET /api/tools` 列表中
- [ ] Streamlit 会话切换器可用
- [ ] 离线可测（不依赖 LLM）
- [ ] 语法检查 + 单元测试 + 相关 smoke 全部通过

---

## 四、Phase 2：全文抓取管道 + 子查询扇出

> 预估：3-4 天　状态：待开始

### 4.1 全文抓取管道（discover → fetch → compress）

**改造内容：**

1. 两阶段来源管道：Tavily/Exa 发现 URL → 自动将 URL 列表喂给抓取工具读正文 → 压缩后进入证据聚合
2. planner 的 deep_research 场景模板改为固定三步：`discover → fetch → compress`
3. 步骤间数据传递：plan schema 支持 `arguments_from: {step_no: 1, field: "urls"}` 引用语法，executor 执行时解析
4. 抓取全文快照存入 `SourceSnapshot`（gzip + SHA-256 不可变 artifact），EvidencePassage 记录字符区间
5. 抓取后端降级链：Firecrawl MCP（有 Key）→ 本地 httpx + BeautifulSoup 正文提取（离线可用，新增 `web_fetcher` 内置工具）

**`content_basis` 标记（Alembic 0006）：**

EvidencePassage 增加 `content_basis` 列，三值枚举：

| 值 | 含义 |
|---|---|
| `full_text` | 基于完整抓取的正文 |
| `partial` | 抓取成功但内容被截断（超 max_chars 限制） |
| `snippet_only` | 仅基于搜索摘要（未抓取或抓取失败） |

报告"证据与限制"章节自动区分呈现；抓取失败 URL 及原因进结构化 trace。

**涉及模块：** `app/agent/planner.py`、`app/agent/executor.py`、新增 `app/tools/web_fetcher.py`、`app/evidence/service.py`、`app/evidence/models.py`

### 4.2 子查询扇出

**改造内容：**

1. `parallel_executor` 真正消费 `sub_queries`：每个子查询独立走检索管道，并发执行
2. 区分步骤依赖关系：无依赖步骤（不同子查询间）可并发，有依赖步骤（同一子查询内的 discover→fetch→compress）串行
3. 新增 run 级 `visited_urls` 全局去重（`threading.Lock + set`），跨子查询避免重复抓取
4. ToolTrace 增加 `sub_query` 列（Alembic 0005），trace 按子查询分组展示
5. 无 LLM 降级：用规则按「、」「和」「以及」等连词拆子主题

**并发安全：**
- `trace/store.py` 加进程内写锁（`threading.Lock` 包裹写事务），解决 SQLite 并发写问题
- 补两个 pytest 用例：N 线程并发写 trace 不报错、并发抓取 URL 去重正确

**涉及模块：** `app/agent/parallel_executor.py`、`app/agent/query_decomposer.py`、`app/trace/models.py`、`app/trace/store.py`、`tests/`

### 4.3 验收标准

- [ ] 深度调研任务自动完成 discover→fetch→compress 三步
- [ ] `content_basis` 标记正确落库（full_text / partial / snippet_only）
- [ ] 报告区分"基于全文/仅摘要"结论
- [ ] trace 按子查询分组展示
- [ ] 并发写 trace 不报错（pytest 验证）
- [ ] URL 去重正确（pytest 验证）
- [ ] 抓取失败 URL 及原因进结构化 trace
- [ ] 离线模式可用（BeautifulSoup 降级链）
- [ ] 语法检查 + 单元测试 + 相关 smoke 全部通过

---

## 五、Phase 3：报告质量与 RAG 工程

> 预估：2-3 天　状态：待开始

### 5.1 deterministic 报告重构

1. deterministic 模式按子查询/主题分组组织证据，模板生成「每个子问题的发现 + 支撑引用编号」结构
2. 素材使用 claim 证据图中的 ResearchClaim + Citation，替代工具输出流水账
3. 结合 Phase 2 的 `content_basis` 标记，每个发现后标注（全文 / 仅摘要 / 部分截断）
4. LLM 模式与 deterministic 模式共用同一套「按主题分组」中间结构

**涉及模块：** `app/agent/reporter.py`、`app/agent/report_generation.py`

### 5.2 递归切分 v2

1. 递归切分：优先按 `\n\n`（段落）→ 句号/换行 → 最后字符硬切
2. markdown heading 感知：heading 路径写入 chunk metadata
3. 保留旧切分器为 `chunker_version=v1`，新索引用 `v2`，`build_rag_index.py` 支持版本参数

**五类边界处理策略（记录进 `docs/rag_chunk_experiment.md`）：**

| 边界情况 | 处理策略 |
|---|---|
| 代码块（``` 围栏） | 视为不可切单元：整个代码块进一个 chunk，超限时整体保留首部并在 metadata 标记 `truncated_code=true` |
| 表格 | 按行组切分，每个 chunk 重复表头行；metadata 记录 `table_header` |
| 列表 | 按列表项边界切分，不切断单个列表项；嵌套列表按顶级项切 |
| 超长无换行文本 | 兜底字符硬切（等价 v1 行为），metadata 标记 `hard_cut=true` |
| 中英混排 | 切分窗口按字符数计，不按词数（与 v1 实验口径一致） |

**涉及模块：** `app/rag/chunker.py`、`app/rag/build_index.py`、`docs/rag_chunk_experiment.md`

### 5.3 Rerank 层

1. RRF 后加 cross-encoder 精排：有网络用 bge-reranker 类模型，离线用规则特征（词项重叠度 + 来源权威性分）
2. rerank 分数写入 chunk metadata 并进入 trace
3. 与现有「来源六维评分」组合成「文档级可靠性 + chunk 级相关性」双层排序

**涉及模块：** `app/rag/hybrid_search.py`、新增 `app/rag/reranker.py`

### 5.4 验收标准

- [ ] deterministic 报告按子主题分组，带引用编号和 content_basis 标记
- [ ] 递归切分 v2 可用，切分对比实验文档更新（含边界用例）
- [ ] rerank 层在离线/在线模式均可工作
- [ ] 语法检查 + 单元测试 + 相关 smoke 全部通过

---

## 六、Phase 4：用户画像提取 + 记忆面板

> 预估：2 天　状态：待开始

### 6.1 规则版用户画像提取

1. 规则层提取稳定偏好：语言（中文/英文）、报告格式（Markdown/Word/PDF）、反复出现的领域关键词
2. LLM 层（可选，默认关）蒸馏画像
3. 新记忆以 `pending` 状态落库，等待用户确认
4. 样本门槛：同一偏好信号出现 ≥2 次才生成 pending 记忆

### 6.2 pending/active 状态机

- `pending` → 用户确认 → `active`
- `pending` → 用户拒绝 → 删除
- `active` → 被新记忆取代 → `superseded`
- `active` → 超过 `valid_until` → `expired`

### 6.3 记忆治理

- **用户确认（HITL）**：Streamlit 侧栏提示"系统了解到您的偏好：xxx，确认保留？"，复用现有 confirmation 机制
- **冲突与更新**：新旧记忆矛盾时旧记忆置 `superseded` 而非删除，保留演进历史
- **时效性**：`valid_until` 到期自动转 `expired`，不再注入
- **遗忘权**：`DELETE /api/memory/{id}` + "清空我的画像"按钮，删除动作也写 trace
- **注入预算控制**：`memory/policy.py` 设置注入预算上限（≤800 字），超出按 recency + confidence 排序裁剪，裁剪动作写 trace

### 6.4 记忆面板（Streamlit）

- 记忆列表页：展示 active/pending/expired 记忆，每条显示来源 run、提取方式、置信度
- 确认交互：pending 记忆显示"确认/拒绝"按钮
- 冷启动状态：新用户显示进度提示

### 6.5 验收标准

- [ ] 多次调研后系统给出"您的偏好"待确认列表
- [ ] 单次行为不产生记忆（≥2 次门槛）
- [ ] pending → active → superseded 状态流转正确
- [ ] 删除操作写 trace
- [ ] 注入预算不超 800 字
- [ ] 记忆面板可交互
- [ ] 语法检查 + 单元测试 + 相关 smoke 全部通过

---

## 七、Phase 5：迭代深化 + 行内引用 + LLM 蒸馏 + 冲突仪表板

> 预估：3-4 天　状态：待开始

### 7.1 迭代深化（iterative deepening）

1. ReAct executor 之上加 deepening 循环：每轮结束 LLM 输出 `{learnings: [...], follow_up_queries: [...]}`
2. 配置 `DEEP_RESEARCH_MAX_DEPTH=2`、`DEEP_RESEARCH_BREADTH=3`，默认保守
3. 每轮 learnings 落库关联 run_id，报告"研究过程"章节展示探索路径
4. 上下文预算：扩展 `context_compressor.py`，超限按轮次从新到旧裁剪
5. 离线降级：无 LLM 时退化为单轮

**涉及模块：** `app/agent/react_executor.py`、新增 `app/agent/deepening.py`、`app/agent/context_compressor.py`

### 7.2 行内引用织入叙述正文（面试 demo 核心交互）

1. Reporter 生成正文时，每个事实性句子后挂行内引用标号 `[1][2]`
2. 标号 → Citation ID → claim 证据图的映射表随报告持久化
3. Streamlit 报告页支持点击标号弹出证据卡片：原文片段 + 来源 + 快照哈希 + content_basis + supports/refutes 判定 + 来源六维评分
4. 引用标号在 Markdown / Word / PDF 三种导出中都保留
5. demo 脚本中设计「点击一个 refutes 引用」的演示动线

**涉及模块：** `app/agent/reporter.py`、`app/agent/report_exporter.py`、`app/evidence/service.py`、`frontend/streamlit_app.py`

### 7.3 冲突仪表板

1. 报告页增加冲突仪表板区域：列出所有 `refutes` 和 `unresolved` 的 claim
2. 每条冲突展示双方证据卡片并排对比
3. 标注"已解决/未解决/待人工判断"状态
4. 叙事：「不是 LLM 说谁对，而是证据自己打架，我让你看到打架过程」

**涉及模块：** `frontend/streamlit_app.py`、`app/evidence/reasoning_service.py`

### 7.4 LLM 记忆蒸馏 + 向量召回

1. LLM 蒸馏：从会话 run 中提炼用户画像（默认关，显式开启）
2. 向量召回：复用 `app/rag` 的 embedding 后端，对 UserMemory 做语义检索
3. 规则召回 + 向量召回混合排序

### 7.5 验收标准

- [ ] deep 模式两轮探索不超时
- [ ] 报告正文 [1][2] 可点击弹出证据卡片（含 supports/refutes 与 content_basis）
- [ ] Word/PDF 导出保留引用
- [ ] 冲突仪表板正确展示 refutes/unresolved 的 claim
- [ ] 向量召回可用
- [ ] 语法检查 + 单元测试 + 相关 smoke 全部通过

---

## 八、Phase 6：工程增强 + 演示脚本

> 预估：2-3 天　状态：待开始

### 8.1 多报告类型

- Reporter 抽象 `report_type` 参数
- 优先实现 `detailed_report`（按子主题分节写作）+ `outline_report`（纯大纲，生成快，适合预览）
- 目录用现有 markdown 标题树自动生成

**涉及模块：** `app/agent/reporter.py`、`app/agent/report_generation.py`

### 8.2 LLM 成本追踪

- LLM client 统一返回 `usage`（prompt/completion tokens）
- 写入 ToolTrace 的 `token_in/token_out/estimated_cost`（字段已预留）
- AgentRun 汇总
- Streamlit 侧栏展示本次 run 成本

**涉及模块：** `app/llm/providers.py`、`app/trace/logger.py`、`frontend/streamlit_app.py`

### 8.3 学术检索器

- 新增 arXiv、Semantic Scholar 两个免费无 Key 的检索工具
- 复用 Tool Registry 接入，遵循只读安全规则
- 补齐"技术调研"场景

**涉及模块：** 新增 `app/tools/arxiv_search.py`、`app/tools/semantic_scholar.py`

### 8.4 Claim 校验 pass

- 报告生成前加校验 pass：把 refutes/unresolved 的 claim 自动降级为「待核实」写入报告限制章节
- 复用 `reasoning_service` 的 supports/refutes/unresolved 判定
- 比 GPT Researcher 的 LLM 互评更有依据

**涉及模块：** `app/agent/reporter.py`、`app/evidence/reasoning_service.py`

### 8.5 Demo 脚本

- 新增 `scripts/demo_deep_research.py`
- 输入：一个预设的研究问题（如"对比 LangGraph、CrewAI 和 AutoGen 的 Agent 编排能力"）
- 输出：完整 discover→fetch→compress→report 链路，每阶段有进度打印
- 用途：面试时一句命令跑完全程

### 8.6 验收标准

- [ ] detailed_report 分节输出；outline_report 可用
- [ ] 侧栏展示 run 成本（token/latency/cost）
- [ ] arXiv 和 Semantic Scholar 工具可正常搜索
- [ ] refutes 结论自动降级为「待核实」
- [ ] `demo_deep_research.py` 一句命令跑完全程
- [ ] 语法检查 + 单元测试 + 全部 smoke 通过

---

## 九、数据模型变更汇总

| 迁移 | 所属 Phase | 变更内容 |
|---|---|---|
| 0004_memory_schema | Phase 1 | 新增 ConversationSession、ChatTurn、UserMemory（含 `extraction_method` 列）；agent_runs 加 `session_id`、`run_config_snapshot` 列 |
| 0005_subquery_trace | Phase 2 | tool_traces 加 `sub_query` 列 |
| 0006_content_basis | Phase 2 | evidence_passages 加 `content_basis` 列（full_text / partial / snippet_only） |
| （无需迁移） | Phase 6 | token_in / token_out / estimated_cost 字段已存在，仅补填充逻辑 |

---

## 十、风险登记册

| # | 风险 | 影响 Phase | 严重程度 | 应对策略 | 状态 |
|---|---|---|---|---|---|
| 1 | BeautifulSoup 对 SPA/反爬页面抓取成功率仅 40-60% | Phase 2 | 中 | 标注为已知限制；EvidencePassage 增加 `content_basis` 标记；报告区分"基于全文"与"仅基于摘要"；失败原因进结构化 trace，转为审计亮点 | 已纳入方案 |
| 2 | 并发子查询写 SQLite 触发 `database is locked`；visited_urls 多 worker 竞争 | Phase 2 | 高 | `trace/store.py` 加进程内写锁；visited_urls 用 `threading.Lock + set`；补两个并发 pytest 用例 | 已纳入方案 |
| 3 | 递归切分在代码块/表格/列表处出边界 bug | Phase 3 | 中 | 五类边界情况处理策略写入文档；每类策略配测试用例 | 已纳入方案 |
| 4 | 新用户记忆冷启动行为未定义 | Phase 1/4 | 低 | 空召回为正常行为且记 trace；不产生兜底文案；UI 进度提示；规则提取设 ≥2 次样本门槛 | 已纳入方案 |
| 5 | 迭代深化在离线模式耗时长、上下文裁剪易出边界 bug | Phase 5 | 中 | 优先级固定在 Phase 5；MAX_DEPTH 默认 2 保守起步；裁剪逻辑重点写边界测试 | 已降级处理 |
| 6 | 子查询扇出 + 全文抓取后报告信息量暴增，用户难以快速定位关键结论 | Phase 5 | 中 | Reporter 生成"执行摘要"章节（≤500 字）放在报告最前；Streamlit 报告页增加侧边目录导航 | 已纳入方案 |
| 7 | 记忆系统积累后注入上下文挤占 Planner 有效 token 预算 | Phase 4 | 中 | `memory/policy.py` 设置注入预算上限（≤800 字），超出按 recency + confidence 排序裁剪；裁剪动作写 trace | 已纳入方案 |

---

## 十一、Phase 验收总览

| 阶段 | 内容 | 预估 | 核心验收项 |
|---|---|---|---|
| Phase 1 | 记忆模块 + 配置快照 | 2-3 天 | 三表迁移成功；session_id 关联；memory_search 工具；冷启动 trace |
| Phase 2 | 全文抓取 + 子查询扇出 | 3-4 天 | discover→fetch→compress 自动完成；content_basis 标记；并发安全 |
| Phase 3 | 报告重构 + 递归切分 + rerank | 2-3 天 | 报告按主题分组；切分 v2 可用；双层排序 |
| Phase 4 | 画像提取 + 记忆面板 | 2 天 | pending→active 状态机；记忆面板交互；注入预算控制 |
| Phase 5 | 迭代深化 + 行内引用 + 冲突仪表板 | 3-4 天 | 行内引用可点击弹窗；冲突并排对比；Word/PDF 保留引用 |
| Phase 6 | 工程增强 + demo 脚本 | 2-3 天 | detailed_report；成本追踪；学术检索器；demo 脚本一句跑通 |
| **合计** | | **14-19 天** | |

---

## 十二、任务执行记录

> 此章节在每 Phase 执行过程中持续更新，记录：
> - 日期/时间
> - 变更文件
> - 实现内容
> - 执行的命令与结果
> - 已知限制
> - Commit hash

### Phase 1 执行记录

（待开始）

### Phase 2 执行记录

（待开始）

### Phase 3 执行记录

（待开始）

### Phase 4 执行记录

（待开始）

### Phase 5 执行记录

（待开始）

### Phase 6 执行记录

（待开始）
