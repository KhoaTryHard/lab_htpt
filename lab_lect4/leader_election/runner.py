from dataclasses import dataclass
import time
import threading

from .node import Config, Node
from .trace import election_counts


@dataclass(frozen=True)
class DemoResult:
    passed: bool
    failover_seconds: float
    leaders: dict[int, int | None]
    completed_rounds: int
    reason: str = ""


class Cluster:
    """Test/demo harness. Survivors never access this observer's state."""

    def __init__(self, addresses, trace, config=Config(), node_class=Node):
        self.trace = trace
        self.start_event = threading.Event()
        self.nodes = {node_id: node_class(node_id, addresses, self.start_event, trace, config)
                      for node_id in sorted(addresses)}

    def start(self, timeout=10.0):
        try:
            for node in self.nodes.values():
                node.start()
            deadline = time.monotonic() + timeout
            for node in self.nodes.values():
                if not node.ready.wait(max(0, deadline - time.monotonic())):
                    raise RuntimeError(f"P{node.node_id} listener did not become ready")
                if node.snapshot()["error"]:
                    raise RuntimeError(f"P{node.node_id}: {node.snapshot()['error']}")
            self.start_event.set()
        except BaseException:
            self.close()
            raise

    def snapshots(self, ids=None):
        return {node_id: self.nodes[node_id].snapshot() for node_id in
                (self.nodes if ids is None else ids)}

    def wait_for(self, predicate, timeout, description):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshots = self.snapshots()
            errors = [state["error"] for state in snapshots.values() if state["error"]]
            if errors:
                raise RuntimeError("; ".join(errors))
            if predicate(snapshots):
                return snapshots
            time.sleep(0.02)
        raise TimeoutError(description)

    def wait_bootstrap(self, timeout=10.0):
        initial = min(self.nodes)
        highest = max(self.nodes)
        result = self.wait_for(
            lambda states: all(state["running"] and state["leader_id"] == highest
                               and not state["active_rounds"] for state in states.values())
            and (initial, 1) in states[initial]["completed"],
            timeout, "Initial COORDINATOR circuit did not finish")
        self.trace.emit(None, "BOOTSTRAP_COMPLETE", f"All nodes agree on P{highest}; initial circuits drained")
        return result

    def crash(self, node_id):
        node = self.nodes[node_id]
        node.crash()
        node.join(2.0)
        if node.is_alive():
            raise RuntimeError(f"P{node_id} thread failed to stop")
        self.trace.emit(None, "CRASH", f"Active leader P{node_id} terminated; no peer notification", victim=node_id)

    def close(self):
        for node in self.nodes.values():
            node.crash()
        for node in self.nodes.values():
            if node.ident is not None:
                node.join(2.0)
                if node.is_alive():
                    raise RuntimeError(f"P{node.node_id} failed to shut down")


def run_demo(addresses, trace, config=Config(), stability_window=2.0, failover_timeout=15.0):
    cluster = Cluster(addresses, trace, config)
    started = None
    try:
        cluster.start()
        cluster.wait_bootstrap()
        cluster.crash(max(addresses))
        started = time.monotonic()
        survivors = tuple(node_id for node_id in addresses if node_id != max(addresses))
        winner = max(survivors)
        stable_since = None
        converged_at = None
        deadline = started + failover_timeout
        while time.monotonic() < deadline:
            states = cluster.snapshots(survivors)
            if any(state["error"] or not state["running"] for state in states.values()):
                raise RuntimeError("A surviving node stopped or reported an error")
            agreed = all(state["leader_id"] == winner and not state["active_rounds"]
                         for state in states.values())
            if agreed:
                if stable_since is None:
                    stable_since = time.monotonic()
                    converged_at = stable_since
                if time.monotonic() - stable_since >= stability_window:
                    records = trace.snapshot()
                    detected = any(record["event"] == "FAILURE_TIMEOUT" and
                                   record.get("suspected_leader") == max(addresses) for record in records)
                    if not detected:
                        raise RuntimeError("No autonomous failure timeout was observed")
                    rounds = [record for record in records if record["event"] == "ROUND_COMPLETE"
                              and record.get("leader_id") == winner]
                    leaders = {node_id: state["leader_id"] for node_id, state in states.items()}
                    result = DemoResult(True, converged_at - started, leaders, len(rounds))
                    trace.emit(None, "PASS", f"P{winner} stable for {stability_window:.1f}s; leaders={leaders}",
                               round_message_counts=election_counts(records), **result.__dict__)
                    return result
            else:
                stable_since = None
            time.sleep(0.02)
        raise TimeoutError(f"P{winner} did not stabilize within {failover_timeout}s")
    except Exception as exc:
        result = DemoResult(False, time.monotonic() - started if started else 0.0,
                            {node_id: state["leader_id"] for node_id, state in cluster.snapshots().items()},
                            0, str(exc))
        trace.emit(None, "FAIL", str(exc), **result.__dict__)
        return result
    finally:
        cluster.close()

