# Phase 3 实施方案：报告重构 + Skills 系统

> 日期：2026-07-26　预估：2-3 天

---

## 一、整体架构

Phase 3 分为两大模块，按依赖顺序执行：

```
┌──────────────────────────────────────────────────────────────┐
│ Step 1: 报告重构 (5.1)                                        │
│ reporter.py 新增按子查询/主题分组 + content_basis 标注         │
│ → 依赖 Phase 2 的 sub_query 列 + content_basis 列 (已就绪)    │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│ Step 2: Skills 系统 (5.2–5.6)                                 │
│ JSON Skill 文件 → Loader → Registry → API → Planner → Streamlit│
│ → 不依赖 Step 1，可并行开发，但建议串行以减少冲突               │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、Step 1: Deterministic 报告重构（TASK.md §5.1）

### 2.1 目标

- deterministic 模式按子查询/主题分组组织证据
- 模板生成「每个子问题的发现 + 支撑引用编号」结构
- 素材使用 claim 证据图中的 ResearchClaim + Citation，替代工具输出流水账
- 结合 Phase 2 的 `content_basis` 标记，每个发现后标注（全文 / 仅摘要 / 部分截断）
- LLM 模式与 deterministic 模式共用同一套「按主题分组」中间结构

### 2.2 现状分析

当前 `reporter.py:generate_markdown_report()` 的结构：
```
## 1. 任务说明
## 2. 运行摘要
## 3. 最终回答         ← 当前：单一大段模板/LLM合成，不按子查询分组
## 4. 执行计划         ← 步骤列表
## 5. ReAct 决策过程   ← 可选
## 6. 证据与工具观察结果 ← 按步骤列出，流水账风格
## 7. Claim Provenance V2 ← 可选
## 8. 可靠性、冲突与限制  ← 可选
## 9. 失败与拒绝详情      ← 可选
```

当前问题：
- 「3. 最终回答」是一个整体回答，不区分子查询
- 「6. 证据与工具观察结果」是工具输出的流水账
- 没有按子查询/主题分组，也没有 content_basis 标注

### 2.3 改造方案

#### 2.3.1 新增中间数据结构 `SubQueryGroup`

在 `reporter.py` 中新增 dataclass：

```python
@dataclass
class SubQueryGroup:
    sub_query: str           # 子查询文本（空串表示未分组）
    step_nos: list[int]      # 属于该组的步骤号
    traces: list[ToolTrace]  # 该组的 trace
    claims: list[dict]       # 来自 provenance_bundle 的 report_claims
    citations: list[dict]    # 来自 provenance_bundle 的 citations
    passages: list[dict]     # 来自 provenance_bundle 的 passages
```

#### 2.3.2 新增分组函数 `_build_sub_query_groups()`

```python
def _build_sub_query_groups(
    plan: dict, traces: list[ToolTrace],
    provenance_bundle: dict | None
) -> list[SubQueryGroup]:
```

逻辑：
1. 从 traces 的 `sub_query` 字段提取分组键
2. 若 traces 无 `sub_query`，回退为按步骤顺序的单组（sub_query=""）
3. 关联 provenance_bundle 中的 citations → passages → claims

#### 2.3.3 重构 `_render_final_answer()`

- 现有逻辑保留（兼容无 provenance_bundle 的场景）
- **新增**：当 provenance_bundle 存在时，生成按子查询分组的回答：

```markdown
### 子问题 1: {sub_query_text}
**发现：**
- 发现点 A [CIT-001-01]（全文）
- 发现点 B [CIT-001-02]（仅摘要）

### 子问题 2: {sub_query_text}
**发现：**
- 发现点 C [CIT-002-01]（全文）
```

#### 2.3.4 重构「6. 证据与工具观察结果」

- 保留现有步骤级展示作为详情
- **新增**：在每个步骤下标注 content_basis：
  - `🌐 全文证据 (full_text)`
  - `📄 部分截断 (partial)`
  - `📎 仅摘要 (snippet_only)`

#### 2.3.5 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/agent/reporter.py` | 修改 | 新增 SubQueryGroup、_build_sub_query_groups、重构 _render_final_answer、增加 content_basis 标注 |
| `tests/test_phase3.py` | 新增 | 测试分组逻辑、content_basis 标注、引用编号生成 |

