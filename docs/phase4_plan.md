# Phase 4 实施计划：用户画像提取 + 记忆面板

> 日期：2026-07-28　状态：规划中　预估：2 天

---

## 一、现状盘点

### 已就绪（Phase 1 已建）

| 组件 | 文件 | 状态 |
|------|------|------|
| ORM 模型 | `app/memory/models.py` — UserMemory, ChatTurn, ConversationSession | ✅ |
| CRUD Store | `app/memory/store.py` — 17 个函数 | ✅ |
| 注入策略 | `app/memory/policy.py` — 预算控制、冷启动、排序 | ✅ |
| 记忆 API | `app/api/memory.py` — list/confirm/delete/clear | ✅ |
| 会话 API | `app/api/sessions.py` — CRUD | ✅ |
| 工具注册 | `app/tools/defaults.py` — memory_search 已注册（handler=None） | ✅ |
| Streamlit 骨架 | 侧栏会话切换器 + 记忆面板（加载/确认/拒绝按钮） | ✅ |
| 测试 | `tests/test_memory.py` — 27 个（模型/store/policy） | ✅ |

### 缺失（Phase 4 待建）

| 组件 | 说明 |
|------|------|
| `app/memory/extractor.py` | 规则提取器 — 从完成的 run 中蒸馏用户偏好 |
| `app/memory/retriever.py` | 记忆召回 — 关键词 + 可选向量（复用 app/rag） |
| memory_search handler | 连接 retriever 到 Tool Registry |
| Executor 集成 | run 完成时自动触发提取 + 创建 ChatTurn |
| Planner 集成 | 任务创建时注入 active 记忆到上下文 |
| Tasks API 集成 | run 完成回调中触发记忆提取 |
| Streamlit 完善 | 冷启动进度提示、「清空全部」按钮、删除单条 |
| 新测试 | 提取器、检索器、memory_search handler、集成路径 |

---

## 二、实施步骤（共 10 步）

### Step 1：`app/memory/extractor.py` — 规则版用户画像提取器

**职责**：从完成的 run 中提取用户偏好，生成 pending 记忆。

**提取规则（全部确定性，无需 LLM）**：

1. **语言偏好**：检测 task 文本的语言（中文字符占比 >30% → 中文偏好；纯英文 → 英文偏好）
2. **报告格式偏好**：从 task 文本检测 "Word"/"PDF"/"Markdown"/"docx" 关键词
3. **领域关键词**：从 task 文本提取 ≥2 次出现的领域词（如 "Agent", "RAG", "LLM"）
4. **样本门槛**：同一偏好信号需在 ≥2 个不同 run 中出现才生成记忆（MIN_SAMPLE_THRESHOLD=2）

**核心函数**：

```python
def extract_preferences_from_run(
    db: Session,
    run: AgentRun,
    tenant_id: str,
    user_id: str,
) -> list[UserMemory]:
    """从单个 run 提取偏好信号（不持久化），返回候选记忆列表。"""

def commit_pending_memories(
    db: Session,
    tenant_id: str,
    user_id: str,
    run: AgentRun,
    candidates: list[UserMemory],
) -> int:
    """持久化候选记忆，应用 ≥2 次样本门槛。返回新增 pending 数量。"""

def should_extract_for_run(db: Session, tenant_id: str, user_id: str) -> bool:
    """检查是否需要提取：当前用户已完成 ≥2 个 run 才触发。"""
```

**样本门槛实现逻辑**：
- 提取当前 run 的信号 → 与历史 active + pending 记忆比较
- 同一 (kind, content) 组合出现 ≥2 次 → 生成/更新 pending 记忆
- 首次出现 → 只记录不生成（待下次确认）

---

### Step 2：`app/memory/retriever.py` — 记忆召回器

**职责**：按查询召回相关 active 记忆，供 planner 和 memory_search 工具使用。

**两种模式**：

1. **关键词召回**（默认，离线可用）：对 query 分词，匹配 content 字段
2. **向量召回**（可选，需 SentenceTransformers）：复用 `app/rag/embedding_backends.py`，对 UserMemory.content 做语义检索

**核心函数**：

```python
def retrieve_memories(
    db: Session,
    tenant_id: str,
    user_id: str,
    query: str,
    top_k: int = 5,
    use_vector: bool = False,
) -> list[UserMemory]:
    """召回与 query 相关的 active 记忆。"""

def retrieve_for_injection(
    db: Session,
    tenant_id: str,
    user_id: str,
    task: str,
    max_chars: int = MAX_INJECTION_CHARS,
) -> tuple[list[UserMemory], str]:
    """为 planner 注入准备记忆上下文。
    Returns (selected_memories, formatted_context_string).
    """
```

