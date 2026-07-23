# Phase 1 实施计划：记忆模块确定性部分 + 配置快照

> 版本：v1.0　日期：2026-07-23
> 基于：TASK.md Phase 1 + 完整代码走查

---

## 一、变更文件清单

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `migrations/versions/0004_memory_schema.py` | **新增** | Alembic 迁移：3 表 + 2 列 |
| 2 | `scripts/migrate_database.py` | **修改** | 注册 P3 memory 表名到 bootstrap |
| 3 | `app/memory/__init__.py` | **新增** | 模块导出 |
| 4 | `app/memory/models.py` | **新增** | ConversationSession, ChatTurn, UserMemory ORM 模型 |
| 5 | `app/memory/store.py` | **新增** | CRUD 操作，对齐 trace/store.py 风格 |
| 6 | `app/memory/policy.py` | **新增** | 冷启动默认行为 + 注入预算控制 + 过期清理 |
| 7 | `app/trace/models.py` | **修改** | AgentRun 增加 `session_id`（String, 可空）和 `run_config_snapshot`（Text, 可空） |
| 8 | `app/trace/store.py` | **修改** | `create_agent_run` 接受 `session_id` 和 `run_config_snapshot` 参数 |
| 9 | `app/schemas.py` | **修改** | TaskCreateRequest 增加 `session_id`；新增 Session/Memory 响应 schema |
| 10 | `app/api/tasks.py` | **修改** | create_task 读取 session_id，自动写入 run_config_snapshot |
| 11 | `app/api/sessions.py` | **新增** | 会话 CRUD + 对话轮次查询 API |
| 12 | `app/api/memory.py` | **新增** | 记忆 CRUD + 确认/删除 API |
| 13 | `app/main.py` | **修改** | 注册 sessions 和 memory router；lifespan 中导入 memory models |
| 14 | `app/database.py` | **修改** | init_db() 导入 memory models |
| 15 | `app/tools/defaults.py` | **修改** | 注册 `memory_search` 只读工具 |
| 16 | `frontend/streamlit_app.py` | **修改** | 左侧栏：会话切换器 + 记忆面板 |
| 17 | `tests/test_memory.py` | **新增** | 记忆模型 + 冷启动 + CRUD 单元测试 |

---

## 二、逐文件变更详情

### 2.1 `migrations/versions/0004_memory_schema.py`（新增）

```python
revision = "0004_memory_schema"
down_revision = "0003_evidence_reasoning"
```

**创建表：**

```sql
-- conversation_sessions
CREATE TABLE conversation_sessions (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

-- chat_turns
CREATE TABLE chat_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id),
    role TEXT NOT NULL,              -- 'user' | 'agent'
    content TEXT NOT NULL,
    run_id TEXT,                     -- FK → agent_runs.run_id
    created_at DATETIME NOT NULL
);

-- user_memories
CREATE TABLE user_memories (
    memory_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,              -- 'profile' | 'preference' | 'fact' | 'interest'
    extraction_method TEXT NOT NULL, -- 'rule' | 'llm' | 'manual'
    content TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'active' | 'superseded' | 'expired'
    source_session_id TEXT,
    source_run_id TEXT,
    valid_until DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
```

**修改列：**

```sql
ALTER TABLE agent_runs ADD COLUMN session_id TEXT;
ALTER TABLE agent_runs ADD COLUMN run_config_snapshot TEXT;
```

### 2.2 `scripts/migrate_database.py`（修改）

在 `P2_TABLES` 之后新增 `P3_TABLES`：

```python
P3_TABLES = {
    "conversation_sessions",
    "chat_turns",
    "user_memories",
}
```

在 `bootstrap_revision_for_tables` 函数中增加 P3 分支：如果 P2 表完整但无 P3 表，stamp 到 `0003_evidence_reasoning`；如果 P3 表完整，stamp 到 `0004_memory_schema`。

### 2.3 `app/memory/models.py`（新增）

三个 SQLAlchemy ORM 模型，模式对齐 `app/trace/models.py`：