### 2.4 验收标准映射

- [ ] deterministic 报告按子主题分组，带引用编号和 content_basis 标记
- [ ] 无子查询时回退为单组展示（向后兼容）
- [ ] 语法检查 + 单元测试 + smoke_e2e 通过

---

## 三、Step 2: Skills 系统（TASK.md §5.2–5.6）

### 3.1 数据流

```
workspace/skills/*.json          ← 用户可编辑的 Skill 定义文件
        │
        ▼
app/skills/loader.py             ← 启动时扫描、解析、校验
        │
        ▼
app/skills/registry.py           ← 内存注册表
        │
        ├──▶ app/api/skills.py   ← GET /api/skills, GET /api/skills/{name}
        │
        ├──▶ app/agent/planner.py ← plan_task(skill_name="...")
        │         │
        │         ▼
        │    executor.py          ← 解析 {{steps[N].output.field}} 引用
        │
        └──▶ frontend/streamlit_app.py ← 场景模板动态加载
```

### 3.2 新增模块

#### 3.2.1 `app/skills/__init__.py`

空 init，导出 loader 和 registry 的公开接口。

#### 3.2.2 `app/skills/models.py` — Pydantic 数据模型

```python
from pydantic import BaseModel, Field

class SkillParameter(BaseModel):
    type: str = "string"
    required: bool = False
    default: Any = None

class SkillStep(BaseModel):
    tool_name: str
    goal: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)

class SkillDefinition(BaseModel):
    name: str
    version: str = "1.0"
    description: str = ""
    required_tools: list[str] = Field(default_factory=list)
    parameters: dict[str, SkillParameter] = Field(default_factory=dict)
    steps: list[SkillStep] = Field(default_factory=list)

class SkillMeta(BaseModel):
    """Lightweight metadata for list endpoint."""
    name: str
    version: str
    description: str
    required_tools: list[str]
    parameters: dict[str, Any]
    status: str = "valid"       # valid | invalid
    error: str | None = None    # validation error message if invalid
```

#### 3.2.3 `app/skills/loader.py` — Skill 文件加载器

```python
def load_skill_from_file(path: Path) -> SkillDefinition | None
def load_all_skills(skills_dir: Path) -> dict[str, SkillDefinition]
def validate_skill(skill: SkillDefinition, available_tools: set[str]) -> list[str]  # errors
```

关键逻辑：
1. 扫描 `workspace/skills/*.json`
2. 每个文件 JSON parse → Pydantic 校验
3. `validate_skill`：检查 `required_tools` 是否都在 Tool Registry 中
4. 校验失败不影响其他 Skill 加载，记录 error

#### 3.2.4 `app/skills/registry.py` — 内存注册表

```python
def init_skill_registry(skills_dir: Path) -> None
def get_skill(name: str) -> SkillDefinition | None
def list_skills() -> list[SkillMeta]
def reload_skills(skills_dir: Path) -> None
```

启动时调用 `init_skill_registry()`，将结果存入模块级 dict。

### 3.3 预置 Skill 文件

#### 3.3.1 `workspace/skills/deep_web_research.json`

```json
{
  "name": "deep_web_research",
  "version": "1.0",
  "description": "深度网页调研：搜索发现 URL → 正文抓取 → 证据压缩 → 报告",
  "required_tools": ["tavily_search", "web_fetcher"],
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
        "urls": [],
        "max_chars": 8000,
        "timeout_seconds": 10
      },
      "arguments_from": {"step_no": "{{steps[0].step_no}}", "field": "results"}
    },
    {
      "tool_name": "report_writer",
      "goal": "从抓取正文中提取证据并生成报告",
      "arguments": {}
    }
  ]
}
```

#### 3.3.2 `workspace/skills/technical_docs_research.json`

4 步：`mcp_github_search → rag_search → web_fetcher → report_writer`

#### 3.3.3 `workspace/skills/local_audit.json`

3 步：`file_reader → sql_query → rag_search → report_writer`

#### 3.3.4 `workspace/skills/quick_search.json`

2 步：`tavily_search → report_writer`

### 3.4 Planner 集成

#### 3.4.1 `app/agent/planner.py` — `plan_task()` 新增 `skill_name` 参数

