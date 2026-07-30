# Phase 5 Implementation Plan

> 日期：2026-07-30  状态：设计中

---

## 一、阶段目标

按 TASK.md §7，分 4 个子任务：

1. **迭代深化** — ReAct 之上加 deepening loop，每轮 LLM 输出 learnings + follow_up_queries，默认 MAX_DEPTH=2
2. **行内引用** — Reporter 在正文中插入 `[CIT-XXX-XX]` 行内引用，Streamlit 点击弹证据卡片，Word/PDF 保留引用
3. **LLM 记忆蒸馏** — LLM 从 run 中蒸馏用户画像（默认关），配合向量召回增强记忆检索
4. **冲突仪表板** — Streamlit 报告页增加冲突区域，refutes/unresolved 并排对比

---

## 二、模块设计

### 2.1 迭代深化 — `app/agent/deepening.py`（新增）

```
deepening_loop(db, run_id, settings, llm_client) → dict

每轮:
  1. run_react_task() → 产生 observations + traces + learnings
  2. LLM 综合 learnings → {learnings: [...], follow_up_queries: [...]}
  3. follow_up_queries 作为新一轮 sub_queries
  4. 重复直到 MAX_DEPTH 或 LLM 返回空 follow_up_queries
  5. 每轮 learnings 写入 trace (deepening_round event)
  6. 最终 report 合并所有轮次证据
```

**配置项（新增）：**
- `DEEP_RESEARCH_MAX_DEPTH=2` — 最大深化轮数
- `DEEP_RESEARCH_BREADTH=3` — 每轮最多 follow-up 查询数
- `DEEP_RESEARCH_ENABLED=false` — 默认关闭

**离线降级：** 无 LLM → 退化为单轮（当前行为）

**上下文预算：** 扩展 `context_compressor.py`，`compress_deepening_context()` 按轮次从新到旧裁剪，超限时最早轮次的 learnings 只保留摘要。

### 2.2 行内引用 — Reporter + Streamlit

**Reporter 侧（`app/agent/reporter.py`）：**

当前 reporter 已有 `CIT-XXX-XX` 引用标签体系（Phase 3 建立），需要：
1. `_render_grouped_final_answer()` 中 claim 引用从 `[CIT-XXX-XX]` 改为 `[CIT-XXX-XX]` 作为可定位锚点
2. 报告末尾新增 `## 9. 引用索引` 章节：citation_label → passage text + source + content_basis + relation 映射表
3. LLM synthesis prompt 中要求 LLM 在事实性句子后插入 `[CIT-XXX-XX]`

**Streamlit 侧（`frontend/streamlit_app.py`）：**

1. 报告渲染时正则匹配 `[CIT-XXX-XX]` → 渲染为 `<span class="citation-badge">`
2. 点击 citation badge → 弹出 evidence card（popover/expander）：
   - 原文片段（passage text 前 500 字符）
   - 来源标题 + URL
   - content_basis 标记
   - relation（supports/refutes/contextualizes）
   - 六维可靠性评分
3. 引用索引从 provenance_bundle 的 citations + passages 构建

**导出保留（`app/agent/report_exporter.py`）：**
- Word: `[CIT-XXX-XX]` 保留为上标链接
- PDF: `[CIT-XXX-XX]` 保留为上标 + 末尾引用索引页

### 2.3 冲突仪表板 — Streamlit

在 Streamlit 报告 Tab 中新增冲突仪表板区域（报告 Markdown 渲染后）：

1. 从 provenance_bundle 提取 `resolutions` 中 `status in ("unresolved", "requires_human")` 的条目
2. 每条冲突展示：
   - Claim 文本
   - 双方证据并排对比（supports vs refutes）
   - 来源 + content_basis + 评分
   - 状态标记（已解决/未解决/待人工判断）
3. 复用 `_render_reasoning_markdown()` 已有数据

### 2.4 LLM 记忆蒸馏 + 向量召回

**LLM 蒸馏（`app/memory/extractor.py`）：**

1. 新增 `extract_preferences_with_llm(db, run, llm_client)` — 从 run 的 observations + plan 中 LLM 提炼画像
2. 启用条件：`MEMORY_LLM_EXTRACTION_ENABLED=true`（默认 false）
3. 蒸馏 prompt：输入 run.task + observations 摘要 → 输出 `{preferences: [{kind, content, confidence}]}`
4. 与规则提取并列：规则先跑，LLM 补充

**向量召回（`app/memory/retriever.py`）：**

1. `retrieve_memories(use_vector=True)` 已在 Phase 4 实现
2. 验证 `create_embedding_backend` 路径可用
3. 混合排序权重：keyword * 0.3 + vector * 0.7（已实现）

---

## 三、变更文件清单

### 新增（4 个）
| 文件 | 说明 |
|------|------|
| `app/agent/deepening.py` | Deepening loop 主逻辑 |
| `tests/test_phase5.py` | Phase 5 测试 |
| `docs/phase5_plan.md` | 本文件 |

### 修改（10 个）
| 文件 | 变更 |
|------|------|
| `app/config.py` | 新增 DEEP_RESEARCH_MAX_DEPTH, DEEP_RESEARCH_BREADTH, DEEP_RESEARCH_ENABLED, MEMORY_LLM_EXTRACTION_ENABLED |
| `app/agent/react_executor.py` | 集成 deepening loop 入口 |
| `app/agent/reporter.py` | 行内引用标记 + 引用索引章节；LLM synthesis 要求插入 citation labels |
| `app/agent/context_compressor.py` | `compress_deepening_context()` 轮次裁剪 |
| `app/memory/extractor.py` | `extract_preferences_with_llm()` LLM 蒸馏 |
| `app/memory/retriever.py` | 向量召回路径验证 + 索引 |
| `frontend/streamlit_app.py` | 可点击 citation badge + evidence card popover + 冲突仪表板 |
| `app/agent/report_exporter.py` | Word/PDF 中 citation 保留为上标链接 |
| `.env.example` | 新增配置变量 |
| `TASK.md` + `CLAUDE.md` | 记录 Phase 5 完成 |

---

## 四、实现顺序

| 步骤 | 内容 | 预估工作量 |
|------|------|-----------|
| 1 | config + deepening.py + context_compressor 扩展 | 最大块 |
| 2 | react_executor 集成 deepening loop | 小 |
| 3 | reporter 行内引用 + 引用索引 | 中 |
| 4 | report_exporter citation 保留 | 小 |
| 5 | Streamlit citation badge + evidence card + 冲突仪表板 | 中 |
| 6 | LLM 记忆蒸馏 (extractor.py) + 向量召回验证 | 中 |
| 7 | .env.example + 测试 | 中 |
| 8 | 验证：syntax → unit → smoke → commit | 收尾 |

---

## 五、关键设计决策

1. **Deepening 入口在 react_executor**：deepening 本质是 ReAct 的多轮封装，不改变 planned executor 的行为。仅在 `execution_mode=react` + `DEEP_RESEARCH_ENABLED=true` 时生效。

2. **行内引用复用 CIT-XXX-XX**：Phase 3 已建立的 citation_label 体系不变，Reporter 只需在渲染时加 CSS class 锚点，Streamlit 端做交互。

3. **冲突仪表板读已有数据**：`reasoning_service.py` 已产出 resolutions（status + confidence + support/refute counts），Streamlit 端直接展示，不需要新后端逻辑。

4. **LLM 蒸馏默认关**：遵循离线优先原则，规则提取为主，LLM 蒸馏为增强项。

5. **向量召回已实现**：Phase 4 retriever.py 已支持 `use_vector=True`，Phase 5 主要是验证 + 索引优化。
