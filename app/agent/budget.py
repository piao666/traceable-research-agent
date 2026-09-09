"""Persistent root/child budgets with atomic admission and conservative accounting.

Limits stop NEW operations. Already-running calls retain their transport timeout;
this is not an interruptible process sandbox or a provider billing guarantee.
"""
from __future__ import annotations

import copy
import inspect
import json
import math
import time
from contextvars import ContextVar
from functools import wraps

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert

from app.llm.base import LLMClient
from app.trace import store
from app.trace.models import RunBudget

_active = ContextVar("research_budget", default=None)
_final_report = ContextVar("final_report_budget", default=False)


class BudgetExceeded(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("Research budget stopped: " + reason)


class FinalizationRequired(BudgetExceeded):
    """Signal a root research loop to stop discovery and spend its report reserve.

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


def estimate_message_tokens(messages, max_tokens: int) -> int:
    """Estimate prompt plus requested completion with per-message overhead."""

    prompt = sum(estimate_text_tokens(message.content) + 12 for message in messages)
    return prompt + max(0, int(max_tokens))


def limits(settings):
    config = {name: getattr(settings, "research_" + name) for name in (
        "max_tool_calls", "max_llm_calls", "max_tokens", "max_seconds", "max_estimated_cost",
        "tool_cost_estimate", "llm_cost_per_million_tokens")}
    config["final_report_tokens"] = min(8000, config["max_tokens"] // 10)
    config["final_report_llm_calls"] = min(2, config["max_llm_calls"] // 5)
    return config


def ensure_budget(db, run_id, settings, *, parent_run_id=None):
    root_id = run_id
    config = limits(settings)
    deadline = time.time() + config["max_seconds"]
    if parent_run_id is not None:
        parent = ensure_budget(db, parent_run_id, settings)
        root_id, config, deadline = parent.root_run_id, json.loads(parent.limits_json), parent.deadline
    db.execute(insert(RunBudget).values(run_id=run_id, root_run_id=root_id,
        limits_json=json.dumps(config, sort_keys=True), deadline=deadline, tool_calls=0, llm_calls=0,
        reserved_tokens=0, estimated_cost=0).on_conflict_do_nothing(index_elements=["run_id"]))
    db.commit()
    row = db.get(RunBudget, run_id, populate_existing=True)
    return db.get(RunBudget, row.root_run_id, populate_existing=True)


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
        token_limit = config["max_tokens"] - (0 if final or not llm else config.get("final_report_tokens", 0))
        llm_limit = config["max_llm_calls"] - (0 if final or not llm else config.get("final_report_llm_calls", 0))
        conditions = [RunBudget.run_id == self.root_id, RunBudget.stop_reason.is_(None),
            RunBudget.deadline > time.time(), RunBudget.tool_calls + tool <= config["max_tool_calls"],
            RunBudget.llm_calls + llm <= llm_limit,
            RunBudget.reserved_tokens + tokens <= token_limit]
        if config["max_estimated_cost"]:
            conditions.append(RunBudget.estimated_cost + cost <= config["max_estimated_cost"])
        admitted = self.db.execute(update(RunBudget).where(*conditions).values(
            tool_calls=RunBudget.tool_calls + tool, llm_calls=RunBudget.llm_calls + llm,
            reserved_tokens=RunBudget.reserved_tokens + tokens, estimated_cost=RunBudget.estimated_cost + cost))
        self.db.commit()
        if admitted.rowcount != 1:
            row = self.db.get(RunBudget, self.root_id, populate_existing=True)
            reason = row.stop_reason or ("deadline" if time.time() >= row.deadline else
                "tool_calls" if row.tool_calls + tool > config["max_tool_calls"] else
                "llm_calls" if row.llm_calls + llm > config["max_llm_calls"] else
                "tokens" if row.reserved_tokens + tokens > config["max_tokens"] else
                "finalization_reserve" if row.reserved_tokens + tokens > token_limit or row.llm_calls + llm > llm_limit else "estimated_cost")
            if reason == "finalization_reserve" and self.run_id == self.root_id and not final:
                raise FinalizationRequired(reason)
            self.stop(reason)

    def tool(self, name):
        cost = 0 if name in {"file_reader", "sql_query", "report_writer"} else self.limits["tool_cost_estimate"]
        if self.limits["max_estimated_cost"] and cost is None:
            self.stop("tool_price_unconfigured")
        self.reserve(tool=1, cost=cost or 0)

    def snapshot(self):
        return budget_snapshot(self.db, self.run_id)

    def can_deepen(self):
        self.reserve()
        row = self.snapshot()
        return (row["accounted_tokens"] + self.limits.get("final_report_tokens", 0) + 2000 < self.limits["max_tokens"]
                and row["llm_calls"] + self.limits.get("final_report_llm_calls", 0) + 2 < self.limits["max_llm_calls"])


def budget_snapshot(db, run_id):
    member = db.get(RunBudget, run_id, populate_existing=True)
    if member is None:
        return None
    row = db.get(RunBudget, member.root_run_id, populate_existing=True)
    config = json.loads(row.limits_json)
    return {"version": "shared-budget-v1", "root_run_id": member.root_run_id, "limits": config,
        "tool_calls": row.tool_calls, "llm_calls": row.llm_calls, "accounted_tokens": row.reserved_tokens,
        "estimated_cost": row.estimated_cost, "cost_currency": "CNY",
        "cost_evaluable": config["tool_cost_estimate"] is not None and config["llm_cost_per_million_tokens"] is not None,
        "deadline": row.deadline, "stop_reason": row.stop_reason}


def current_budget():
    return _active.get()


def reserve_tool(name):
    runtime = current_budget()
    if runtime is not None:
        runtime.tool(name)


class BudgetClient(LLMClient):
    def __init__(self, client):
        self.client = copy.copy(client)
        if hasattr(self.client, "max_retries"):
            self.client.max_retries = 0  # A reservation covers one provider attempt.

    def is_available(self):
        return self.client.is_available()

    def describe(self):
        return self.client.describe()

    def complete(self, messages, temperature=0.0, max_tokens=2000):
        runtime = current_budget()
        if runtime is None or not self.is_available():
            return self.client.complete(messages, temperature=temperature, max_tokens=max_tokens)
        # Missing provider usage keeps this conservative reservation charged;
        # known usage below reconciles it to the provider's actual accounting.
        reserved = estimate_message_tokens(messages, max_tokens)
        rate = runtime.limits["llm_cost_per_million_tokens"]
        if runtime.limits["max_estimated_cost"] and rate is None:
            runtime.stop("llm_price_unconfigured")
        cost = reserved * (rate or 0) / 1_000_000
        runtime.reserve(llm=1, tokens=reserved, cost=cost)
        response = self.client.complete(messages, temperature=temperature, max_tokens=max_tokens)
        actual = max(0, response.usage.total_tokens,
                     max(0, response.usage.prompt_tokens) + max(0, response.usage.completion_tokens)) if response.usage else 0
        if actual > 0:
            delta = actual - reserved
            runtime.db.execute(update(RunBudget).where(RunBudget.run_id == runtime.root_id).values(
                reserved_tokens=RunBudget.reserved_tokens + delta,
                estimated_cost=RunBudget.estimated_cost + delta * (rate or 0) / 1_000_000))
            runtime.db.commit()
            snapshot = runtime.snapshot()
            if snapshot["accounted_tokens"] > runtime.limits["max_tokens"]:
                runtime.stop("tokens")
            if runtime.limits["max_estimated_cost"] and snapshot["estimated_cost"] > runtime.limits["max_estimated_cost"]:
                runtime.stop("estimated_cost")
        return response


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