```python
class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str]
    user_id: Mapped[str]
    title: Mapped[str | None]
    created_at / updated_at (DateTime with timezone)

class ChatTurn(Base):
    __tablename__ = "chat_turns"
    turn_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] (FK → conversation_sessions)
    role: Mapped[str]  # 'user' | 'agent'
    content: Mapped[str]
    run_id: Mapped[str | None] (FK → agent_runs)
    created_at

class UserMemory(Base):
    __tablename__ = "user_memories"
    memory_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id / user_id
    kind  # profile | preference | fact | interest
    extraction_method  # rule | llm | manual
    content
    confidence (Float, default 0.5)
    status  # pending | active | superseded | expired
    source_session_id / source_run_id (nullable)
    valid_until (nullable DateTime)
    created_at / updated_at
```

### 2.4 `app/memory/store.py`（新增）

函数签名对齐 `app/trace/store.py` 风格：

```python
def create_session(db: Session, tenant_id: str, user_id: str, title: str | None = None) -> ConversationSession
def get_session(db: Session, session_id: str) -> ConversationSession | None
def list_sessions(db: Session, tenant_id: str, user_id: str) -> list[ConversationSession]
def update_session_title(db: Session, session_id: str, title: str) -> ConversationSession

def create_chat_turn(db: Session, session_id: str, role: str, content: str, run_id: str | None = None) -> ChatTurn
def list_chat_turns(db: Session, session_id: str) -> list[ChatTurn]

def create_user_memory(db: Session, tenant_id, user_id, kind, extraction_method, content, ...) -> UserMemory
def get_user_memory(db: Session, memory_id: str) -> UserMemory | None
def list_user_memories(db: Session, tenant_id: str, user_id: str, status: str | None = None) -> list[UserMemory]
def update_memory_status(db: Session, memory_id: str, status: str) -> UserMemory
def delete_user_memory(db: Session, memory_id: str) -> None
def delete_all_user_memories(db: Session, tenant_id: str, user_id: str) -> int
def supersede_memory(db: Session, memory_id: str) -> UserMemory
def expire_memories(db: Session) -> int  # 批量过期 valid_until < now 的记录
```

### 2.5 `app/memory/policy.py`（新增）

```python
# 冷启动常量
COLD_START_REASON = "cold_start"
MIN_SAMPLE_THRESHOLD = 2       # 同一偏好信号出现 ≥2 次才生成 pending 记忆
MAX_INJECTION_CHARS = 800      # 记忆注入预算上限

def should_inject_memory(active_memories: list[UserMemory]) -> bool
def select_memories_for_injection(active_memories: list[UserMemory], max_chars: int = MAX_INJECTION_CHARS) -> list[UserMemory]
    # 按 recency + confidence 排序，超限裁剪
def format_memory_context(memories: list[UserMemory]) -> str
def build_cold_start_trace_event() -> dict  # {recalled: 0, reason: "cold_start"}
```

### 2.6 `app/trace/models.py`（修改）

AgentRun 类增加两列：

```python
session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
run_config_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`session_id` 不使用 FK 约束（避免与 memory 模块的循环导入），仅作为字符串引用。

### 2.7 `app/trace/store.py`（修改）

`create_agent_run` 签名变更：

```python
def create_agent_run(
    db: Session,
    task: str,
    report_type: str,
    source_mode: str,
    allowed_tools: list[str] | None = None,
    session_id: str | None = None,          # ★ 新增
    run_config_snapshot: str | None = None,  # ★ 新增
) -> AgentRun:
```

创建 AgentRun 时赋值 `session_id` 和 `run_config_snapshot`。

### 2.8 `app/schemas.py`（修改）

**修改 TaskCreateRequest：**

```python
class TaskCreateRequest(BaseModel):
    task: str
    report_type: str = "summary"
    source_mode: str = "real"
    allowed_tools: list[str] | None = None
    execution_mode_override: str | None = None
    scenario_template: str | None = None
    scenario_template_key: str | None = None
    session_id: str | None = None  # ★ 新增
```

**新增 Session/Memory schema：**

```python
class SessionCreateRequest(BaseModel):
    title: str | None = None

class SessionResponse(BaseModel):
    session_id: str
    tenant_id: str
    user_id: str
    title: str | None
    turn_count: int
    created_at: datetime
    updated_at: datetime

class ChatTurnResponse(BaseModel):
    turn_id: str
    session_id: str
    role: str
    content: str
    run_id: str | None
    created_at: datetime

