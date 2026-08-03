# Phase 6 实施计划 — 工程增强 + 演示脚本

> 日期：2026-08-03　状态：规划中

---

## 一、阶段目标

1. **多报告类型**：`detailed_report`（按子主题分节）+ `outline_report`（纯大纲，快速预览）
2. **LLM 成本追踪**：LLMResponse 补 `usage` 字段 → ToolTrace token_in/out/cost → AgentRun 汇总 → Streamlit 展示
3. **学术检索器**：arXiv + Semantic Scholar 免费检索工具，接入 Tool Registry
4. **Claim 校验 pass**：报告生成前将 refutes/unresolved claim 自动降级为「待核实」
5. **Demo 脚本**：`scripts/demo_deep_research.py` 一句命令跑完全程

---

## 二、分项设计

### 2.1 多报告类型（reporter.py）

**涉及文件：**
- `app/agent/reporter.py` — `generate_markdown_report()` 接受 `report_type` 参数
- `app/agent/executor.py` — `run_plan()` 传递 `report_type`
- `app/agent/parallel_executor.py` — 同样传递 `report_type`

**设计：**

```
generate_markdown_report(run, plan, observations, traces, llm_client, provenance_bundle, report_type="summary")
```

三种模式：
| report_type | 行为 |
|---|---|
| `summary`（默认） | 现有行为，不变 |
| `detailed_report` | 现有行为 + 自动生成目录（TOC）插入报告头部 |
| `outline_report` | 仅生成 §1 任务说明 + §2 运行摘要 + 目录 + 各节标题（不展开证据详情） |

**TOC 生成：** 扫描生成的 Markdown 中的 `##` 标题，生成编号列表插入 `§2 运行摘要` 之后。

**report_type 传递链：**
- `POST /api/tasks` 已有 `report_type` 字段 → `AgentRun.report_type` → `run_plan()` 读取 `run.report_type` 传给 `generate_markdown_report()`

### 2.2 LLM 成本追踪

**涉及文件：**
- `app/llm/base.py` — `LLMResponse` 增加 `usage` 字段
- `app/llm/providers.py` — `OpenAICompatibleLLMClient.complete()` 解析 response 中的 `usage`
- `app/trace/logger.py` — `record_tool_result()` 接受 token 参数写入 trace
- `app/trace/store.py` — 新增 `update_agent_run_cost()` 汇总函数
- `app/agent/executor.py` — `run_plan()` 完成后汇总 token/cost
- `app/agent/react_executor.py` — ReAct 模式同样记录 LLM token
- `frontend/streamlit_app.py` — 侧栏展示 run 成本

**设计：**

1. `LLMResponse` 新增：
```python
class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class LLMResponse(BaseModel):
    # ... existing fields ...
    usage: LLMUsage | None = None
```

2. `OpenAICompatibleLLMClient.complete()` 解析：
```python
usage_raw = response_payload.get("usage", {})
usage = LLMUsage(
    prompt_tokens=usage_raw.get("prompt_tokens", 0),
    completion_tokens=usage_raw.get("completion_tokens", 0),
    total_tokens=usage_raw.get("total_tokens", 0),
)
```

3. 成本计算（`app/llm/cost.py` 新增）：
```python
# 定价参考（RMB/1M tokens）
PRICING = {
    "deepseek-chat": {"prompt": 1.0, "completion": 2.0},
    "qwen-plus": {"prompt": 2.0, "completion": 6.0},
    "qwen-turbo": {"prompt": 0.3, "completion": 0.6},
}
```

4. `record_tool_result()` 新增可选参数 `token_in`, `token_out`, `estimated_cost`

5. `run_plan()` 执行完成后：
   - 查询该 run 的所有 trace，汇总 `token_in/token_out/estimated_cost`
   - 调用 `store.update_agent_run_cost(db, run_id, ...)` 更新 `AgentRun`

6. Streamlit：
   - `_render_cost_summary()` 展示：总 token、总成本、按步骤明细

