# ReAct Execution Notes

ReAct is optional. Each decision, action, observation summary, bounded retry,
and finish state is persisted as a trace event. Invalid decisions fall back to
the deterministic planner when configured.