class UserMemoryResponse(BaseModel):
    memory_id: str
    kind: str
    extraction_method: str
    content: str
    confidence: float
    status: str
    source_session_id: str | None
    source_run_id: str | None
    valid_until: datetime | None
    created_at: datetime

class MemoryConfirmRequest(BaseModel):
    approved: bool

class MemoryListResponse(BaseModel):
    memories: list[UserMemoryResponse]
    total: int
    active_count: int
    pending_count: int
```

### 2.9 `app/api/tasks.py`（修改）

`create_task` 端点修改两处：

1. 读取 `request.session_id`，传给 `store.create_agent_run`
2. 在创建 run 之前，生成 `run_config_snapshot`：

```python
import json
from app.config import settings

# 在 create_task 函数内，create_agent_run 调用之前：
run_config_snapshot = json.dumps(
    settings.get_safe_runtime_config_summary(),
    ensure_ascii=False,
    sort_keys=True,
)

run = store.create_agent_run(
    db=db,
    task=request.task,
    report_type=request.report_type,
    source_mode=request.source_mode,
    allowed_tools=request.allowed_tools,
    session_id=request.session_id,
    run_config_snapshot=run_config_snapshot,
)
```

### 2.10 `app/api/sessions.py`（新增）

```python
router = APIRouter(prefix="/sessions", tags=["sessions"],
                   dependencies=[Depends(require_api_key), Depends(require_request_context)])

POST   ""                    → create_session(request, db, req_ctx)
GET    ""                    → list_sessions(db, req_ctx)
GET    "/{session_id}"       → get_session(session_id, db)
GET    "/{session_id}/turns" → list_turns(session_id, db)
```

### 2.11 `app/api/memory.py`（新增）

```python
router = APIRouter(prefix="/memory", tags=["memory"],
                   dependencies=[Depends(require_api_key), Depends(require_request_context)])

GET    ""                → list_memories(db, req_ctx, status: str | None = None)
POST   "/{memory_id}/confirm" → confirm_memory(memory_id, request, db)
DELETE "/{memory_id}"    → delete_memory(memory_id, db)
DELETE ""                → clear_all_memories(db, req_ctx)
```

### 2.12 `app/main.py`（修改）

在 lifespan 中导入 memory models（确保表注册到 Base.metadata）：

```python
from app.memory import models as memory_models  # noqa: F401
```

注册新路由：

```python
from app.api import sessions, memory
app.include_router(sessions.router, prefix=settings.api_prefix)
app.include_router(memory.router, prefix=settings.api_prefix)
```

### 2.13 `app/database.py`（修改）

在 `init_db()` 中导入 memory models：

```python
from app.memory import models as memory_models  # noqa: F401
```

### 2.14 `app/tools/defaults.py`（修改）

注册 `memory_search` 工具：

```python
register_tool(
    ToolSpec(
        name="memory_search",
        description=(
            "Search the user's cross-session memory for relevant preferences, "
            "facts, and research history. Read-only, returns active memories "
            "for the current tenant/user context."
        ),
        input_schema={"query": "string", "top_k": "integer"},
        output_schema={"memories": "array", "recalled": "integer"},
        risk_level=RiskLevel.LOW,
        tags=["memory", "search", "read-only"],
    ),
    handler=None,  # 当前 Phase 不实现 handler，仅注册 spec 供 planner 发现
)
```

### 2.15 `frontend/streamlit_app.py`（修改）

1. **会话切换器**（左侧栏上方）：
   - 下拉菜单列出当前用户的会话列表
   - "新建会话"按钮
   - 选中会话后，后续创建任务自动关联 session_id

2. **记忆面板**（左侧栏下方）：
   - 展示 active 记忆数量
   - 点击展开详情（内容 + 来源 run + 置信度 + 提取方式）
   - pending 记忆显示"确认/拒绝"按钮
   - 新用户显示进度提示：「完成 3 次调研后，系统将开始为您总结偏好」

### 2.16 `tests/test_memory.py`（新增）

```python
class MemoryModelTests(unittest.TestCase):
    def test_create_session_and_turn(self) -> None: ...
    def test_create_user_memory_defaults(self) -> None: ...
    def test_memory_status_lifecycle(self) -> None: ...

class MemoryPolicyTests(unittest.TestCase):
    def test_cold_start_behavior(self) -> None: ...
    def test_injection_budget_respected(self) -> None: ...
    def test_min_sample_threshold(self) -> None: ...