```python
def plan_task(
    task: str,
    allowed_tools: list[str] | None = None,
    source_mode: str = "real",
    planner_mode: str | None = None,
    scenario_template: str | None = None,
    execution_mode_override: str | None = None,
    skill_name: str | None = None,          # ★ 新增
) -> dict[str, Any]:
```

当 `skill_name` 不为 None 时：
1. `skill = get_skill(skill_name)`，若不存在则 fallback 到 keyword-matching
2. `_fill_skill_parameters(skill, task)` — 用 task 文本提取参数值
3. `_skill_to_plan(skill, task)` — 将 Skill 步骤转为 plan steps
4. 返回 plan（`planner_source = "skill"`）

#### 3.4.2 占位符填充 `_fill_skill_parameters()`

```
{{parameters.query}}     → 从 task 文本提取或使用 task 全文
{{parameters.max_urls}}  → 从 task 文本提取数字或使用 default
{{steps[0].step_no}}     → 运行时由 executor 解析（保持字面量，由 executor 处理）
{{steps[0].output.urls}} → 运行时由 executor 解析
```

设计原则：
- `{{parameters.*}}` → planner 阶段填充（用 task 文本 + 正则提取）
- `{{steps[N].output.field}}` → executor 阶段填充（复用 Phase 2 的 `_resolve_arguments_from`）
- `{{steps[N].step_no}}` → planner 阶段填充（编译时已知步骤序号）

#### 3.4.3 `_skill_to_plan()`

```python
def _skill_to_plan(
    skill: SkillDefinition, task: str, allowed_tools: list[str] | None
) -> dict[str, Any]:
    steps = []
    for i, skill_step in enumerate(skill.steps, 1):
        step = _step_template(skill_step.tool_name, task)
        step["step_no"] = i
        step["tool_name"] = skill_step.tool_name
        if skill_step.goal:
            step["goal"] = skill_step.goal
        # Fill compile-time placeholders
        arguments = _fill_compile_time_placeholders(
            skill_step.arguments, skill.steps, i
        )
        step["arguments"] = arguments
        # Preserve arguments_from for runtime resolution
        if hasattr(skill_step, 'arguments_from') and skill_step.arguments_from:
            step["arguments_from"] = _fill_compile_time_placeholders(
                skill_step.arguments_from, skill.steps, i
            )
        steps.append(step)
    return {
        "version": "skill-v1",
        "task": task,
        "skill_name": skill.name,
        "skill_version": skill.version,
        "steps": steps,
        "notes": [f"Plan generated from skill '{skill.name}' v{skill.version}"],
        ...
    }
```

### 3.5 Executor 增强

#### 3.5.1 扩展 `_resolve_arguments_from()` 支持 Skill 引用语法

现有 executor 已支持 `{step_no: N, field: "results"}` 语法。

需要新增支持：
- `arguments_from` 中 `step_no` 为字符串 `"{{steps[0].step_no}}"` → planner 阶段已替换为实际数字
- 无需修改 executor，planner 阶段完成替换即可

### 3.6 API 端点

#### 3.6.1 `app/api/skills.py` — 新增

```python
GET /api/skills              → list_skills() → [SkillMeta]
GET /api/skills/{name}       → get_skill() → SkillDefinition | 404
POST /api/skills/reload      → reload_skills() → {"status": "ok", "count": N}
```

#### 3.6.2 `app/main.py` 注册

```python
from app.api import skills
app.include_router(skills.router, prefix=settings.api_prefix)
```

### 3.7 Schema 扩展

#### 3.7.1 `app/schemas.py` — TaskCreateRequest 新增字段

```python
class TaskCreateRequest(BaseModel):
    ...
    skill_name: str | None = None       # ★ 新增
```

### 3.8 Streamlit 集成

#### 3.8.1 场景模板动态加载

当前：`DEMO_TEMPLATES` 硬编码 3 个模板。

改造后：
1. 启动时 `GET /api/skills` 获取已安装 Skill 列表
2. 场景模板下拉框 = 硬编码 DEMO_TEMPLATES + 动态 Skill 列表
3. 选中 Skill 时展示其 description、required_tools、参数
4. 创建任务时传 `skill_name` 替代 `scenario_template_key`

