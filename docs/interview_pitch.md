# Interview Pitch

## 30-second Version

我做了一个可追踪的 Research Agent 后端。它不是普通 RAG 问答，而是把任务先规划成工具步骤，再通过 planned 或 ReAct 模式执行，并把每次工具调用的成功、失败、拒绝、延迟和 Observation 持久化，最后基于证据生成报告。项目还包含真实与 Hybrid RAG、HITL、SQL 只读校验、Auth、异步执行、Alembic 和 Streamlit 演示。

## 1-minute Version

这个项目解决的是 Agent “能运行但不可控、不可复盘”的问题。用户创建任务后，系统只生成并持久化 plan，不会自动执行；执行时可以选择稳定的 planned executor，或者使用 Thought/Action/Observation 动态决策的 ReAct executor。所有工具都经过 Tool Registry 和安全边界校验，结果写入 Trace DB，再由 Reporter 生成 Markdown 报告。RAG 同时支持轻量 deterministic、SentenceTransformers/Chroma、BM25 和 RRF Hybrid。工程上还补了 API Key、Tenant/User request context、BackgroundTasks 异步接口、Alembic、SQLGlot 和离线 GitHub fallback。最后我用 18 条任务做了 ReAct 与 planned 对比，ReAct 的失败恢复是 6/7，planned 是 1/7，但 planned 更快、更短。

## 3-minute Version

项目主链路是 `create task -> inspect plan -> run -> trace -> report`。创建任务和执行任务被刻意拆开：前者只写 pending run 和结构化 plan，便于审核；后者才调用工具。Planner 默认可以走确定性规则，也可以调用 Qwen/DeepSeek，任何非法 JSON、Schema 错误或 provider 不可用都会回退确定性 plan。

执行层保留了两种模式。planned executor 顺序执行完整 plan，适合稳定任务；ReAct executor 每一步根据任务、允许工具和历史 Observation 生成结构化 decision，并受 max steps、同工具调用次数、allowed tools 和 HITL 限制。失败、RAG 空结果、SQL 拒绝和 GitHub fallback 都会反馈到下一步，而不是直接让 API 500。

工具层使用 Registry 统一管理。文件读取限制目录；SQL 使用 SQLGlot 只允许单条 SELECT/WITH，并保留关键词二次防线；GitHub 只允许 GET，支持 mock、缓存、重试和 fallback；RAG 支持 deterministic/JSON、SentenceTransformers/Chroma、BM25 和 Dense+BM25 RRF。所有工具结果都会保存 trace，Reporter 根据 Observation 和 trace 生成可审计报告。

为了证明差异而不只展示功能，我设计了 18 条 planned/ReAct 对比任务。两种模式完成率都是 100%，planned 平均 1.278 步、826.514ms；ReAct 平均 2.611 步、1299.416ms，但失败恢复从 planned 的 1/7 提升到 6/7，Trace 质量也更高。这个结果说明两者不是替代关系：稳定短路径用 planned，复杂和异常场景才值得使用 ReAct。

## Resume Bullets

* 设计并实现 Traceable Research Agent 后端闭环，基于 FastAPI、SQLAlchemy 与 SQLite 完成任务创建、计划审核、工具执行、Trace 持久化、HITL 和证据报告生成。
* 构建结构化 LLM Planner 与可选 ReAct executor，引入 Thought/Action/Observation、allowed-tools 校验、max-steps/重复调用保护及确定性 fallback，保证异常输出不触发 API 500。
* 实现 deterministic/JSON、SentenceTransformers/Chroma、BM25 与 RRF Hybrid RAG，并通过 SQLGlot、路径白名单和 GitHub GET-only policy 建立工具安全边界。
* 补齐 Streamlit、API Key、Tenant/User context、BackgroundTasks、Alembic 及 smoke/eval；设计 18-case 对比实验，测得 ReAct 恢复 6/7、planned 恢复 1/7，并量化步骤、Trace 质量与延迟取舍。

## Common Questions

### 1. 这个项目和普通 RAG 有什么区别？

普通 RAG 重点是检索后回答；本项目重点是任务生命周期、工具执行、状态控制、Trace、HITL 和基于证据的报告。RAG 只是一个工具，不是整个系统。

### 2. 为什么要做 Trace？

Agent 的关键问题不是只看最终答案，而是知道调用了什么、为什么失败、是否被安全策略拒绝、是否发生 fallback。Trace 同时支持调试、审计、评测和面试演示。

### 3. LLM Planner 不稳定怎么办？

强制结构化 JSON 和 Schema 校验；未知工具、非法参数或 provider 异常都会回退确定性 Planner。创建任务仍不自动执行，因此 plan 可以先审查。

### 4. planned executor 和 ReAct executor 有什么区别？

planned 先生成完整 plan 再顺序执行；ReAct 每一步根据最新 Observation 决策下一步。前者路径短且稳定，后者更适合失败恢复。

### 5. ReAct 为什么没有完全替代 planned？

量化结果显示 ReAct 恢复更好，但步骤更多、延迟更高、依赖模型决策。简单稳定任务没有必要承担额外成本。

### 6. 工具失败时 Agent 怎么恢复？

工具结果被规范化为 Observation。ReAct 可以换工具、安全重试或带 limitation 结束；planned 会继续预定步骤并在报告中保留失败证据。

### 7. SQL 工具如何保证安全？

SQLGlot 解析后只接受单条 SELECT/WITH；拒绝 DDL、DML、PRAGMA、ATTACH、VACUUM 和多语句；关键词 guard 是第二道防线。

### 8. Real RAG 和 deterministic RAG 如何切换？

通过 embedding/vector backend 环境配置切换。默认 deterministic/JSON 离线稳定；显式启用后使用本地 SentenceTransformers 模型和 Chroma。

### 9. Hybrid RAG 为什么需要 BM25 + Dense？

Dense 擅长语义相似，BM25 擅长精确术语、标识符和关键词。RRF 用排名而非不可比原始分数融合两路结果。

### 10. 为什么使用 ChromaDB？

它适合本地持久化 demo、接入成本低，能与 SentenceTransformers 快速形成真实向量检索闭环。生产集群不是本项目范围。

### 11. 为什么没有直接做完整 MCP server？

当前目标是可追踪研究 Agent。先实现 GET-only adapter、allowlist 和 Trace 能证明边界；完整 MCP server 会扩大协议和运维范围，并引入写操作风险。

### 12. run_async 为什么使用 BackgroundTasks？

它满足单机 demo 的立即返回和轮询需求，且不引入队列基础设施。缺点是不持久、不跨进程，生产化应替换为 durable queue。

### 13. ReAct vs Planned 的实验结果说明了什么？

两者完成率都为 100%；ReAct 恢复 6/7、planned 1/7，且 ReAct Trace 质量更高；但 planned 更快、步骤更少。因此应按任务复杂度选择，而不是统一使用 ReAct。

### 14. 这个项目还有哪些不足？

没有生产前端、分布式任务队列、持久化多租户隔离、完整 MCP server 和大规模公开 benchmark；真实 LLM eval 也仍是可选项。