class MemoryStoreTests(unittest.TestCase):
    def test_list_by_tenant_user_isolation(self) -> None: ...
    def test_expire_memories(self) -> None: ...
    def test_supersede_memory(self) -> None: ...
```

---

## 三、实施步骤（严格顺序）

### Step 1：数据层 — Migration + Models

1. 创建 `migrations/versions/0004_memory_schema.py`
2. 创建 `app/memory/__init__.py`、`app/memory/models.py`
3. 修改 `app/trace/models.py`（AgentRun 加两列）
4. 修改 `scripts/migrate_database.py`（注册 P3_TABLES）
5. 修改 `app/database.py`（导入 memory models）
6. 运行迁移验证：`python scripts/migrate_database.py`

### Step 2：业务层 — Store + Policy

7. 创建 `app/memory/store.py`
8. 创建 `app/memory/policy.py`
9. 修改 `app/trace/store.py`（create_agent_run 加参数）

### Step 3：API 层 — Schema + Router

10. 修改 `app/schemas.py`（TaskCreateRequest + 新增 Session/Memory schema）
11. 创建 `app/api/sessions.py`
12. 创建 `app/api/memory.py`
13. 修改 `app/api/tasks.py`（session_id + run_config_snapshot）
14. 修改 `app/main.py`（注册路由 + lifespan 导入）

### Step 4：工具层 — memory_search 注册

15. 修改 `app/tools/defaults.py`

### Step 5：前端 — Streamlit

16. 修改 `frontend/streamlit_app.py`

### Step 6：测试

17. 创建 `tests/test_memory.py`

---

## 四、每步验证方式

| Step | 验证命令 | 预期结果 |
|------|---------|---------|
| 1-6 | `python scripts/migrate_database.py` | 输出 "database is at Alembic head"，SQLite 中可见新表 |
| 1-6 | `python -c "from app.memory.models import *; from app.trace.models import AgentRun; print('OK')"` | 无导入错误 |
| 7-9 | `python -c "from app.memory.store import *; print('OK')"` | 无导入错误 |
| 10-14 | `curl http://127.0.0.1:8000/health` | 服务正常启动 |
| 10-14 | `curl -X POST http://127.0.0.1:8000/api/sessions -H 'Content-Type: application/json' -d '{}'` | 返回 session_id |
| 10-14 | `curl -X POST http://127.0.0.1:8000/api/tasks -H 'Content-Type: application/json' -d '{"task":"test","session_id":"<id>"}'` | 任务创建成功，run 关联 session_id |
| 10-14 | `curl http://127.0.0.1:8000/api/tools` | memory_search 出现在工具列表中 |
| 15 | Streamlit 页面可切换会话 | 会话下拉菜单正常 |
| 16 | `python -m unittest discover -s tests -v` | 新增测试 + 原有测试全部通过 |
| 全部 | `python -m compileall -q app scripts frontend` | 无语法错误 |

---

## 五、关键设计决策

1. **session_id 不使用 FK 约束**：避免 `app/trace/models.py` 与 `app/memory/models.py` 之间的循环导入。session_id 是纯字符串引用，与现有 `run_id`（uuid hex）风格一致。

2. **run_config_snapshot 在 API 层生成**：不在 store 层生成（store 层不应依赖 config），由 `app/api/tasks.py` 调用 `settings.get_safe_runtime_config_summary()` 后传入。这样 store 层保持纯净，且未来其他入口（如 Streamlit 直接调用）也能控制 snapshot 内容。

3. **memory_search 当前 Phase 不实现 handler**：仅注册 ToolSpec 供 planner 发现和 API 展示。handler 在 Phase 4（向量召回）实现。这是有意的不完整——先让工具出现在列表中，后续补实现。

4. **冷启动不产生兜底文案**：`memory/policy.py` 的 `format_memory_context` 在无记忆时返回空字符串，planner 和 Reporter 不做任何替换。trace 中记录 `memory_recall` 事件标记 `cold_start`。

5. **样本门槛在 extractor 层（Phase 4 实现）**：当前 Phase 只定义常量 `MIN_SAMPLE_THRESHOLD=2`，实际过滤逻辑在 Phase 4 的 `app/memory/extractor.py` 中实现。
