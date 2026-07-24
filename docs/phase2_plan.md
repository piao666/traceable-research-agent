# Phase 2 实施计划（最终版）

> 基于：TASK.md §4 + 完整代码审计 · 2026-07-24

---

## 代码审计关键发现

1. **httpx 已在 requirements.txt**（第2行），**beautifulsoup4 不在** — 需要添加
2. **query_decomposer.py 已存在** — `decompose_task()` + `decompose_and_annotate_plan()` 已实现 LLM 分解，缺少无 LLM 的规则降级
3. **parallel_executor.py 已存在** — `_plan_groups()` 按工具类型分组，需要改为消费 `sub_queries`
4. **evidence/service.py** — `_materialize_item()` 已产出 SourceSnapshot (gzip+SHA-256)，只需新增 `content_basis` 列和填充逻辑
5. **迁移模式** — 4 个 Alembic 版本使用 `revision`/`down_revision` 变量 + batch_alter_table
6. **测试用 unittest**（不是 pytest）— `tests/test_memory.py` 使用 `unittest.TestCase`

---

## 实施步骤（9步，严格顺序）

### Step 1: 依赖 + 数据迁移 (0005 + 0006)
- 添加 `beautifulsoup4` 到 requirements.txt
- 创建 `migrations/versions/0005_subquery_trace.py` — tool_traces 加 `sub_query` TEXT 列
- 创建 `migrations/versions/0006_content_basis.py` — evidence_passages 加 `content_basis` VARCHAR(32) NOT NULL DEFAULT 'snippet_only'
- 修改 `app/trace/models.py` — ToolTrace 加 `sub_query: Mapped[str | None]`
- 修改 `app/evidence/models.py` — EvidencePassage 加 `content_basis: Mapped[str]`
- 修改 `scripts/migrate_database.py` — 注册 0005/0006 版本

### Step 2: web_fetcher 内置工具
- 新增 `app/tools/web_fetcher.py` — httpx + BeautifulSoup 正文提取
- 安全边界：只允许 http/https、拒绝内网 IP、10s 超时、只读 User-Agent
- 降级链：httpx GET → HTML parse → `<article>` → `<main>` → `<body>` → clean_web_snippet
- 输出：`{pages: [{url, title, content, content_basis, error?}], fetched_count, failed_count}`
- 修改 `app/tools/defaults.py` — 注册 `web_fetcher` 工具

### Step 3: 规则拆解降级
- 修改 `app/agent/query_decomposer.py` — 新增 `decompose_task_by_rules()` 函数
- 按分隔符（`、`, `和`, `以及`, `;`, `, and`）拆子主题
- 当 LLM 不可用时调用，确保离线模式也能演示子查询扇出

### Step 4: planner deep_research 三步模板
- 修改 `app/agent/planner.py`
- deep_research 场景确定性模板改为：
  1. `tavily_search` (discover) — include_raw_content=false
  2. `web_fetcher` (fetch) — arguments_from 引用上一步 URL
  3. `report_writer` (report)
- 新增 `_step_template` 中的 web_fetcher 模板
- 保持 remote MCP 优先级（firecrawl.scrape > web_fetcher）

### Step 5: executor + parallel_executor 支持 arguments_from + sub_queries
- 修改 `app/agent/executor.py`:
  - 新增 `_resolve_arguments_from(step, observations)` — 从前面步骤的输出中提取字段
  - `EXECUTABLE_TOOLS` 加 `web_fetcher`
  - 步骤间数据传递：`arguments_from: {step_no: N, field: "results"}` → 提取 URL 列表
- 修改 `app/agent/parallel_executor.py`:
  - 消费 `plan["sub_queries"]`，不同子查询间并发，同子查询内串行
  - 新增 `visited_urls: set[str]` + `threading.Lock` 跨子查询去重

### Step 6: trace 写锁 + sub_query 传递
- 修改 `app/trace/store.py` — 加 `threading.Lock` 包裹写事务
- 修改 `app/trace/logger.py` — `record_tool_result()` 和 `record_trace_event()` 支持 `sub_query` 参数

### Step 7: evidence content_basis 填充
- 修改 `app/evidence/service.py` — `_materialize_item()` 中根据 tool_name 和 metadata 推断 content_basis：
  - web_fetcher → 从 output 中读取逐页 content_basis
  - tavily_search (无 fetch) → snippet_only
  - file_reader / sql_query → full_text
  - rag_search → snippet_only

### Step 8: API + 前端
- 修改 `app/schemas.py` — ToolTraceResponse 加 `sub_query: str | None`
- 修改 `app/api/tasks.py` — `_tool_trace_response` 传递 sub_query
- 修改 `frontend/streamlit_app.py` — trace 页按 sub_query 分组展示（expander 折叠）

### Step 9: 测试 + 验证
- 新增 `tests/test_phase2.py`:
  - WebFetcherTests: 正常抓取 / 超时 / 内网拒绝 / content_basis 标记
  - ArgumentsFromTests: 从 Tavily output 提取 URL
  - SubQueryDecompositionTests: 规则拆解中英文
  - ConcurrentWriteTests: N 线程并发写 trace 不报错 / URL 去重正确
- 运行全部验证：`compileall` → `migrate` → `unittest` → `smoke_e2e`

---

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| web_fetcher 为内置工具 | httpx + BeautifulSoup | TASK 要求离线降级链，不依赖外部服务 |
| arguments_from 解析位置 | executor 层，执行前解析 | 与工具解耦 |
| 并发写锁粒度 | 进程内 threading.Lock | SQLite WAL + 30s busy_timeout 已足够 |
| content_basis 判定位置 | evidence materialize 时 | 此时有完整 observation + trace 信息 |
| 规则拆解 | 降级方案，非主路径 | LLM 拆解质量更高，规则仅保证离线可用 |
