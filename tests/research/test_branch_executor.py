import json
from types import SimpleNamespace

from app.research.branch_executor import SerialPearExecutor


class _FakeNodeExecutor:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.calls = []

    def execute(self, _db, _scope, node, _settings, _client=None):
        self.calls.append(node.node_id)
        return {
            "node_id": node.node_id,
            "run_id": f"run-{node.node_id}",
            "status": next(self.statuses),
        }


def _node(node_id, depth, priority, required=True):
    return SimpleNamespace(
        node_id=node_id,
        depth=depth,
        priority=priority,
        metadata_json=json.dumps({"required": required}),
    )


def test_serial_pear_orders_nodes_and_never_overlaps():
    fake = _FakeNodeExecutor(["completed", "completed", "completed"])
    executor = SerialPearExecutor(fake)
    nodes = [_node("z", 1, 2), _node("a", 0, 9), _node("b", 1, 1)]

    results = executor.execute_serial(None, None, nodes, None)

    assert fake.calls == ["a", "b", "z"]
    assert [item["status"] for item in results] == ["completed"] * 3


def test_serial_pear_stops_on_required_waiting_node():
    fake = _FakeNodeExecutor(["waiting_human", "completed"])
    executor = SerialPearExecutor(fake)
    nodes = [_node("a", 0, 0), _node("b", 1, 0)]

    results = executor.execute_serial(None, None, nodes, None)

    assert fake.calls == ["a"]
    assert results[0]["status"] == "waiting_human"

