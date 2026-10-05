"""Persistent root/child budgets with atomic admission and conservative accounting.

Limits stop NEW operations. Already-running calls retain their transport timeout;
this is not an interruptible process sandbox or a provider billing guarantee.
"""
from __future__ import annotations

import inspect
import json
import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from app.llm.base import LLMClient
from app.trace import store
from app.trace.models import AgentRun, RunBudget

_active = ContextVar("research_budget", default=None)
_final_report = ContextVar("final_report_budget", default=False)
_LOCAL_ONLY_TOOLS = frozenset({"file_reader", "sql_query", "report_writer"})


class BudgetExceeded(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("Research budget stopped: " + reason)


class FinalizationRequired(BudgetExceeded):
    """Signal a research-scope node to stop discovery and preserve report reserve.

    This is deliberately non-terminal: unlike a hard budget breach it must not
    persist ``stop_reason`` before the root Run has had a chance to finalize.
    """


def estimate_text_tokens(value: str) -> int:
    """Conservative tokenizer-independent estimate without counting UTF-8 bytes.

    CJK characters commonly occupy one token while ordinary ASCII prose is
    closer to four characters per token.  A 20% margin covers JSON punctuation,
    code and tokenizer differences until a provider reports actual usage.
    """

    text = str(value or "")
    cjk = sum(
        1
        for char in text
        if ("\u3400" <= char <= "\u4dbf")
        or ("\u4e00" <= char <= "\u9fff")
        or ("\uf900" <= char <= "\ufaff")
    )
    non_ascii = sum(1 for char in text if ord(char) > 127) - cjk
    ascii_chars = len(text) - cjk - non_ascii
    estimate = math.ceil(ascii_chars / 4) + cjk + math.ceil(non_ascii / 2)
    return max(1, math.ceil(estimate * 1.2))


def estimate_message_tokens(messages, max_tokens: int | None) -> int:
    """Estimate prompt plus requested completion with per-message overhead."""

    prompt = sum(estimate_text_tokens(message.content) + 12 for message in messages)
    return prompt + max(0, int(max_tokens or 0))


def limits(settings):
    config = {name: getattr(settings, "research_" + name) for name in (
        "max_tool_calls", "max_llm_calls", "max_tokens", "max_seconds", "max_estimated_cost",
        "tool_cost_estimate", "llm_cost_per_million_tokens")}
    # A real Deep report may need a draft plus bounded evidence-based
    # revisions. The old 8k reserve was smaller than one requested 8192-token
    # completion, before accounting for its prompt. This only partitions the
    # existing hard limit; it does not increase the total token allowance.
    config["final_report_tokens"] = min(60000, config["max_tokens"] * 3 // 10)
    # Report drafting and quote-guarded multilingual citation review both
    # consume logical model calls. Reserve several within the same hard cap.
    # Three writer attempts plus up to four citation batches per attempt.
    # Keep one extra final-check call while leaving most calls for research.
    config["final_report_llm_calls"] = min(16, config["max_llm_calls"] // 3)
    # Preserve a bounded slice of the existing wall-clock and estimated-cost
    # caps for final report generation and its citation/reference checks.
    # These are reservations inside the configured caps, never extra capacity.
    config["final_report_seconds"] = min(120.0, config["max_seconds"] * 0.2)
    config["final_report_cost"] = (
        config["max_estimated_cost"] * 0.2 if config["max_estimated_cost"] else 0.0
    )
    return config


def ensure_budget(db, run_id, settings, *, parent_run_id=None):
    root_id = run_id
    config = limits(settings)
    deadline = time.time() + config["max_seconds"]
    if parent_run_id is not None:
        parent = ensure_budget(db, parent_run_id, settings)
        root_id, config, deadline = parent.root_run_id, json.loads(parent.limits_json), parent.deadline
    db.execute(
        insert(RunBudget)
        .values(
            run_id=run_id,
            root_run_id=root_id,
            limits_json=json.dumps(config, sort_keys=True),
            deadline=deadline,
            tool_calls=0,
            llm_calls=0,
            provider_attempts=0,
            reserved_tokens=0,
            estimated_cost=0,
        )
        .on_conflict_do_nothing(index_elements=["run_id"])
    )
    db.commit()
    row = db.get(RunBudget, run_id, populate_existing=True)
    return db.get(RunBudget, row.root_run_id, populate_existing=True)


@contextmanager
def planning_budget(db, run_id, settings):
    """Install the persisted root budget while a new task is being planned.

    API planning runs before the normal execution wrapper. This context makes
    its planner, decomposer, and repair clients share the same atomic ledger.
    Nested use for the same Run reuses the existing runtime and always restores
    the prior context on exit.
    """

    active = current_budget()
    if active is not None and active.run_id == run_id:
        yield active
        return
    runtime = BudgetRuntime(db, run_id, settings)
    token = _active.set(runtime)
    try:
        yield runtime
    finally:
        _active.reset(token)


def _run_plan_and_budget(db, run_id: str):
    run = store.get_fresh_agent_run(db, run_id)
    if run is None:
        return None, None
    try:
        plan = json.loads(run.plan_json or "{}")
    except (TypeError, json.JSONDecodeError):
        plan = {}
    return run, plan if isinstance(plan, dict) else {}


def pause_budget_deadline(db, run_id: str, *, now: float | None = None) -> bool:
    """Persist the start of a human wait so its wall time is excluded once.

    Only a Run with an existing ledger and a waiting status can pause. The
    marker lives in plan JSON for migration-free persistence and is retained
    if the run is cancelled or its budget has already stopped.
    """

    run, plan = _run_plan_and_budget(db, run_id)
    if run is None or run.status not in {"waiting_human", "waiting_human_plan"}:
        return False
    member_budget = db.get(RunBudget, run_id, populate_existing=True)
    if member_budget is None:
        return False
    root_id = member_budget.root_run_id
    root_run = store.get_fresh_agent_run(db, root_id)
    root_budget = db.get(RunBudget, root_id, populate_existing=True)
    if (root_run is None or root_budget is None or root_budget.root_run_id != root_id
            or root_run.status in {"cancelled", "failed", "completed", "incomplete"}
            or root_budget.stop_reason is not None):
        return False
    # A child can return while its parent still has work to dispatch. Exclude
    # only the interval when the scope itself has stopped for human input.
    if run_id != root_id and root_run.status not in {"waiting_human", "waiting_human_plan"}:
        return False
    waiter_ids = [run_id]
    if run_id == root_id:
        from app.research.models import ResearchNode, ResearchScope
        child_waiters = list(db.scalars(
            select(ResearchNode.run_id)
            .join(ResearchScope, ResearchNode.scope_id == ResearchScope.scope_id)
            .where(ResearchScope.root_run_id == root_id,
                   ResearchNode.run_id != root_id,
                   ResearchNode.status.in_(["waiting_human", "waiting_human_plan"]))
        ))
        # PEAR projects child waits onto the root. Approval belongs to those
        # children; registering the projection would leave an orphan pause.
        if child_waiters:
            waiter_ids = sorted(set(child_waiters))
    started_at = float(time.time() if now is None else now)
    if not math.isfinite(started_at) or started_at >= root_budget.deadline:
        return False
    try:
        root_plan = json.loads(root_run.plan_json or "{}")
    except (TypeError, json.JSONDecodeError):
        root_plan = {}
    if not isinstance(root_plan, dict):
        return False
    marker = root_plan.get("execution_budget_pause")
    if marker is None:
        marker = {
            "started_at": started_at,
            "root_run_id": root_id,
            "waiting_run_ids": waiter_ids,
        }
    elif isinstance(marker, dict) and marker.get("root_run_id") == root_id:
        waiters = marker.get("waiting_run_ids")
        if not isinstance(waiters, list):
            return False
        if all(waiter in waiters for waiter in waiter_ids):
            return False
        marker = {**marker, "waiting_run_ids": list(dict.fromkeys([*waiters, *waiter_ids]))}
    else:
        return False
    root_plan["execution_budget_pause"] = marker
    serialized = json.dumps(root_plan, ensure_ascii=False, default=str)
    changed = db.execute(
        update(AgentRun)
        .where(AgentRun.run_id == root_id, AgentRun.plan_json == root_run.plan_json)
        .values(plan_json=serialized)
    )
    if changed.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def resume_budget_deadline(db, run_id: str, *, now: float | None = None) -> bool:
    """Extend the shared deadline by one persisted human-wait interval.

    The plan marker is consumed with a compare-and-swap update in the same
    transaction as the deadline adjustment, making retries idempotent. A
    cancelled/terminal Run or stopped budget is never revived or extended.
    """

    run, _plan = _run_plan_and_budget(db, run_id)
    if run is None:
        return False
    if run.status in {"cancelled", "failed", "completed", "incomplete"}:
        return False
    member_budget = db.get(RunBudget, run_id, populate_existing=True)
    if member_budget is None:
        return False
    root_run_id = member_budget.root_run_id
    root_run = store.get_fresh_agent_run(db, root_run_id)
    if root_run is None or root_run.status in {"cancelled", "failed", "completed", "incomplete"}:
        return False
    try:
        root_plan = json.loads(root_run.plan_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    marker = root_plan.get("execution_budget_pause") if isinstance(root_plan, dict) else None
    if not isinstance(marker, dict) or marker.get("root_run_id") != root_run_id:
        return False
    started_at = marker.get("started_at")
    waiters = marker.get("waiting_run_ids")
    if (not isinstance(started_at, (int, float)) or isinstance(started_at, bool)
            or not math.isfinite(float(started_at)) or not isinstance(waiters, list)
            or run_id not in waiters):
        return False
    resumed_at = float(time.time() if now is None else now)
    if not math.isfinite(resumed_at) or resumed_at < float(started_at):
        return False
    root_budget = db.get(RunBudget, root_run_id, populate_existing=True)
    if root_budget is None or root_budget.root_run_id != root_run_id:
        return False
    if float(started_at) >= root_budget.deadline:
        return False
    if root_budget.stop_reason is not None:
        return False
    remaining_waiters = [waiter for waiter in waiters if waiter != run_id]
    if remaining_waiters:
        marker = {**marker, "waiting_run_ids": remaining_waiters}
        root_plan["execution_budget_pause"] = marker
    else:
        root_plan.pop("execution_budget_pause", None)
    paused_seconds = max(0.0, resumed_at - float(started_at)) if not remaining_waiters else 0.0
    serialized = json.dumps(root_plan, ensure_ascii=False, default=str)
    changed_plan = db.execute(
        update(AgentRun)
        .where(AgentRun.run_id == root_run_id, AgentRun.plan_json == root_run.plan_json)
        .values(plan_json=serialized)
    )
    if changed_plan.rowcount != 1:
        db.rollback()
        return False
    changed_budget = db.execute(
        update(RunBudget)
        .where(RunBudget.run_id == root_run_id, RunBudget.stop_reason.is_(None))
        .values(deadline=RunBudget.deadline + paused_seconds)
    )
    if changed_budget.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


class BudgetRuntime:
    def __init__(self, db, run_id, settings):
        self.db, self.run_id = db, run_id
        root = ensure_budget(db, run_id, settings)
        self.root_id = root.root_run_id
        self.limits = json.loads(root.limits_json)

    def stop(self, reason):
        self.db.execute(update(RunBudget).where(RunBudget.run_id == self.root_id,
            RunBudget.stop_reason.is_(None)).values(stop_reason=reason))
        self.db.commit()
        raise BudgetExceeded(reason)

    def reserve(self, *, tool=0, llm=0, tokens=0, cost=0):
        root_run = store.get_fresh_agent_run(self.db, self.root_id)
        run = store.get_fresh_agent_run(self.db, self.run_id)
        if (root_run and root_run.status == "cancelled") or (run and run.status == "cancelled"):
            raise BudgetExceeded("parent_cancelled")
        if root_run and self.run_id != self.root_id and root_run.status in {"failed", "completed"}:
            raise BudgetExceeded("parent_terminal")
        config = self.limits
        final = _final_report.get() and self.run_id == self.root_id
        llm_limit = config["max_llm_calls"] - (0 if final or not llm else config.get("final_report_llm_calls", 0))
        token_limit = config["max_tokens"] - (0 if final else config.get("final_report_tokens", 0))
        cost_limit = config["max_estimated_cost"]
        if cost_limit and not final:
            cost_limit = max(0.0, cost_limit - config.get("final_report_cost", 0.0))
        now = time.time()
        deadline_limit = now + (0 if final else config.get("final_report_seconds", 0.0))
        conditions = [RunBudget.run_id == self.root_id, RunBudget.stop_reason.is_(None),
            RunBudget.deadline > deadline_limit, RunBudget.tool_calls + tool <= config["max_tool_calls"],
            RunBudget.llm_calls + llm <= llm_limit,
            RunBudget.reserved_tokens + tokens <= token_limit,
        ]
        if config["max_estimated_cost"]:
            conditions.append(RunBudget.estimated_cost + cost <= cost_limit)
        admitted = self.db.execute(update(RunBudget).where(*conditions).values(
            tool_calls=RunBudget.tool_calls + tool, llm_calls=RunBudget.llm_calls + llm,
            reserved_tokens=RunBudget.reserved_tokens + tokens,
            estimated_cost=RunBudget.estimated_cost + cost))
        self.db.commit()
        if admitted.rowcount != 1:
            row = self.db.get(RunBudget, self.root_id, populate_existing=True)
            if row.stop_reason:
                raise BudgetExceeded(row.stop_reason)
            if now >= row.deadline:
                self.stop("deadline")
            if not final and now + config.get("final_report_seconds", 0.0) >= row.deadline:
                raise FinalizationRequired("deadline")
            if row.tool_calls + tool > config["max_tool_calls"]:
                self.stop("tool_calls")
            if row.llm_calls + llm > llm_limit:
                if not final and row.llm_calls + llm <= config["max_llm_calls"]:
                    raise FinalizationRequired("llm_calls")
                self.stop("llm_calls")
            if row.reserved_tokens + tokens > token_limit:
                if not final and row.reserved_tokens + tokens <= config["max_tokens"]:
                    raise FinalizationRequired("tokens")
                self.stop("tokens")
            if config["max_estimated_cost"] and row.estimated_cost + cost > cost_limit:
                if not final and row.estimated_cost + cost <= config["max_estimated_cost"]:
                    raise FinalizationRequired("estimated_cost")
                self.stop("estimated_cost")
            self.stop("admission_rejected")

    def tool(self, name):
        # Internal reference-index calls use ``reference_index:<name>`` and are
        # deliberately chargeable just like other real external tool calls.
        cost = 0 if name in _LOCAL_ONLY_TOOLS else self.limits["tool_cost_estimate"]
        if self.limits["max_estimated_cost"] and cost is None:
            self.stop("tool_price_unconfigured")
        self.reserve(tool=1, cost=cost or 0)

    def record_provider_attempts(self, attempts: int) -> None:
        """Record physical provider attempts separately from logical LLM calls."""

        count = max(0, int(attempts))
        if not count:
            return
        self.db.execute(
            update(RunBudget)
            .where(RunBudget.run_id == self.root_id)
            .values(provider_attempts=RunBudget.provider_attempts + count)
        )
        self.db.commit()

    def snapshot(self):
        return budget_snapshot(self.db, self.run_id)

    def can_deepen(self, *, required_llm_calls: int = 2, required_tokens: int = 0):
        try:
            self.reserve()
        except FinalizationRequired:
            return False
        row = self.snapshot()
        return (
            row["llm_calls"] + self.limits.get("final_report_llm_calls", 0)
            + max(0, required_llm_calls) <= self.limits["max_llm_calls"]
            and row["accounted_tokens"] + self.limits.get("final_report_tokens", 0)
            + max(0, required_tokens) <= self.limits["max_tokens"]
        )


def budget_snapshot(db, run_id):
    member = db.get(RunBudget, run_id, populate_existing=True)
    if member is None:
        return None
    row = db.get(RunBudget, member.root_run_id, populate_existing=True)
    config = json.loads(row.limits_json)
    return {"version": "shared-budget-v1", "root_run_id": member.root_run_id, "limits": config,
        "tool_calls": row.tool_calls, "llm_calls": row.llm_calls,
        "provider_attempts": row.provider_attempts,
        "accounted_tokens": row.reserved_tokens,
        "estimated_cost": row.estimated_cost, "cost_currency": "CNY",
        "cost_evaluable": config["tool_cost_estimate"] is not None and config["llm_cost_per_million_tokens"] is not None,
        "deadline": row.deadline, "stop_reason": row.stop_reason}


def current_budget():
    return _active.get()


def final_report_evidence_token_budget() -> int:
    """Return the fixed evidence share of the protected final-report budget."""

    runtime = current_budget()
    final_report_tokens = int(
        runtime.limits.get("final_report_tokens", 8000) if runtime is not None else 8000
    )
    # Keep the frozen evidence projection bounded independently of the larger
    # report/revision reserve, or more headroom would just admit more prompt.
    return min(5600, int(final_report_tokens * 0.7))


def reserve_tool(name):
    runtime = current_budget()
    if runtime is not None:
        runtime.tool(name)


class BudgetClient(LLMClient):
    def __init__(self, client):
        # Provider retries are an adapter concern.  The budget reserves one
        # logical call and records the adapter's bounded physical attempts.
        self.client = client

    def is_available(self):
        return self.client.is_available()

    def describe(self):
        return self.client.describe()

    def complete(self, messages, temperature=0.0, max_tokens=2000):
        return self._complete_with(self.client.complete, messages, temperature, max_tokens)

    def structured_complete(self, messages, temperature=0.0, max_tokens=2000):
        if hasattr(self.client, "structured_complete"):
            return self._complete_with(self.client.structured_complete, messages, temperature, max_tokens)
        # Old duck-typed clients without structured_complete: fall back to the
        # base-class implementation that calls complete() then validates JSON.
        return LLMClient.structured_complete(self, messages, temperature=temperature, max_tokens=max_tokens)

    def _complete_with(self, method, messages, temperature, max_tokens):
        runtime = current_budget()
        if runtime is None or not self.is_available():
            return method(messages, temperature=temperature, max_tokens=max_tokens)
        rate = runtime.limits["llm_cost_per_million_tokens"]
        if runtime.limits["max_estimated_cost"] and rate is None:
            runtime.stop("llm_price_unconfigured")
        estimated_tokens = estimate_message_tokens(messages, max_tokens)
        estimated_call_cost = estimated_tokens * (rate or 0) / 1_000_000
        runtime.reserve(llm=1, tokens=estimated_tokens, cost=estimated_call_cost)
        response = method(messages, temperature=temperature, max_tokens=max_tokens)
        runtime.record_provider_attempts(_provider_attempt_count(response))
        actual = max(0, response.usage.total_tokens,
                     max(0, response.usage.prompt_tokens) + max(0, response.usage.completion_tokens)) if response.usage else 0
        if actual > 0:
            runtime.db.execute(update(RunBudget).where(RunBudget.run_id == runtime.root_id).values(
                reserved_tokens=RunBudget.reserved_tokens + actual - estimated_tokens,
                estimated_cost=RunBudget.estimated_cost + (actual - estimated_tokens) * (rate or 0) / 1_000_000))
            runtime.db.commit()
            snapshot = runtime.snapshot()
            if snapshot["accounted_tokens"] > runtime.limits["max_tokens"]:
                runtime.stop("tokens")
            if runtime.limits["max_estimated_cost"] and snapshot["estimated_cost"] > runtime.limits["max_estimated_cost"]:
                runtime.stop("estimated_cost")
            final = _final_report.get() and runtime.run_id == runtime.root_id
            if not final and snapshot["accounted_tokens"] > (
                runtime.limits["max_tokens"] - runtime.limits.get("final_report_tokens", 0)
            ):
                raise FinalizationRequired("tokens")
            if (not final and runtime.limits["max_estimated_cost"]
                    and snapshot["estimated_cost"] > (
                        runtime.limits["max_estimated_cost"]
                        - runtime.limits.get("final_report_cost", 0.0)
                    )):
                raise FinalizationRequired("estimated_cost")
        return response


def _provider_attempt_count(response) -> int:
    response_metadata = getattr(response, "metadata", None)
    metadata = response_metadata if isinstance(response_metadata, dict) else {}
    explicit = metadata.get("provider_attempts")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return max(1, explicit)
    attempt = metadata.get("attempt")
    if isinstance(attempt, int) and not isinstance(attempt, bool):
        return max(1, attempt)
    retry_count = metadata.get("retry_count")
    if isinstance(retry_count, int) and not isinstance(retry_count, bool):
        return max(1, retry_count + 1)
    return 1


def budget_client(client):
    if client is None or isinstance(client, BudgetClient) or current_budget() is None:
        return client
    return BudgetClient(client)


def report_budget(function):
    @wraps(function)
    def wrapped(run, plan, *args, **kwargs):
        runtime = current_budget()
        # A full retry has a lineage parent but owns a NEW root ledger. Only
        # actual shared-budget children are excluded from finalization headroom.
        root = runtime is not None and runtime.run_id == runtime.root_id
        final = (root and not plan.get("adaptive_gate_pending")
                 and (not plan.get("deepening_pending") or plan.get("deepening_phase") == "finalizing"))
        token = _final_report.set(final)
        try:
            return function(run, plan, *args, **kwargs)
        finally:
            _final_report.reset(token)
    return wrapped


@contextmanager
def acquisition_budget():
    """A report's recovery fetch must not consume protected finalization funds."""
    token = _final_report.set(False)
    try:
        yield
    finally:
        _final_report.reset(token)


def budgeted_execution(function):
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        db, run_id = bound.arguments["db"], bound.arguments["run_id"]
        run = store.get_fresh_agent_run(db, run_id)
        active = current_budget()
        if run is None or run.status in {"completed", "failed", "cancelled", "waiting_human", "waiting_human_plan"} or (
            active is not None and active.run_id == run_id):
            return function(*args, **kwargs)
        settings = bound.arguments.get("settings_obj") or bound.arguments.get("settings")
        runtime = BudgetRuntime(db, run_id, settings)
        token = _active.set(runtime)
        try:
            try:
                result = function(*args, **kwargs)
            except BudgetExceeded as exc:
                result = {"run_id": run_id, "status": "failed", "message": str(exc)}
                if exc.reason == "parent_cancelled":
                    store.update_agent_run_status(db, run_id, "cancelled", "Parent research was cancelled.")
                else:
                    runtime.db.execute(update(RunBudget).where(RunBudget.run_id == runtime.root_id).values(stop_reason=exc.reason))
                    runtime.db.commit()
            waiting_run = store.get_fresh_agent_run(db, run_id)
            if waiting_run and waiting_run.status in {"waiting_human", "waiting_human_plan"}:
                pause_budget_deadline(db, run_id)
            snapshot = runtime.snapshot()
            fresh = store.get_fresh_agent_run(db, run_id)
            root_run = store.get_fresh_agent_run(db, runtime.root_id)
            if fresh and root_run and root_run.status == "cancelled" and fresh.status != "cancelled":
                fresh = store.update_agent_run_status(db, run_id, "cancelled", "Parent research was cancelled.")
                result.update(status="cancelled", error_message=fresh.error_message)
            if fresh and snapshot["stop_reason"] and fresh.status != "cancelled":
                from app.agent.outcome import fail_execution
                fresh = fail_execution(db, run_id, BudgetExceeded(snapshot["stop_reason"]))
                result.update(status=fresh.status, error_message=fresh.error_message)
            if fresh:
                plan = json.loads(fresh.plan_json or "{}")
                plan["execution_budget"] = snapshot
                store.replace_agent_run_plan(db, run_id, plan)
                # Budget exceptions must preserve the full public Run contract,
                # including counters/URLs and the final structured error.
                from app.agent.executor import _summary
                result = {**result, **_summary(fresh)}
            return result
        finally:
            _active.reset(token)
    return wrapped