**向量召回的嵌入复用**：
- 使用 `create_embedding_backend(settings)` 获取后端
- 调用 `backend.embed_query(query)` 获取 query 向量
- 对每条 active 记忆调用 `backend.embed_texts([mem.content])` 获取向量
- 用 cosine_similarity 排序
- 确定性后端（默认）直接用关键词匹配 + TF 排序

---

### Step 3：`memory_search` 工具 handler

**位置**：在 `app/memory/retriever.py` 中实现 handler 函数，在 `app/tools/defaults.py` 中注册。

```python
# app/memory/retriever.py
def memory_search_handler(arguments: dict) -> ToolResult:
    """Tool handler for memory_search — keyword/vector recall of user memories."""
    query = str(arguments.get("query") or "")
    top_k = int(arguments.get("top_k") or 5)
    # ... 获取 db session、tenant/user context，调用 retrieve_memories
    return ToolResult(success=True, output={...})

# app/tools/defaults.py — 替换 handler=None
register_tool(
    ToolSpec(name="memory_search", ...),
    handler=memory_search_handler,
)
```

**注意**：memory_search 需要访问数据库 session。现有 tool handler 都通过全局变量或闭包访问 db，这里需要特殊处理。方案：handler 内部通过 `app.database.SessionLocal()` 创建临时 session，从安全上下文中获取 tenant_id/user_id（或使用默认值）。

---

### Step 4：Executor 集成 — run 完成时触发记忆提取

**位置**：修改 `app/agent/executor.py` 的 `run_plan()` 和 `app/agent/parallel_executor.py` 的 `run_plan_parallel()`。

**在 run 状态变为 completed 之后、return 之前插入**：

```python
# 1. 创建 ChatTurn（记录此轮交互）
from app.memory.store import create_chat_turn
if run.session_id:
    create_chat_turn(db, run.session_id, "agent", report_summary, run_id=run.run_id)

# 2. 触发记忆提取
from app.memory.extractor import extract_preferences_from_run, commit_pending_memories
if should_extract_for_run(db, tenant_id, user_id):
    candidates = extract_preferences_from_run(db, run, tenant_id, user_id)
    new_count = commit_pending_memories(db, tenant_id, user_id, run, candidates)
    # 3. 写 memory_extraction trace 事件
    if new_count > 0:
        record_trace_event(db, run_id, step_no=-1, tool_name="memory_extraction", ...)
```

**tenant_id / user_id 获取**：当前 executor 不持有这些信息。方案：
- 从 `run.run_config_snapshot` JSON 中读取（Phase 1 已存入）
- 或在 `run_config_snapshot` 中新增 `tenant_id` / `user_id` 字段

**ChatTurn 内容**：使用 reporter 生成的 markdown 前 500 字符作为摘要。

---

### Step 5：Planner 集成 — 任务创建时注入记忆

**位置**：修改 `app/agent/planner.py` 的 `plan_task()` 函数。

**在 plan 生成前插入记忆注入逻辑**：

```python
def plan_task(task, allowed_tools=None, source_mode="real", ..., skill_name=None):
    # Phase 4: Memory injection
    memory_context = ""
    try:
        from app.database import SessionLocal
        from app.memory.retriever import retrieve_for_injection
        db = SessionLocal()
        try:
            _, memory_context = retrieve_for_injection(
                db, tenant_id="demo", user_id="local-user", task=task
            )
        finally:
            db.close()
    except Exception:
        pass  # 记忆注入失败不影响 plan 生成

    if memory_context:
        # 将记忆上下文注入 task，或作为 plan 的额外字段
        plan["injected_memory_context"] = memory_context

    # ... 后续 skill / deterministic / llm 逻辑
```

**设计要点**：
- 记忆注入失败不应阻塞 plan 生成（try/except）
- 注入的上下文放在 plan 的 `injected_memory_context` 字段中
- Reporter 可以在生成报告时引用此上下文
- 需要在 `run_config_snapshot` 中包含 `tenant_id` 和 `user_id`

---

### Step 6：Tasks API 集成

**位置**：修改 `app/api/tasks.py` 的 `create_task` 端点。

**任务创建时**：
- 已支持 `session_id` 参数（Phase 1） ✅
- `run_config_snapshot` 已自动写入（Phase 1） ✅
- **新增**：在 `run_config_snapshot` 中包含 `tenant_id` 和 `user_id`

**修改 `create_agent_run` 调用处**：将 tenant_id/user_id 写入 `run_config_snapshot` JSON 中。

---

