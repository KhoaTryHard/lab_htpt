import asyncio
import json
import subprocess
import sys
import tempfile
import unittest

from twopc.config import PARTICIPANTS
from twopc.harness import Cluster, PROJECT_ROOT, SCENARIOS, exercise, sample_operations
from twopc.nodes import STORAGE_EXIT
from twopc.protocol import envelope, fingerprint, request


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cluster = Cluster(self.temp.name)
        self.addCleanup(self.cluster.close)

    def send(self, node, message):
        return asyncio.run(request(self.cluster.config.address(node), message,
                                   self.cluster.config.request_timeout))

    def test_idempotency_busy_validation_and_opposite_decision(self):
        c = self.cluster
        c.start_all({"P3": ("omit_vote",)})
        first = c.submit()
        self.assertFalse(first["completed"])
        self.assertEqual(c.submit()["fingerprint"], first["fingerprint"])
        self.assertEqual(c.submit(operations=sample_operations(1))["type"], "ERROR")
        self.assertEqual(c.submit("102")["type"], "ERROR")
        c.verify("GLOBAL_ABORT", (0, 0, 0))
        self.assertEqual(c.status()["reason"], "vote_timeout")
        before = {p: len(c.wal_records(p)) for p in PARTICIPANTS}
        self.assertTrue(c.submit()["completed"])
        fp = fingerprint(sample_operations())
        for node in PARTICIPANTS:
            response = self.send(node, envelope("GLOBAL_ABORT", "COORD", "101", fingerprint=fp))
            self.assertEqual(response["type"], "ACK")
            self.assertEqual(len(c.wal_records(node)), before[node])
        c.restart("P3")
        c.submit("102", sample_operations(1))
        c.verify("GLOBAL_COMMIT", (11, 21, 31), "102")
        for node in PARTICIPANTS:
            count = len(c.wal_records(node))
            response = self.send(node, envelope("GLOBAL_ABORT", "COORD", "102",
                                                fingerprint=fingerprint(sample_operations(1))))
            self.assertEqual(response["type"], "ERROR")
            self.assertEqual(len(c.wal_records(node)), count)
        self.assertEqual(c.rpc("COORD", "SUBMIT", "bad", operations={})["type"], "ERROR")
        # An unrecognized participant or mismatched decision cannot satisfy ACK.
        self.assertEqual(self.send("COORD", envelope("ACK", "P4", "102",
                                                    fingerprint=fp, decision="GLOBAL_COMMIT"))["type"], "ERROR")

    def test_local_lock_conflict_unknown_abort_and_late_prepare(self):
        c = self.cluster
        c.start_all()
        ops, fp = sample_operations(), fingerprint(sample_operations())
        prepare = envelope("VOTE_REQUEST", "COORD", "manual", operations=ops, fingerprint=fp)
        self.assertEqual(self.send("P1", prepare)["type"], "VOTE_COMMIT")
        self.assertEqual(self.send("P1", prepare)["type"], "VOTE_COMMIT")
        self.assertEqual(c.rpc("P1", "GET", key="x")["value"], 0)
        conflict = envelope("VOTE_REQUEST", "COORD", "conflict", operations=ops, fingerprint=fp)
        self.assertEqual(self.send("P1", conflict)["type"], "VOTE_ABORT")
        self.assertEqual(c.status("P1", "manual")["locks"], {"x": "manual"})
        self.assertEqual(self.send("P1", envelope("GLOBAL_ABORT", "COORD", "manual",
                                                 fingerprint=fp))["type"], "ACK")
        self.assertEqual(self.send("P1", prepare)["type"], "VOTE_ABORT")
        self.assertEqual(c.status("P1", "manual")["locks"], {})
        self.assertEqual(self.send("P2", envelope("GLOBAL_COMMIT", "COORD", "unknown",
                                                 fingerprint=fp))["type"], "ERROR")
        self.assertEqual(self.send("P2", envelope("GLOBAL_ABORT", "COORD", "unknown",
                                                 fingerprint=fp))["type"], "ACK")
        late = envelope("VOTE_REQUEST", "COORD", "unknown", operations=ops, fingerprint=fp)
        self.assertEqual(self.send("P2", late)["type"], "VOTE_ABORT")

    def test_crash_in_init_then_recovery_aborts(self):
        c = self.cluster
        c.start_all({"P1": ("after_init",)})
        c.submit()
        c.crashed("P1")
        self.assertEqual([r["event"] for r in c.wal_records("P1")], ["META", "INIT"])
        c.start("P1")
        c.verify("GLOBAL_ABORT", (0, 0, 0))

    def test_crash_after_start_before_any_prepare(self):
        c = self.cluster
        c.start_all({"COORD": ("after_start",)})
        try:
            c.submit()
        except (OSError, TimeoutError, ValueError):
            pass
        c.crashed("COORD")
        self.assertEqual([r["event"] for r in c.wal_records("COORD")], ["META", "START_2PC"])
        c.start("COORD")
        c.verify("GLOBAL_ABORT", (0, 0, 0))

    def test_cli_submit_status_get_and_unconfirmed_response(self):
        c = self.cluster
        c.start_all()
        transaction = c.directory / "transaction.json"
        transaction.write_text(json.dumps(sample_operations()), encoding="utf-8")

        def command(*args):
            return subprocess.run(
                [sys.executable, "-m", "twopc", *args, "--config", str(c.config_path)],
                cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

        result = command("submit", "--tx-id", "101", "--file", str(transaction))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["completed"])
        result = command("status", "--tx-id", "101")
        self.assertEqual(json.loads(result.stdout)["decision"], "GLOBAL_COMMIT")
        result = command("get", "--node", "P2", "--key", "x")
        self.assertEqual(json.loads(result.stdout)["value"], 20)
        c.stop("COORD")
        result = command("submit", "--tx-id", "new", "--file", str(transaction), "--wait", "0")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["type"], "UNCONFIRMED")

    def test_fsync_error_stops_server_without_vote_response(self):
        c = self.cluster
        # Construct the real server first, then simulate a disk failure on its
        # next fsync. This remains a subprocess/TCP test, not a mocked response.
        script = """
import asyncio, sys
from twopc.config import Config
from twopc.nodes import Participant
import twopc.wal as wal_module
node = Participant(Config.load(sys.argv[1]), 'P1')
def fail_fsync(fd):
    raise OSError('injected disk failure')
wal_module.os.fsync = fail_fsync
asyncio.run(node.serve())
"""
        stream = c.output_path("P1").open("ab")
        c.outputs["P1"] = stream
        c.processes["P1"] = subprocess.Popen(
            [sys.executable, "-u", "-c", script, str(c.config_path)],
            cwd=PROJECT_ROOT, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        c.wait(lambda: c.ping("P1"), "fault-injected server listen")
        ops = sample_operations()
        with self.assertRaises((OSError, TimeoutError, ValueError)):
            self.send("P1", envelope("VOTE_REQUEST", "COORD", "101",
                                     operations=ops, fingerprint=fingerprint(ops)))
        c.wait(lambda: c.processes["P1"].poll() is not None, "storage-failed server exit")
        self.assertEqual(c.processes["P1"].returncode, STORAGE_EXIT)
        self.assertNotIn("VOTE_COMMIT", [r["event"] for r in c.wal_records("P1")])


def scenario_test(name):
    def test(self):
        exercise(self.cluster, name)
        # Each participant has one durable global decision for transaction 101.
        for node in PARTICIPANTS:
            finals = [r for r in self.cluster.wal_records(node)
                      if r["tx_id"] == "101" and r["event"] in {"GLOBAL_COMMIT", "GLOBAL_ABORT"}]
            self.assertEqual(len(finals), 1)
        if name in {"participant-ready-crash", "participant-decision-crash"}:
            self.assertIn("RECOVER", self.cluster.log("P1"))
        if name == "lost-ack":
            self.assertIn("FAILPOINT drop_ack", self.cluster.log("P1"))
    return test


for scenario in SCENARIOS:
    setattr(ProcessTests, "test_scenario_" + scenario.replace("-", "_"), scenario_test(scenario))


if __name__ == "__main__":
    unittest.main()
