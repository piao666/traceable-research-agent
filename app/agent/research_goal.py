"""Deterministic task requirements and conservative completion checks.

This is not an oracle for arbitrary research correctness. Explicit inability and
structured-data requests get hard checks; general prose still needs human review.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re


def build_task_contract(task: str, created_at: datetime | None = None) -> dict:
    anchor = (created_at or datetime.now(timezone.utc)).date()
    contract = {"version": "task-contract-v1", "as_of": anchor.isoformat(),
                "original_task": task, "goal_kind": "research", "period": None,
                "unresolved_fields": []}
    match = re.search(r"(?:近|最近|过去)\s*(\d{1,2}|十)\s*年|(?:last|past)\s+(\d{1,2})\s+years", task, re.I)
    if match:
        raw = match.group(1) or match.group(2)
        years = 10 if raw == "十" else int(raw)
        if years > 0:
            try:
                start = anchor.replace(year=anchor.year - years)
            except ValueError:
                start = anchor.replace(year=anchor.year - years, day=28)
            contract["period"] = {"start": start.isoformat(), "end": anchor.isoformat()}
    else:
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", task)
        if len(dates) >= 2:
            try:
                start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[1])
                if start <= end:
                    contract["period"] = {"start": start.isoformat(), "end": end.isoformat()}
            except ValueError:
                pass
    provider_review = re.search(r"(?:比较|评估|介绍|explain|compare|review).*(?:接口|平台|工具|API|provider|framework)", task, re.I)
    if (not provider_review and re.search(r"股票|股价|收盘价|stock|closing price", task, re.I)
            and re.search(r"涨幅|历史|数据|收盘价|historical|dataset|closing price", task, re.I)):
        contract["goal_kind"] = "price_series"
        contract["adjustment"] = next((label for pattern, label in (
            (r"不复权|unadjusted", "unadjusted"), (r"前复权", "forward_adjusted"),
            (r"后复权", "backward_adjusted"), (r"总回报|total return", "total_return"))
            if re.search(pattern, task, re.I)), None)
        codes = re.findall(r"(?<![A-Za-z0-9])\d{6}(?:\.(?:SZ|SH))?(?![A-Za-z0-9])", task, re.I)
        contract["instrument_codes"] = codes
        labels: list[str] = []
        for match in re.finditer(
                r"(?:^|[\s，。；;,:：])((?:我(?:需要|想要|想看)|请(?:查询|分析|获取)?|查询|获取|分析|查看)?\s*[\u4e00-\u9fff]{2,24})(?:股票|股价)",
                task):
            label = re.sub(r"^(?:我(?:需要|想要|想看)|请(?:查询|分析|获取)?|查询|获取|分析|查看)\s*", "", match.group(1)).strip()
            if label not in {"某", "某只", "该", "这只", "一只"} and len(label) >= 2:
                labels.append(label)
        contract["instrument_labels"] = list(dict.fromkeys(labels))
        if not contract["period"]:
            contract["unresolved_fields"].append("period")
        if contract["adjustment"] is None:
            contract["unresolved_fields"].append("adjustment")
        if not re.search(r"每年|年度|每日|日度|每月|月度|累计|annual|daily|monthly|cumulative", task, re.I):
            contract["unresolved_fields"].append("interval")
    return contract


def finish_failure(reason: str | None, summary: str = "", goal_status=None) -> str | None:
    if goal_status is not None and goal_status not in ("achieved", "not_met", "blocked", "needs_clarification", "failed"):
        return "goal_not_met"
    if goal_status in {"not_met", "blocked", "needs_clarification", "failed"}:
        return "goal_not_met"
    reason = (reason or "").lower().replace("_", " ")
    if any(term in reason for term in ("unavailable", "exhaust", "max steps", "no available",
                                       "invalid", "failed", "inaccessib", "cannot", "not met", "不可用", "耗尽", "无法完成", "未完成")):
        return "goal_not_met"
    # Detect explicit admission even if the model labels it achieved. Do not
    # search source text, which may legitimately describe somebody else's failure.
    if re.search(r"(?:task|request|extraction)\s+cannot\s+be\s+completed|"
                 r"(?:unable|cannot)\s+to\s+(?:complete|extract|obtain|retrieve)|"
                 r"no\s+[^.!?\n]{0,140}(?:dataset|price data|data series)[^.!?\n]{0,60}\b(?:found|available)|"
                 r"(?:未(?:能)?(?:找到|取得|获取|提取)|没有找到).{0,40}(?:数据|价格)|无法完成", summary, re.I):
        return "goal_not_met"
    return None


def structured_goal_failure(contract: dict, observations: list[dict]) -> str | None:
    if contract.get("goal_kind") != "price_series":
        return None
    if contract.get("unresolved_fields"):
        return "task_requirements_unresolved"
    # Readers provide tables from actual content, never from an LLM finish payload.
    period = contract.get("period")
    for obs in observations:
        if not obs.get("success") or obs.get("tool_name") not in {"web_fetcher", "file_reader", "sql_query"}:
            continue
        output = obs.get("output") or {}
        if not isinstance(output, dict):
            continue
        tables = [(table, str(output)) for table in output.get("tables") or []]
        if obs.get("tool_name") == "sql_query":
            tables.append((output, str(output)))
        for page in output.get("pages") or []:
            if isinstance(page, dict) and not page.get("error"):
                tables.extend((table, str(page)) for table in page.get("tables") or [])
        for table, source_text in tables:
            if not isinstance(table, dict) or table.get("truncated"):
                continue
            # Explicit basis and instrument must be present in tool-origin data,
            # never borrowed from the model's task/finish text.
            codes = contract.get("instrument_codes", [])
            code_terms = list(dict.fromkeys([term for code in codes for term in (code, code.split(".")[0])]))
            labels = contract.get("instrument_labels", [])
            if code_terms and not any(term.lower() in source_text.lower() for term in code_terms):
                continue
            if not code_terms and labels and not any(term.lower() in source_text.lower() for term in labels):
                continue
            columns = [str(col).lower() for col in table.get("columns") or []]
            basis_patterns = {"unadjusted": r"不复权|unadjusted", "forward_adjusted": r"前复权|forward.adjusted",
                              "backward_adjusted": r"后复权|backward.adjusted", "total_return": r"总回报|total.return"}
            normalized = [re.sub(r"[%％()（）\s]", "", re.sub(
                r"(?:unadjusted|forward.adjusted|backward.adjusted)[_\s]*|不复权|前复权|后复权", "", col))
                for col in columns]
            date_col = next((i for i, col in enumerate(columns) if re.fullmatch(r"date|日期|交易日期|时间", col)), None)
            wants_return = bool(re.search(r"涨幅|涨跌幅|return", contract.get("original_task", ""), re.I))
            value_col = next((i for i, col in enumerate(normalized) if re.fullmatch(
                r"涨幅|涨跌幅|return|change_pct|pct_change|return_pct|change_percent|total_return"
                if wants_return else r"close|收盘价?", col)), None)
            if date_col is None or value_col is None:
                continue
            # Do not borrow the basis of an unrelated column (e.g. unadjusted
            # open beside a forward-adjusted close). An explicit value-column
            # basis takes precedence over a generic table caption.
            column_basis = next((name for name, pattern in basis_patterns.items()
                                 if re.search(pattern, columns[value_col], re.I)), None)
            requested_basis = contract.get("adjustment")
            if requested_basis and (column_basis != requested_basis if column_basis else not re.search(
                    basis_patterns[requested_basis], str(table.get("caption") or ""), re.I)):
                continue
            dates = []
            for row in table.get("rows") or []:
                try:
                    values = [row.get(col) for col in table["columns"]] if isinstance(row, dict) else row
                    raw_day = str(values[date_col]).strip()
                    day = next((datetime.strptime(raw_day[:length], pattern).date()
                                for pattern, length in (("%Y-%m-%d", 10), ("%Y/%m/%d", 10), ("%Y%m%d", 8))
                                if _valid_date(raw_day[:length], pattern)), None)
                    if day is None:
                        continue
                    value = float(str(values[value_col]).replace("%", "").replace(",", ""))
                    if value != value or abs(value) == float("inf"):
                        continue
                    dates.append(day)
                except (ValueError, TypeError, IndexError, KeyError):
                    continue
            if len(set(dates)) < 2:
                continue
            if period:
                start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
                in_range = sorted(set(day for day in dates if start <= day <= end))
                if len(in_range) < 2 or (in_range[0] - start).days > 7 or (end - in_range[-1]).days > 7:
                    continue
                text = contract.get("original_task", "")
                max_gap = 14 if re.search(r"每日|日度|daily", text, re.I) else 62 if re.search(r"每月|月度|monthly", text, re.I) else 370
                if any((right - left).days > max_gap for left, right in zip(in_range, in_range[1:])):
                    continue
            # A table is necessary but not sufficient for a return computation.
            # Price-to-return calculation is a separate capability; never pretend
            # a table of closing prices already delivers a requested return series.
            return None
    return "structured_data_unavailable"


def _valid_date(value: str, pattern: str) -> bool:
    try:
        datetime.strptime(value, pattern)
        return True
    except ValueError:
        return False