**重要约束：** 成本追踪仅在 LLM 实际可用时有效。Deterministic 模式（离线）下 token 字段保持 0。

### 2.3 学术检索器

**涉及文件：**
- 新增 `app/tools/arxiv_search.py` — arXiv API 检索
- 新增 `app/tools/semantic_scholar.py` — Semantic Scholar API 检索
- `app/tools/defaults.py` — 注册两个新工具
- `app/config.py` — 新增配置项（超时、默认结果数）
- `.env.example` — 新增配置变量

**arXiv API：**
- 免费，无需 Key
- Endpoint: `http://export.arxiv.org/api/query?search_query={query}&start=0&max_results={n}`
- 返回 Atom XML，需解析
- 速率限制：1 req/3s（礼貌策略）
- 安全：只读 HTTP GET，无认证

**Semantic Scholar API：**
- 免费，无需 Key（可选 Key 提升速率）
- Endpoint: `https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit={n}`
- 返回 JSON
- 速率限制：100 req/5min（无 Key），100 req/s（有 Key）
- 安全：只读 HTTP GET

**工具注册：**
```python
register_tool(ToolSpec(
    name="arxiv_search",
    description="Search academic papers on arXiv. Free, no API key required.",
    input_schema={"query": "string", "max_results": "integer"},
    output_schema={"papers": "array", "total_results": "integer"},
    risk_level=RiskLevel.LOW,
    tags=["academic", "arxiv", "read-only"],
), handler=arxiv_search_handler)

register_tool(ToolSpec(
    name="semantic_scholar_search",
    description="Search academic papers via Semantic Scholar API. Free, optional API key.",
    input_schema={"query": "string", "limit": "integer", "fields": "string"},
    output_schema={"papers": "array", "total": "integer"},
    risk_level=RiskLevel.LOW,
    tags=["academic", "semantic-scholar", "read-only"],
), handler=semantic_scholar_handler)
```

### 2.4 Claim 校验 pass

**涉及文件：**
- `app/agent/reporter.py` — `generate_markdown_report()` 增加校验 pass
- `app/evidence/reasoning_service.py` — 无需修改，复用现有数据

**设计：**

在 `generate_markdown_report()` 中，生成最终报告之前：

1. 检查 `provenance_bundle.resolutions` 中 `status in ("unresolved", "requires_human")` 的条目
2. 对这些 claim，在报告 §3 最终回答中追加 `⚠️ 待核实` 标记
3. 新增 `## 10. 限制与待核实结论` 章节，列出：
   - 每个未解决 claim 的文本
   - 冲突状态
   - 支持/反驳证据数量
   - 建议的人工判断方向

**函数设计：**
```python
def _render_limitations_section(provenance_bundle, groups) -> list[str]:
    """Generate limitations section with downgraded claims."""
```

**关键原则：** 这个 pass 不修改 `reasoning_service` 的判定逻辑，只是在报告层面将已有判定转化为用户可见的限制说明。

### 2.5 Demo 脚本

**涉及文件：**
- 新增 `scripts/demo_deep_research.py`

**设计：**
```python
"""一句命令演示完整调研链路。

用法：
    python scripts/demo_deep_research.py
    python scripts/demo_deep_research.py --question "对比 PyTorch 和 TensorFlow"

演示流程：
    1. 创建任务 → 显示 run_id
    2. 规划步骤 → 显示 plan 摘要
    3. 执行工具 → 每步显示进度
    4. 生成报告 → 显示 report 路径
    5. 展示 trace → 显示 trace 摘要
"""

PRESET_QUESTIONS = [
    "对比 LangGraph、CrewAI 和 AutoGen 的 Agent 编排能力",
    "2024年大模型安全领域最重要的5篇论文",
    "向量数据库 Milvus vs Qdrant vs Weaviate 技术对比",
]
```

