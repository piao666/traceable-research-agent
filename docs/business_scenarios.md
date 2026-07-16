# Business Scenarios

Traceable Research Agent is positioned as a research operations agent, not only
as an observability demo. The system is useful when a team needs a sourced,
repeatable report instead of an opaque chat answer.

## Target Users

| User | Job To Be Done |
| --- | --- |
| Product manager | Compare competitors, pricing, releases, docs, and user-facing positioning. |
| Sales / BD | Prepare account briefs and meeting notes from public company/product signals. |
| Engineering lead | Evaluate tools, APIs, frameworks, and integration risks before adoption. |
| Analyst / operator | Merge public sources with internal notes, metrics, and knowledge-base evidence. |
| Reviewer / compliance stakeholder | Check which conclusions came from which tool calls and which claims are uncertain. |

## High-value Research Tasks

### Competitive Intelligence

Input:

```text
调研 OpenAI-compatible API 网关产品，比较定价、模型支持、限流、文档成熟度和迁移风险。
```

Expected workflow:

1. Discover sources with Tavily or Exa.
2. Read official pages, pricing pages, docs, and changelogs with Firecrawl.
3. Preserve failed, fallback, or missing pages in trace.
4. Generate a report with comparison tables and source links.

Business value:

- Reduces manual browser-copy-paste work.
- Keeps claims tied to source URLs.
- Makes stale or failed evidence visible before a decision.

### Technical Vendor Evaluation

Input:

```text
比较 FastAPI、LangGraph 和 MCP SDK 在企业 Agent 工具链落地中的适用边界。
```

Expected workflow:

1. Search GitHub, docs, and local notes.
2. Use RAG to retrieve internal architecture notes.
3. Summarize integration cost, maturity, risk, and recommended adoption path.

Business value:

- Turns scattered docs into a decision memo.
- Exposes technical tradeoffs instead of only listing features.
- Supports interview discussion around tool-chain integration.

### Sales / Account Research

Input:

```text
为一家 B2B SaaS 公司准备会前 briefing：业务线、近期动态、产品入口、潜在痛点和可切入话题。
```

Expected workflow:

1. Discover official pages and recent public sources.
2. Extract relevant product/company information.
3. Generate a concise account brief with uncertainty notes.

Business value:

- Speeds up repetitive pre-meeting research.
- Produces a consistent briefing format.
- Keeps public-source evidence available for review.

### Internal Experiment Review

Input:

```text
结合本地 RAG 实验记录、SQL 指标和外部资料，复盘当前 chunk size 策略是否可靠。
```

Expected workflow:

1. Read local experiment notes.
2. Query local metrics.
3. Retrieve related internal RAG notes.
4. Generate a review report with limitations.

Business value:

- Connects internal evidence with external context.
- Avoids treating a small demo benchmark as a production conclusion.
- Makes next experiment steps explicit.

## Why Trace Matters Commercially

Traceability is not only a technical feature. In business research, it answers:

- Which source supports this claim?
- Did the agent read the page or only search for it?
- Did any tool fail, timeout, or return fallback/mock data?
- Can another person rerun or audit this report later?
- Which conclusions should be treated as uncertain?

This is the difference between a one-off generated answer and a research
artifact that a team can use in a decision process.
