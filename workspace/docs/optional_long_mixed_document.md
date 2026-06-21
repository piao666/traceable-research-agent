# Long Mixed Research Document

## Part One: Control Before Execution

A research Agent becomes difficult to trust when task acceptance, model
planning, tool execution, and final prose happen in one opaque request. The
traceable design separates those stages. Task creation writes a pending run and
a plan. The plan can be deterministic or proposed by an LLM, but it is always
normalized before execution. A client may inspect tool names, arguments, risk
levels, and completion criteria before choosing to run. This first boundary is
important because visibility before execution is different from logging after
an irreversible action.

The planned path reads the complete plan and advances step by step. A known
tool order makes the path easy to reason about, and a report step can wait for
confirmation. The dynamic path instead uses a compact decision object. The
decision rationale is intentionally short; Action is constrained to enabled
allowed tools; Args remains subject to handler validation. These details are
separated across components so that changing a prompt does not change the SQL
parser or file whitelist.

Several paragraphs later, the audit consequence becomes visible. Each call
creates a trace with status and latency. The run record contains overall state,
while tool trace rows preserve individual evidence. A final report cites the
observations but does not replace them. When an interviewer opens the report
and asks where a claim came from, the Trace Viewer can show the source chunk,
query, fallback metadata, and error status.

## Part Two: Retrieval Is More Than One Score

Dense retrieval and lexical retrieval solve overlapping but distinct
problems. A trained embedding model can connect paraphrases, related concepts,
and multilingual wording. A sparse model can prioritize exact tokens such as
`same_tool_max_calls`, `RAG_RRF_K`, endpoint paths, or SQL operator names. A
hybrid system should not simply add cosine similarity to a BM25 score because
their scales have unrelated meanings.

The relevant fusion rule appears after this conceptual setup. Reciprocal Rank
Fusion uses list position: a candidate at rank r contributes `1/(k+r)`. If the
same chunk appears in both lists, its contributions accumulate. A high rank in
only one list can still preserve useful evidence, while agreement improves the
fused position. The metadata keeps dense rank, sparse rank, component scores,
and RRF score for inspection.

Chunking changes what either retriever can see. Small windows increase the
number of candidates and isolate precise sentences. They can also separate a
definition from the condition that qualifies it. Large windows maintain
continuity but may contain Agent architecture, SQL safety, and UI details in
one candidate. A query about one topic may retrieve the mixed chunk for a
strong unrelated term. Overlap helps at boundaries, but it increases index
size and duplicate evidence.

Pseudo comparison fields:

```text
chunk_size | granularity | continuity | candidate_count | possible_noise
256        | high        | lower      | high            | lower per chunk
512        | balanced    | balanced   | medium          | medium
1024       | lower       | high       | low             | higher per chunk
```

No row is universally best. The experiment must evaluate expected evidence on
a corpus with enough documents and boundary-sensitive queries. The old corpus
had one short document, so every top-five list almost necessarily contained
the correct source. Expanding documents and references improves the question,
but it does not guarantee that recall will differ. Honest saturation is a valid
result if it is explained.

## Part Three: SQL Safety Across Boundaries

Consider a task that asks for database evidence and then casually includes the
word delete. The planner may place `sql_query` in a plan, but the tool handler
still owns the final safety decision. SQLGlot parses the complete input. A
single SELECT or WITH query is accepted. DELETE, UPDATE, INSERT, DROP, ALTER,
CREATE, PRAGMA, ATTACH, DETACH, and VACUUM are rejected. A second keyword guard
provides defense in depth after parsing.

A multi-statement attack demonstrates why a prefix check is insufficient.
`SELECT 1; DROP TABLE documents` starts with a read operation but contains a
second destructive statement. Parsing the entire input exposes both. The tool
returns a structured safety rejection, and the trace status becomes rejected.
The database is unchanged, while the Agent still has an observation it can use
to explain the limitation or choose a safe alternative query.

The recovery example is deliberately separated from the parser description.
In ReAct mode, the next decision might issue `SELECT id, title FROM documents`
after observing the rejection. This is not a bypass: it is a new safe request
that passes the same parser. In planned mode, later evidence steps can continue
while the report lists the rejected SQL. Both paths preserve the original bad
case for audit.

## Part Four: External Evidence Without Write Access

GitHub evidence is useful for research, but a demo Agent does not need issue
creation or repository mutation. The adapter offers deterministic mock results
and optional public search. Public calls use GET. An optional token may be held
in local configuration, but neither cache records nor traces include the token.

Failures are classified. A timeout is different from invalid JSON; a rate
limit is different from a repository validation error. Bounded retry handles
transient cases. If fallback is enabled, the adapter returns mock evidence and
labels `data_source=fallback`. The label matters because a report should not
present fallback content as live GitHub data.

The MCP direction is also intentionally limited. The current component is a
read-only adapter rather than a server implementing every protocol feature.
HTTP POST, PUT, PATCH, and DELETE are denied. Future write capability would
need explicit discovery policy, human elevation, and trace persistence. It is
not enabled by changing a single environment flag.

## Part Five: UI, HITL, and Async Status

Streamlit demonstrates the backend contract without sharing its database. A
task form sends allowed tools and task text. A plan panel shows planner source.
The run panel chooses synchronous or BackgroundTasks execution. Trace rows are
rendered with status and latency, while a ReAct expander shows Thought, Action,
and Observation. Retrieval metadata displays whether Dense, BM25, or hybrid
mode was used.

HITL becomes visible when report writing requires approval. Status changes to
`waiting_human`; the pending action is persisted. Refreshing or calling async
run again does not bypass the guard. The UI sends a confirm request, and only
approval resumes execution. A rejected confirmation remains an explicit state
rather than an invisible button outcome.

BackgroundTasks provides local convenience, not durable job delivery. A
process restart can lose in-memory work. Production evolution would introduce
a queue and idempotent workers, but the demo honestly presents polling and
single-process limitations.

## Part Six: Evaluation and Interpretation

Engineering evidence has several layers. Smoke tests validate one behavior at
a time. Application eval combines stable cases and summarizes success, trace
completeness, safety hits, and visible failure. The planned/ReAct comparison
adds recovery_count, failed_tool_recovery_rate, avg_steps, latency, fallback
count, HITL success, and trace_quality_score.

Trace quality is structural rather than subjective. Tool ordering and complete
summaries matter. ReAct receives an additional point when its decision trace
contains Thought, Action, Observation, and explicit recovery or limitation.
The score is useful for regression but is not a blind human evaluation.

Docker uses lightweight dependencies so the health demo is not coupled to a
large local model. Real embedding evaluation is an explicit local run. The
environment switch for the chunk experiment must therefore be meaningful: a
true value requests SentenceTransformers through the shared backend factory,
and an unavailable model causes a clear failure. A false value selects the
deterministic baseline for CI reproducibility.

The final interpretation joins facts introduced far apart in this document.
Traceability is created by separating decisions from safe handlers and by
persisting outcomes. Retrieval credibility is created by comparing multiple
chunk sizes on diverse sources without forcing a winner. Operational honesty
is created by labeling fallback, optional models, local async limits, and
small-benchmark scope. These principles are more important than a perfect
Recall@5 value on a tiny corpus.
