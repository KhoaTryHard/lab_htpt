import socket
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from leader_election.node import Config, Node
from leader_election.runner import Cluster, run_demo
from leader_election.trace import Trace, election_counts

FAST = Config(heartbeat_interval=0.05, failure_timeout=0.25,
              socket_timeout=0.1, election_timeout=2)


def free_addresses():
    sockets = []
    try:
        for _ in range(5):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return {index: sock.getsockname() for index, sock in enumerate(sockets, 1)}
    finally:
        for sock in sockets:
            sock.close()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.trace = Trace(console=False)
        self.cluster = None

    def tearDown(self):
        if self.cluster:
            self.cluster.close()
        self.trace.close()

    def start_cluster(self, config=FAST, node_class=Node, addresses=None):
        self.cluster = Cluster(addresses or free_addresses(), self.trace, config, node_class)
        self.cluster.start()
        return self.cluster.wait_bootstrap()

    def test_bootstrap_complete_circuit_and_real_socket_counts(self):
        states = self.start_cluster()
        self.assertEqual({state["leader_id"] for state in states.values()}, {5})
        self.assertIn((1, 1), states[1]["completed"])
        self.assertTrue(all(not state["active_rounds"] for state in states.values()))
        self.assertEqual(election_counts(self.trace.snapshot()), {"P1:1": 10})

    def test_required_demo_and_same_ports_can_run_again(self):
        addresses = free_addresses()
        for iteration in range(2):
            with self.subTest(iteration=iteration):
                trace = Trace(console=False)
                try:
                    result = run_demo(addresses, trace, FAST, stability_window=0.25, failover_timeout=5)
                    self.assertTrue(result.passed, result.reason)
                    self.assertEqual(result.leaders, {1: 4, 2: 4, 3: 4, 4: 4})
                    records = trace.snapshot()
                    stopped = next(i for i, r in enumerate(records) if r["node"] == 5 and r["event"] == "STOPPED")
                    crashed = next(i for i, r in enumerate(records) if r["event"] == "CRASH")
                    self.assertLess(stopped, crashed)
                    self.assertFalse(any(r["node"] == 5 for r in records[crashed + 1:]))
                    self.assertTrue(any(r["event"] == "FAILURE_TIMEOUT" for r in records[crashed + 1:]))
                    self.assertTrue(any(r["node"] == 4 and r["event"] == "BYPASS" and
                                        r["destination"] == 5 for r in records))
                    for record in records:
                        if record["event"] == "ROUND_COMPLETE" and record["leader_id"] == 4:
                            self.assertEqual(set(record["participants"]), {1, 2, 3, 4})
                            key = f"P{record['election_id'][0]}:{record['election_id'][1]}"
                            self.assertEqual(election_counts(records)[key], 8)
                finally:
                    trace.close()

    def test_four_concurrent_timeout_origins_finish_independently(self):
        barrier = threading.Barrier(4)

        class ConcurrentNode(Node):
            def _start_election(self, reason):
                if reason == "heartbeat-timeout" and self.local_counter <= 1:
                    barrier.wait(timeout=3)
                super()._start_election(reason)

        self.start_cluster(node_class=ConcurrentNode)
        self.cluster.crash(5)
        states = self.cluster.wait_for(
            lambda views: all(views[node]["leader_id"] == 4 and not views[node]["active_rounds"]
                              and any(round_id != (1, 1) for round_id in views[node]["completed"])
                              for node in range(1, 5)), 6, "Concurrent rounds did not all finish")
        self.assertFalse(self.cluster.nodes[5].is_alive())
        for node in range(1, 5):
            self.assertEqual(states[node]["timeouts"], 1)
        records = self.trace.snapshot()
        rounds = [r for r in records if r["event"] == "ROUND_COMPLETE" and r["leader_id"] == 4]
        self.assertEqual({r["node"] for r in rounds}, {1, 2, 3, 4})
        self.assertEqual(len(rounds), 4)
        counts = election_counts(records)
        for record in rounds:
            key = f"P{record['election_id'][0]}:{record['election_id'][1]}"
            self.assertEqual(counts[key], 8)
        p2 = next(r for r in rounds if r["node"] == 2)
        self.assertEqual(p2["participants"], (2, 3, 4, 1))

    def test_default_healthy_cluster_has_no_false_elections_for_ten_seconds(self):
        self.start_cluster(config=Config())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            states = self.cluster.snapshots()
            self.assertTrue(all(state["running"] and not state["error"]
                                and state["leader_id"] == 5 for state in states.values()))
            time.sleep(0.05)
        records = self.trace.snapshot()
        self.assertEqual(len([r for r in records if r["event"] == "ELECTION_START"]), 1)
        self.assertFalse(any(r["event"] == "FAILURE_TIMEOUT" for r in records))

    def test_bad_frames_leave_node_alive_and_heartbeats_running(self):
        self.start_cluster()
        address = self.cluster.nodes[2].addresses[2]
        with socket.create_connection(address, timeout=1) as sock:
            sock.sendall(b"bad-json\n")
        with socket.create_connection(address, timeout=1) as sock:
            sock.sendall(b"X" * 9000)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if len([r for r in self.trace.snapshot() if r["node"] == 2 and r["event"] == "INVALID"]) >= 2:
                break
            time.sleep(0.02)
        self.assertEqual(len([r for r in self.trace.snapshot() if r["node"] == 2 and r["event"] == "INVALID"]), 2)
        self.assertTrue(self.cluster.nodes[2].is_alive())
        self.assertEqual(self.cluster.nodes[2].snapshot()["leader_id"], 5)

    def test_busy_port_rolls_back_all_started_threads(self):
        addresses = free_addresses()
        with socket.socket() as occupied:
            occupied.bind(addresses[3])
            occupied.listen()
            self.cluster = Cluster(addresses, self.trace, FAST)
            with self.assertRaisesRegex(RuntimeError, "P3"):
                self.cluster.start()
            self.assertTrue(all(not node.is_alive() for node in self.cluster.nodes.values()))

    def test_election_watchdog_retries_with_new_round_id(self):
        class DropFirstElection(Node):
            def _forward(self, message):
                if self.node_id == 1 and message.election_id == (1, 1):
                    return
                super()._forward(message)

        retry_config = Config(0.05, 0.25, 0.1, 0.5)
        self.cluster = Cluster(free_addresses(), self.trace, retry_config, DropFirstElection)
        self.cluster.start()
        states = self.cluster.wait_for(lambda views: all(v["leader_id"] == 5 and not v["active_rounds"]
                                                        for v in views.values()), 4, "Retry election failed")
        self.assertIn((1, 2), states[1]["completed"])
        self.assertTrue(any(r["event"] == "ELECTION_TIMEOUT" and r["election_id"] == (1, 1)
                            for r in self.trace.snapshot()))

    def test_keyboard_interrupt_cleans_up_threads_and_cli_returns_130(self):
        from leader_election.__main__ import main

        before = {thread.ident for thread in threading.enumerate()}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Cluster, "wait_bootstrap", side_effect=KeyboardInterrupt):
                self.assertEqual(main(["--quiet", "--base-port", "55300", "--log-dir", directory]), 130)
            text = (Path(directory) / "trace.log").read_text(encoding="utf-8")
            self.assertIn("INTERRUPTED", text)
            self.assertEqual(text.count("STOPPED:"), 5)
        self.assertEqual({thread.ident for thread in threading.enumerate()}, before)


if __name__ == "__main__":
    unittest.main()