### Step 7：Streamlit 记忆面板完善

**位置**：修改 `frontend/streamlit_app.py`。

**新增/改进**：

1. **冷启动进度提示**：当前已有静态文本「完成 3 次调研后…」，改为动态：
   ```python
   completed_runs = _count_completed_runs()  # 新 API 或从 sessions 推算
   if completed_runs < 3:
       st.progress(completed_runs / 3, text=f"完成 {completed_runs}/3 次调研后，系统将开始为您总结偏好")
   ```

2. **删除单条记忆按钮**：pending 和 active 记忆旁增加「删除」按钮
   ```python
   if st.button("🗑️", key=f"del_{mem['memory_id']}"):
       _delete_memory(mem["memory_id"])
       st.session_state.memory_list = _load_memories()
       st.rerun()
   ```

3. **「清空全部记忆」按钮**：在记忆面板底部增加
   ```python
   if total > 0:
       if st.button("清空全部记忆", type="secondary", use_container_width=True):
           api_request("DELETE", "/api/memory", timeout=5)
           st.session_state.memory_list = _load_memories()
           st.rerun()
   ```

4. **记忆面板自动加载**：去除手动「加载记忆」按钮，进入页面时自动加载

---

### Step 8：`app/memory/__init__.py` 更新

新增导出：
```python
from app.memory.extractor import (
    commit_pending_memories,
    extract_preferences_from_run,
    should_extract_for_run,
)
from app.memory.retriever import (
    memory_search_handler,
    retrieve_for_injection,
    retrieve_memories,
)
```

---

### Step 9：测试 — `tests/test_phase4.py`

**测试分类**（预估 25-30 个）：

| 类别 | 数量 | 内容 |
|------|------|------|
| Extractor | 8-10 | 语言检测、格式偏好、领域关键词提取、样本门槛、≥2 次触发、should_extract_for_run |
| Retriever | 6-8 | 关键词召回、空查询、top_k 限制、向量召回降级、format_memory_context |
| memory_search handler | 3-4 | 正常调用、空查询、无匹配结果 |
| 集成 | 5-6 | run 完成→ChatTurn 创建、提取触发、trace 事件、memory_search 出现在工具列表 |
| 端到端 | 2-3 | 多次 run 后 pending 记忆生成、确认后变 active |

---

### Step 10：文档更新

1. 更新 `TASK.md` — Phase 4 执行记录
2. 更新 `CLAUDE.md` — Phase 状态表
3. 更新 `AGENTS.md`（如需要）

---

## 三、变更文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `app/memory/extractor.py` | 规则提取器 |
| **新增** | `app/memory/retriever.py` | 记忆召回器 + memory_search handler |
| **新增** | `tests/test_phase4.py` | Phase 4 测试 |
| **新增** | `docs/phase4_plan.md` | 本文件 |
| **修改** | `app/memory/__init__.py` | 新增导出 |
| **修改** | `app/tools/defaults.py` | memory_search 注册 handler |
| **修改** | `app/agent/executor.py` | run 完成时触发记忆提取 + ChatTurn |
| **修改** | `app/agent/parallel_executor.py` | 同上 |
| **修改** | `app/agent/planner.py` | 任务创建时注入记忆 |
| **修改** | `app/api/tasks.py` | run_config_snapshot 含 tenant_id/user_id |
| **修改** | `frontend/streamlit_app.py` | 记忆面板完善 |
| **修改** | `CLAUDE.md` | Phase 状态更新 |
| **修改** | `TASK.md` | Phase 4 执行记录 |

---

## 四、验收标准（来自 TASK.md §6.5）

- [ ] 多次调研后系统给出"您的偏好"待确认列表
- [ ] 单次行为不产生记忆（≥2 次门槛）
- [ ] pending → active → superseded 状态流转正确
- [ ] 删除操作写 trace
- [ ] 注入预算不超 800 字
- [ ] 记忆面板可交互
- [ ] 语法检查 + 单元测试 + 相关 smoke 全部通过

---

## 五、风险与边界

| 风险 | 应对 |
|------|------|
| tenant_id/user_id 在 executor 中不可用 | 从 `run.run_config_snapshot` JSON 中读取，Step 6 确保写入 |
| memory_search handler 需要 db session | 使用 `SessionLocal()` 创建临时 session |
| 向量召回依赖 SentenceTransformers | 默认使用关键词召回，向量为可选增强 |
| 提取器规则过于简单 | 确定性规则是 v1，Phase 5 补 LLM 蒸馏 |
| 记忆注入增加 plan 生成延迟 | 轻量级 keyword 检索，≤50ms 目标 |
