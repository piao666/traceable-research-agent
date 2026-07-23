# TASK.md — Traceable Research Agent 改造任务书

> 版本：v2.0　更新日期：2026-07-23
> 依据：`TraceableResearchAgent项目改造方案v2.md` + Claude Code 补充建议 + 用户决策（RAG 降级 + Skills 系统）
> 状态：进行中（Phase 1 ✅，Phase 2-6 待开始）

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

> 预估：2-3 天　状态：✅ 已完成

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

- [x] 三张表迁移成功，Alembic 升级到 0004
- [x] `agent_runs` 含 `session_id` 和 `run_config_snapshot` 列
- [x] 同一对话框内问"对比上次调研的 X 和这次的 Y"，agent 自动关联历史 run
- [x] 新用户空召回有 `cold_start` trace 记录
- [x] `memory_search` 工具出现在 `GET /api/tools` 列表中
- [x] Streamlit 会话切换器可用
- [x] 离线可测（不依赖 LLM）
- [x] 语法检查 + 单元测试 + 相关 smoke 全部通过

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

## 五、Phase 3：报告重构 + Skills 系统

> 预估：2-3 天　状态：待开始
>
> **v2.0 修订说明**：原计划中的递归切分 v2（4.2）和 Rerank 层（4.3）已移除。理由：RAG 是强用户定制场景——不同用户的语料、切分需求、检索策略完全不同，继续打磨内置 RAG 组件是低杠杆投入。`app/rag/` 全部代码保留不动（离线演示依赖它，记忆模块依赖它的 embedding 后端），但不再做深度优化。空出的工作量投入 Skills 系统——把 planner 中 hardcode 的场景模板和步骤模板抽成可注册、可发现、用户可自定义的数据文件。

### 5.1 deterministic 报告重构

1. deterministic 模式按子查询/主题分组组织证据，模板生成「每个子问题的发现 + 支撑引用编号」结构
2. 素材使用 claim 证据图中的 ResearchClaim + Citation，替代工具输出流水账
3. 结合 Phase 2 的 `content_basis` 标记，每个发现后标注（全文 / 仅摘要 / 部分截断）
4. LLM 模式与 deterministic 模式共用同一套「按主题分组」中间结构

**涉及模块：** `app/agent/reporter.py`、`app/agent/report_generation.py`

### 5.2 Skill 文件格式与加载器

Skill 是 JSON 文件，放在 `workspace/skills/` 目录下，用户可自行添加/修改，不需要改 Python 代码。

**Skill JSON Schema：**

```json
{
  "name": "deep_web_research",
  "version": "1.0",
  "description": "深度网页调研：搜索发现 URL → 正文抓取 → 证据压缩 → 报告",
  "required_tools": ["tavily_search", "web_fetcher", "report_writer"],
  "parameters": {
    "query": {"type": "string", "required": true},
    "max_urls": {"type": "integer", "default": 5}
  },
  "steps": [
    {
      "tool_name": "tavily_search",
      "goal": "发现与查询相关的网页 URL 列表",
      "arguments": {
        "query": "{{parameters.query}}",
        "max_results": "{{parameters.max_urls}}",
        "include_raw_content": false
      }
    },
    {
      "tool_name": "web_fetcher",
      "goal": "抓取上一步发现的所有 URL 正文",
      "arguments": {
        "urls": "{{steps[0].output.urls}}"
      }
    },
    {
      "tool_name": "report_writer",
      "goal": "从抓取正文中提取证据并生成报告",
      "arguments": {}
    }
  ]
}
```

**关键设计点：**

| 特性 | 机制 | 说明 |
|------|------|------|
| 参数化 | `{{parameters.query}}` | 引用用户创建任务时传入的参数 |
| 步骤间数据传递 | `{{steps[0].output.urls}}` | 复用 Phase 2 的 `arguments_from` 机制 |
| 可组合 | `{{skill.other_skill.steps[0].output}}` | Skill 步骤可引用另一个 Skill 的输出 |
| 文件即代码 | `workspace/skills/*.json` | 用户不改 Python 代码即可定制 |

**涉及模块：** 新增 `app/skills/loader.py`、`workspace/skills/` 目录

### 5.3 Skill Registry + API

1. 启动时扫描 `workspace/skills/*.json`，校验 schema，注册到内存
2. `GET /api/skills` 返回所有已安装 Skill 的元数据（name, version, description, required_tools, parameters）
3. `GET /api/skills/{name}` 返回完整 Skill 定义
4. Skill 校验：required_tools 必须已注册在 Tool Registry 中；参数引用必须解析成功

**涉及模块：** 新增 `app/skills/registry.py`、`app/api/skills.py`、修改 `app/main.py`

### 5.4 Planner 集成

1. `plan_task()` 接受 `skill_name` 参数（优先级高于 keyword 匹配）
2. 若传入 `skill_name`，加载 Skill 模板，用用户参数填充 `{{parameters.*}}` 占位符，直接生成步骤
3. executor 执行时解析 `{{steps[N].output.field}}` 引用（复用 Phase 2 的 `arguments_from` 解析器）
4. 不传 `skill_name` 时保持现有 keyword-matching 行为不变——向后兼容

**涉及模块：** `app/agent/planner.py`、`app/agent/executor.py`、`app/schemas.py`（TaskCreateRequest 加 `skill_name`）

### 5.5 预置 Skill 文件

| Skill 文件 | 说明 | 步骤 |
|------------|------|------|
| `deep_web_research.json` | 深度网页调研 | tavily_search → web_fetcher → report_writer |
| `technical_docs_research.json` | 技术文档调研 | mcp_github_search → rag_search → web_fetcher → report_writer |
| `local_audit.json` | 本地资料复盘 | file_reader → sql_query → rag_search → report_writer |
| `quick_search.json` | 快速搜索（无抓取） | tavily_search → report_writer |

