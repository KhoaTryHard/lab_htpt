"""Real-process cluster control, shared by demos and integration tests.

The harness may inspect logs as test evidence. Running nodes never read another
node's files. Polls wait on observable conditions and always have deadlines.
"""

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import Config, PARTICIPANTS
from .protocol import envelope, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def available_ports():
    sockets = []
    try:
        for _ in range(4):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Cluster:
    def __init__(self, directory, *, request_timeout=0.4, vote_timeout=1.2,
                 retry_interval=0.15):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory / "config.json"
        self.config_path.write_text(json.dumps({
            "addresses": {name: {"host": "127.0.0.1", "port": port}
                          for name, port in zip(("COORD", *PARTICIPANTS), available_ports())},
            "data_dir": "data", "initial_values": {"x": 0},
            "request_timeout": request_timeout, "vote_timeout": vote_timeout,
            "retry_interval": retry_interval,
        }, indent=2), encoding="utf-8")
        self.config = Config.load(self.config_path)
        self.processes = {}
        self.outputs = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def output_path(self, node):
        return self.directory / f"{node}.stdout.log"

    def log(self, node):
        path = self.output_path(node)
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def diagnostics(self):
        return "\n".join(f"--- {n} ---\n{self.log(n)}" for n in ("COORD", *PARTICIPANTS))

    def start(self, node, failpoints=(), locked_keys=(), tx_id="101"):
        old = self.processes.get(node)
        if old is not None and old.poll() is None:
            raise RuntimeError(f"{node} is already running")
        previous_output = self.outputs.pop(node, None)
        if previous_output:
            previous_output.close()
        command = [sys.executable, "-u", "-m", "twopc",
                   "coordinator" if node == "COORD" else "participant",
                   "--config", str(self.config_path)]
        if node != "COORD":
            command += ["--id", node]
        for point in failpoints:
            command += ["--failpoint", point]
        if failpoints:
            command += ["--fault-tx", tx_id]
        for key in locked_keys:
            command += ["--lock-key", key]
        stream = self.output_path(node).open("ab")
        self.outputs[node] = stream
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.processes[node] = subprocess.Popen(
            command, cwd=PROJECT_ROOT, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=creationflags)
        self.wait(lambda: self.ping(node), f"{node} listen", timeout=10)

    def start_all(self, faults=None, conflicts=None):
        faults, conflicts = faults or {}, conflicts or {}
        for node in (*PARTICIPANTS, "COORD"):
            self.start(node, faults.get(node, ()), conflicts.get(node, ()))

    def stop(self, node):
        process = self.processes.get(node)
        if process is not None:
            if process.poll() is None:
                if sys.platform == "win32":
                    # Windows venv python.exe can be a launcher with an actual
                    # interpreter child. Killing only the launcher leaks servers
                    # and inherited stdout handles. Restrict termination to our PID tree.
                    result = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode and process.poll() is None:
                        raise RuntimeError(f"Cannot stop {node} process tree: {result.stderr!r}")
                else:
                    process.kill()
            process.wait(timeout=5)
        stream = self.outputs.pop(node, None)
        if stream:
            stream.close()

    def close(self):
        for node in list(self.processes):
            self.stop(node)

    def restart(self, node, **kwargs):
        self.stop(node)
        self.start(node, **kwargs)

    def rpc(self, node, kind, tx_id=None, **fields):
        return asyncio.run(request(self.config.address(node),
                                   envelope(kind, "CLIENT", tx_id, **fields),
                                   self.config.request_timeout))

    def ping(self, node):
        process = self.processes.get(node)
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{node} exited ({process.returncode}):\n{self.log(node)}")
        try:
            return self.rpc(node, "PING").get("type") == "PONG"
        except (OSError, TimeoutError, ValueError):
            return False

    def wait(self, predicate, description, timeout=12):
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = predicate()
                if result:
                    return result
            except (OSError, TimeoutError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise AssertionError(f"Timeout waiting for {description}\n{self.diagnostics()}")
            time.sleep(0.02)

    def status(self, node="COORD", tx_id="101"):
        return self.rpc(node, "STATUS", tx_id)

    def submit(self, tx_id="101", operations=None):
        return self.rpc("COORD", "SUBMIT", tx_id, operations=operations or sample_operations())

    def completed(self, tx_id="101"):
        def check():
            result = self.status(tx_id=tx_id)
            return result if result.get("completed") else None
        return self.wait(check, f"Tx {tx_id} complete")

    def crashed(self, node):
        from .nodes import CRASH_EXIT
        self.wait(lambda: self.processes[node].poll() is not None, f"{node} crash")
        require(self.processes[node].returncode == CRASH_EXIT,
                f"Unexpected exit for {node}:\n{self.log(node)}")

    def wal_records(self, node):
        path = self.config.data_dir / node / "wal.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def verify(self, decision, values, tx_id="101"):
        final = self.completed(tx_id)
        require(final["decision"] == decision, f"Wrong decision: {final}")
        for node, value in zip(PARTICIPANTS, values):
            status = self.status(node, tx_id)
            require(status["decision"] == decision, f"{node} disagrees: {status}")
            require(not status["locks"], f"{node} retained transaction locks")
            require(self.rpc(node, "GET", key="x")["value"] == value,
                    f"{node} has incorrect committed value")
        return final


def sample_operations(offset=0):
    return {node: [{"op": "SET", "key": "x", "value": i * 10 + offset}]
            for i, node in enumerate(PARTICIPANTS, 1)}


SCENARIOS = (
    "all-commit", "vote-abort", "vote-timeout", "participant-ready-crash",
    "coordinator-wait-crash", "coordinator-commit-crash", "coordinator-partial-crash",
    "participant-decision-crash", "lost-ack", "restart-all",
)


def exercise(cluster, scenario):
    """Run and assert a scenario; raises on any acceptance-criteria violation."""
    faults, conflicts = {}, {}
    mapping = {
        "vote-timeout": ("P3", "omit_vote"),
        "participant-ready-crash": ("P1", "after_prepare"),
        "coordinator-wait-crash": ("COORD", "before_decision"),
        "coordinator-commit-crash": ("COORD", "after_decision"),
        "coordinator-partial-crash": ("COORD", "after_first_decision"),
        "participant-decision-crash": ("P1", "after_decision"),
        "lost-ack": ("P1", "drop_ack"),
    }
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    if scenario in mapping:
        node, point = mapping[scenario]
        faults[node] = (point,)
    if scenario == "vote-abort":
        conflicts["P3"] = ("x",)
    cluster.start_all(faults, conflicts)
    try:
        response = cluster.submit()
        require(response["type"] == "STATUS", f"Submit failed: {response}")
    except (OSError, TimeoutError, ValueError):
        if not scenario.startswith("coordinator-"):
            raise
    expected = "GLOBAL_COMMIT"
    values = (10, 20, 30)
    if scenario in {"vote-abort", "vote-timeout", "participant-ready-crash",
                    "coordinator-wait-crash"}:
        expected, values = "GLOBAL_ABORT", (0, 0, 0)
    if scenario == "participant-ready-crash":
        cluster.crashed("P1")
        cluster.wait(lambda: cluster.status().get("decision") == "GLOBAL_ABORT",
                     "coordinator vote timeout")
        require(not cluster.status()["completed"], "Missing participant must prevent completion")
        cluster.start("P1")
    elif scenario == "participant-decision-crash":
        cluster.crashed("P1")
        cluster.start("P1")
    elif scenario.startswith("coordinator-"):
        cluster.crashed("COORD")
        if scenario == "coordinator-wait-crash":
            # Observe READY across several retry periods and beyond the vote timeout.
            deadline = time.monotonic() + cluster.config.vote_timeout * 1.1
            while time.monotonic() < deadline:
                for node in PARTICIPANTS:
                    status = cluster.status(node)
                    require(status["state"] == "READY" and status["locks"].get("x") == "101",
                            f"{node} failed to remain blocked: {status}")
                    require(cluster.rpc(node, "GET", key="x")["value"] == 0,
                            "Prepared data leaked through GET")
                time.sleep(0.02)
        cluster.start("COORD")
    final = cluster.verify(expected, values)
    if scenario == "restart-all":
        cluster.restart("P3", locked_keys=("x",))
        cluster.submit("102", sample_operations(1))
        cluster.verify("GLOBAL_ABORT", values, "102")
        cluster.close()
        cluster.start_all()
        cluster.verify("GLOBAL_COMMIT", values)
        cluster.verify("GLOBAL_ABORT", values, "102")
        cluster.submit("103", sample_operations(2))
        cluster.verify("GLOBAL_COMMIT", (12, 22, 32), "103")
    return final