#### 3.8.2 具体修改

在 `render_sidebar()` 中：
- 新增 `_load_skills()` 辅助函数
- `st.selectbox("选择演示场景")` 的 options 合并 DEMO_TEMPLATES + skills
- 选中 Skill 时显示参数表单（简单实现：task 文本中提示参数）
- `_current_scenario_template_key()` 逻辑调整

### 3.9 生命周期集成

#### 3.9.1 `app/main.py` — lifespan 中初始化

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    ...
    init_db()
    register_default_tools()
    from app.skills.registry import init_skill_registry
    init_skill_registry(Path("workspace/skills"))
    await _register_remote_mcp_tools_with_retry()
    yield
```

### 3.10 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/skills/__init__.py` | 新增 | 模块入口 |
| `app/skills/models.py` | 新增 | Pydantic 模型 |
| `app/skills/loader.py` | 新增 | 文件扫描 + 校验 |
| `app/skills/registry.py` | 新增 | 内存注册表 |
| `app/api/skills.py` | 新增 | API 端点 |
| `workspace/skills/deep_web_research.json` | 新增 | 预置 Skill |
| `workspace/skills/technical_docs_research.json` | 新增 | 预置 Skill |
| `workspace/skills/local_audit.json` | 新增 | 预置 Skill |
| `workspace/skills/quick_search.json` | 新增 | 预置 Skill |
| `app/agent/planner.py` | 修改 | 新增 skill_name 参数 + _skill_to_plan |
| `app/agent/executor.py` | 修改 | 扩展 arguments_from 支持 steps[N].step_no |
| `app/schemas.py` | 修改 | TaskCreateRequest 加 skill_name |
| `app/main.py` | 修改 | 注册 skills router + lifespan 初始化 |
| `frontend/streamlit_app.py` | 修改 | 动态加载 Skill 列表 |
| `tests/test_phase3.py` | 新增 | 覆盖 5.1 + 5.2-5.6 全部验收项 |

---

## 四、执行顺序（12 步）

### Step 1: 报告重构（5.1）

| # | 任务 | 文件 |
|---|------|------|
| 1 | 新增 SubQueryGroup dataclass + _build_sub_query_groups() | `app/agent/reporter.py` |
| 2 | 重构 _render_final_answer() 支持分组展示 + 引用编号 | `app/agent/reporter.py` |
| 3 | 重构「6. 证据与工具观察结果」增加 content_basis 标注 | `app/agent/reporter.py` |
| 4 | 补测试 | `tests/test_phase3.py` |
| 5 | 验证：语法检查 + 单元测试 + smoke_e2e | — |

### Step 2: Skills 系统（5.2–5.6）

| # | 任务 | 文件 |
|---|------|------|
| 6 | 创建 app/skills/ 模块（models, loader, registry） | 3 个新文件 |
| 7 | 创建 4 个预置 Skill JSON 文件 | `workspace/skills/*.json` |
| 8 | 新增 /api/skills 端点 + 注册到 main.py | `app/api/skills.py`, `app/main.py` |
| 9 | Planner 集成：plan_task() 支持 skill_name | `app/agent/planner.py` |
| 10 | schemas.py：TaskCreateRequest 加 skill_name | `app/schemas.py` |
| 11 | Streamlit 集成：动态加载 Skill 列表 | `frontend/streamlit_app.py` |
| 12 | 补测试 + 全量验证 | `tests/test_phase3.py` |

---

## 五、向后兼容性保障

| 场景 | 保障措施 |
|------|----------|
| 不传 `skill_name` | planner 保持现有 keyword-matching 行为不变 |
| `workspace/skills/` 目录为空 | loader 返回空 dict，不影响系统运行 |
| Skill 文件 JSON 格式错误 | 该 Skill status=invalid，其他 Skill 正常加载 |
| `required_tools` 中的工具未注册 | 校验警告，仍可加载（运行时由 executor 处理） |
| 报告无 provenance_bundle | 回退到现有模板逻辑（_render_final_answer） |
| 报告无子查询 | 按单组展示（现有行为） |
| `app/rag/` 全部代码 | 保留不动 |

---

## 六、验收标准（来自 TASK.md §5.7）

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
