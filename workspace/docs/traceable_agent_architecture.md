# Traceable Agent Architecture

The runtime is a single local deployment. A planner creates a JSON plan, the
executor calls only registered read-only tools, and the trace store persists
inputs, summaries, status, latency, and errors. Evidence materialization then
normalizes source locations for the Markdown reporter.