### 5.6 Streamlit 集成

1. 场景模板下拉改为从 `GET /api/skills` 动态加载
2. 选中 Skill 后展示其描述、所需工具、参数表单
3. 创建任务时传 `skill_name` 替代 `scenario_template_key`

**涉及模块：** `frontend/streamlit_app.py`

### 5.7 验收标准

- [ ] deterministic 报告按子主题分组，带引用编号和 content_basis 标记
- [ ] `workspace/skills/` 下 4 个预置 Skill 文件可被 loader 正确解析
- [ ] `GET /api/skills` 返回 Skill 列表，`GET /api/skills/{name}` 返回完整定义
- [ ] `POST /api/tasks` 传 `skill_name` 时 planner 用 Skill 模板生成步骤
- [ ] `{{parameters.*}}` 占位符被正确填充
- [ ] `{{steps[N].output.field}}` 引用在 executor 中被正确解析
- [ ] Streamlit 场景模板从 `/api/skills` 动态加载
- [ ] 不传 `skill_name` 时保持现有 behavior（向后兼容）
- [ ] `app/rag/` 全部代码保留不动
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
| 3 | 新用户记忆冷启动行为未定义 | Phase 1/4 | 低 | 空召回为正常行为且记 trace；不产生兜底文案；UI 进度提示；规则提取设 ≥2 次样本门槛 | 已纳入方案 |
| 4 | 迭代深化在离线模式耗时长、上下文裁剪易出边界 bug | Phase 5 | 中 | 优先级固定在 Phase 5；MAX_DEPTH 默认 2 保守起步；裁剪逻辑重点写边界测试 | 已降级处理 |
| 5 | 子查询扇出 + 全文抓取后报告信息量暴增，用户难以快速定位关键结论 | Phase 5 | 中 | Reporter 生成"执行摘要"章节（≤500 字）放在报告最前；Streamlit 报告页增加侧边目录导航 | 已纳入方案 |
| 6 | 记忆系统积累后注入上下文挤占 Planner 有效 token 预算 | Phase 4 | 中 | `memory/policy.py` 设置注入预算上限（≤800 字），超出按 recency + confidence 排序裁剪；裁剪动作写 trace | 已纳入方案 |
| 7 | Skill 文件被用户误改导致 schema 校验失败 | Phase 3 | 中 | loader 校验时返回结构化错误（文件名 + 原因）；校验失败不影响其他 Skill 加载；`GET /api/skills` 返回每个 Skill 的 `status: valid\|invalid` | 已纳入方案 |

---

## 十一、Phase 验收总览

| 阶段 | 内容 | 预估 | 核心验收项 |
|---|---|---|---|
| Phase 1 | 记忆模块 + 配置快照 | ✅ 已完成 | 三表迁移成功；session_id 关联；memory_search 工具；冷启动 trace |
| Phase 2 | 全文抓取 + 子查询扇出 | 3-4 天 | discover→fetch→compress 自动完成；content_basis 标记；并发安全 |
| Phase 3 | 报告重构 + Skills 系统 | 2-3 天 | 报告按主题分组；4 个预置 Skill；`/api/skills` 端点；planner 集成；向后兼容 |
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

- **日期**：2026-07-23
- **变更文件**（18 个）：
  - 新增：`migrations/versions/0004_memory_schema.py`、`app/memory/__init__.py`、`app/memory/models.py`、`app/memory/store.py`、`app/memory/policy.py`、`app/api/sessions.py`、`app/api/memory.py`、`tests/test_memory.py`、`docs/phase1_plan.md`
  - 修改：`app/trace/models.py`、`app/trace/store.py`、`app/database.py`、`app/schemas.py`、`app/api/tasks.py`、`app/main.py`、`app/tools/defaults.py`、`scripts/migrate_database.py`、`frontend/streamlit_app.py`
- **实现内容**：
  - Alembic 0004 迁移：conversation_sessions、chat_turns、user_memories 三张新表
  - agent_runs 表新增 session_id（可空）和 run_config_snapshot（JSON TEXT）列
  - app/memory 模块：models（3 个 ORM 模型）、store（17 个 CRUD 函数）、policy（冷启动、注入预算 ≤800 字、recency+confidence 排序）
  - /api/sessions CRUD 端点（create/list/get/turns）
  - /api/memory CRUD + confirm/delete 端点
  - 创建任务时自动写入 run_config_snapshot（settings.get_safe_runtime_config_summary()）
  - memory_search 工具注册到 Tool Registry（handler 推迟到 Phase 4）
  - Streamlit 侧边栏：会话切换器 + 记忆面板（加载/确认/拒绝）
  - 27 个新单元测试
- **命令与结果**：
  - `python scripts/migrate_database.py` → 0001→0004 连续迁移成功
  - `python -m compileall -q app scripts frontend` → 无语法错误
  - `python -m unittest discover -s tests -v` → 75/75 通过（原 48 + 新增 27）
  - `python scripts/smoke_e2e.py` → e2e: ok, run completed, 3 traces, report generated
  - `curl /api/sessions` → 会话 CRUD 正常
  - `curl /api/memory` → 记忆列表正常（cold_start: total=0）
  - `curl /api/tools` → memory_search 出现在工具列表中（共 7 个工具）
- **已知限制**：
  - memory_search 工具无 handler（Phase 4 实现向量召回）
  - 记忆提取器（extractor.py）和向量检索器（retriever.py）在 Phase 4 实现
  - 样本门槛（MIN_SAMPLE_THRESHOLD=2）常量已定义，逻辑在 Phase 4 生效
- **Commit**：`bba4a4b` → pushed to `origin/feature/improvements`

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