**关键点：**
- 不依赖 Streamlit，纯命令行
- 每阶段有 `[1/5]` 进度指示
- 显示关键指标：步骤数、trace 数、token 消耗（如有）
- 输出最终报告的前 50 行预览
- 失败步骤显示结构化错误信息

---

## 三、变更文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `app/agent/reporter.py` | report_type 参数 + TOC + 校验 pass + 限制章节 |
| 修改 | `app/agent/executor.py` | 传递 report_type；汇总 token/cost |
| 修改 | `app/agent/parallel_executor.py` | 传递 report_type；汇总 token/cost |
| 修改 | `app/llm/base.py` | LLMUsage model；LLMResponse 加 usage 字段 |
| 修改 | `app/llm/providers.py` | complete() 解析 usage |
| 新增 | `app/llm/cost.py` | 成本计算（各模型定价） |
| 修改 | `app/trace/logger.py` | record_tool_result 接受 token 参数 |
| 修改 | `app/trace/store.py` | update_agent_run_cost() |
| 新增 | `app/tools/arxiv_search.py` | arXiv API 检索 |
| 新增 | `app/tools/semantic_scholar.py` | Semantic Scholar API 检索 |
| 修改 | `app/tools/defaults.py` | 注册 arxiv_search + semantic_scholar_search |
| 修改 | `app/config.py` | 新增学术检索器配置项 |
| 修改 | `app/agent/react_executor.py` | LLM token 记录 |
| 修改 | `frontend/streamlit_app.py` | 成本侧栏展示 |
| 修改 | `.env.example` | 新增配置变量 |
| 新增 | `scripts/demo_deep_research.py` | Demo 脚本 |
| 新增 | `tests/test_phase6.py` | 单元测试 |
| 修改 | `TASK.md` | Phase 6 执行记录 |
| 修改 | `CLAUDE.md` | 更新 smoke check 文档 |

---

## 四、实施顺序

1. **6.2 LLM 成本追踪**（基础改造，后续依赖）— 修改 `LLMResponse` + `providers.py` + `logger.py`
2. **6.1 多报告类型**（依赖 reporter，独立可测）— 修改 `reporter.py` + `executor.py`
3. **6.4 Claim 校验 pass**（依赖 reporter + reasoning）— 修改 `reporter.py`
4. **6.3 学术检索器**（独立模块）— 新增两个 tool 文件 + 注册
5. **6.5 Demo 脚本**（集成上述所有）— 新增 `demo_deep_research.py`
6. **测试 + Streamlit** — 新增 test_phase6.py + Streamlit 成本展示
7. **验收** — 语法检查 + 单元测试 + smoke + TASK.md 更新 + commit

---

## 五、向后兼容性

| 场景 | 行为 |
|------|------|
| `report_type="summary"`（默认） | 与 Phase 5 行为完全一致 |
| LLM 不可用 | token_in/out/cost 保持 0，不影响报告生成 |
| 无 provenance bundle | 校验 pass 跳过，不产生限制章节 |
| arXiv/Semantic Scholar 不可用 | 工具返回 failed trace，不阻塞其他工具 |
| 离线模式 | Demo 脚本使用 mock 工具，正常跑通 |
| `app/rag/` 全部代码 | 保留不动 |

---

## 六、验收标准

- [ ] `detailed_report` 含自动生成 TOC
- [ ] `outline_report` 仅含大纲（生成快，<2s）
- [ ] LLMResponse 含 usage 字段，providers.py 正确解析
- [ ] ToolTrace token_in/token_out/estimated_cost 正确写入
- [ ] AgentRun 汇总 token/cost
- [ ] Streamlit 侧栏展示 run 成本
- [ ] `arxiv_search` 工具可正常搜索并返回论文列表
- [ ] `semantic_scholar_search` 工具可正常搜索并返回论文列表
- [ ] refutes/unresolved claim 在报告中降级为「待核实」
- [ ] `demo_deep_research.py` 一句命令跑完全程
- [ ] 语法检查 + 单元测试 + 全部 smoke 通过
