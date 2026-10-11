import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from twopc.config import Config, PARTICIPANTS
from twopc.harness import sample_operations
from twopc.nodes import Coordinator, Participant
from twopc.protocol import ProtocolError, envelope, fingerprint, normalize_operations
from twopc.state import CoordinatorTx, can_commit
from twopc.wal import LogCorruption, StorageError, WAL


def config(directory):
    return Config({node: {"host": "127.0.0.1", "port": 9000 + i}
                   for i, node in enumerate(("COORD", *PARTICIPANTS))},
                  Path(directory), {"x": 0})


class WALTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "wal.jsonl"

    def test_truncated_tail_is_removed_before_next_append(self):
        wal = WAL(self.path)
        wal.append("TEST", "1", value=7)
        with self.path.open("ab") as stream:
            stream.write(b'{"seq":2,"event":"incomplete')
        recovered = WAL(self.path)
        self.assertEqual(len(recovered.records), 1)
        recovered.append("TEST", "2", value=8)
        again = WAL(self.path)
        self.assertEqual([r["seq"] for r in again.records], [1, 2])
        self.assertEqual(again.records[1]["data"]["value"], 8)

    def test_complete_invalid_record_is_rejected_without_repair(self):
        raw = b'not-json\n'
        self.path.write_bytes(raw)
        with self.assertRaises(LogCorruption):
            WAL(self.path)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_sequence_gap_is_rejected(self):
        self.path.write_text('{"seq":2,"event":"TEST","tx_id":"1","data":{}}\n',
                             encoding="utf-8")
        with self.assertRaises(LogCorruption):
            WAL(self.path)

    def test_failed_fsync_poisoned_wal_cannot_acknowledge_append(self):
        wal = WAL(self.path)
        with patch("twopc.wal.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(StorageError):
                wal.append("TEST", "1")
        self.assertEqual(wal.records, [])
        with self.assertRaises(StorageError):
            wal.append("TEST", "2")

    def test_fsync_sees_flushed_complete_record(self):
        wal = WAL(self.path)
        visible = []
        with patch("twopc.wal.os.fsync", side_effect=lambda fd: visible.append(self.path.read_bytes())):
            wal.append("TEST", "1", text="Tiếng Việt")
        self.assertEqual(len(visible), 1)
        self.assertTrue(visible[0].endswith(b"\n"))
        self.assertIn("Tiếng Việt".encode(), visible[0])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = config(self.temp.name)
        self.ops = sample_operations()
        self.fp = fingerprint(self.ops)

    def prepare_message(self, tx_id="101"):
        return envelope("VOTE_REQUEST", "COORD", tx_id,
                        operations=self.ops, fingerprint=self.fp)

    def test_commit_requires_exactly_all_three_votes(self):
        for invalid in ({}, {"P1": "VOTE_COMMIT"},
                        {"P1": "VOTE_COMMIT", "P2": "VOTE_COMMIT"},
                        {"P1": "VOTE_COMMIT", "P2": "VOTE_COMMIT", "P3": "VOTE_ABORT"},
                        {**dict.fromkeys(PARTICIPANTS, "VOTE_COMMIT"), "P4": "VOTE_COMMIT"}):
            with self.subTest(votes=invalid):
                self.assertFalse(can_commit(invalid))
        self.assertTrue(can_commit(dict.fromkeys(PARTICIPANTS, "VOTE_COMMIT")))

    def test_invalid_operations(self):
        bad_cases = [{}, {"P1": []}, {p: [{"op": "SET", "key": "x", "value": True}]
                                    for p in PARTICIPANTS},
                     {p: self.ops[p] * 2 for p in PARTICIPANTS}]
        for value in bad_cases:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                normalize_operations(value)

    def test_state_and_data_change_only_after_fsync(self):
        node = Participant(self.config, "P1")
        observations = []

        def observe(fd):
            tx = node.transactions.get("101")
            observations.append((tx.state if tx else None, node.store["x"]))

        with patch("twopc.wal.os.fsync", side_effect=observe):
            vote = node.prepare(self.prepare_message())
            self.assertEqual(vote["type"], "VOTE_COMMIT")
            self.assertEqual(node.store["x"], 0)
            ack = node.final_decision("101", "GLOBAL_COMMIT", self.fp)
        self.assertEqual(observations, [(None, 0), ("INIT", 0), ("READY", 0)])
        self.assertEqual(ack["type"], "ACK")
        self.assertEqual(node.store["x"], 10)
        self.assertEqual(node.locks, {})

    def test_storage_failure_cannot_return_commit_vote(self):
        node = Participant(self.config, "P1")
        # INIT can persist; fail the prepare record's fsync.
        node.record("INIT", "101", fingerprint=self.fp, operations=self.ops["P1"])
        with patch("twopc.wal.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(StorageError):
                node.record("VOTE_COMMIT", "101", fingerprint=self.fp,
                            operations=self.ops["P1"])
        self.assertEqual(node.transactions["101"].state, "INIT")
        self.assertEqual(node.locks, {})
        self.assertEqual(node.store["x"], 0)

    def test_ready_replay_restores_pending_and_locks(self):
        node = Participant(self.config, "P1")
        node.prepare(self.prepare_message())
        recovered = Participant(self.config, "P1")
        self.assertEqual(recovered.transactions["101"].state, "READY")
        self.assertEqual(recovered.locks, {"x": "101"})
        self.assertEqual(recovered.store["x"], 0)
        recovered.final_decision("101", "GLOBAL_COMMIT", self.fp)
        twice = Participant(self.config, "P1")
        self.assertEqual(twice.store["x"], 10)
        self.assertEqual(twice.locks, {})

    def test_multiple_keys_and_abort_preserve_committed_history(self):
        node = Participant(self.config, "P1")
        operations = sample_operations()
        operations["P1"].append({"op": "SET", "key": "y", "value": 7})
        fp = fingerprint(operations)
        node.prepare(envelope("VOTE_REQUEST", "COORD", "101",
                              operations=operations, fingerprint=fp))
        self.assertNotIn("y", node.store)
        self.assertEqual(node.locks, {"x": "101", "y": "101"})
        node.final_decision("101", "GLOBAL_COMMIT", fp)
        operations["P1"] = [{"op": "SET", "key": "x", "value": 99},
                            {"op": "SET", "key": "z", "value": 8}]
        fp = fingerprint(operations)
        node.prepare(envelope("VOTE_REQUEST", "COORD", "102",
                              operations=operations, fingerprint=fp))
        node.final_decision("102", "GLOBAL_ABORT", fp)
        recovered = Participant(self.config, "P1")
        self.assertEqual(recovered.store, {"x": 10, "y": 7})
        self.assertEqual(recovered.locks, {})

    def test_init_recovery_votes_abort(self):
        node = Participant(self.config, "P1")
        node.record("INIT", "101", fingerprint=self.fp, operations=self.ops["P1"])
        recovered = Participant(self.config, "P1")
        self.assertEqual(recovered.transactions["101"].vote, "VOTE_ABORT")
        self.assertEqual(recovered.prepare(self.prepare_message())["type"], "VOTE_ABORT")

    def test_unknown_abort_tombstone_and_invalid_commit(self):
        node = Participant(self.config, "P1")
        unknown = asyncio.run(node.handle(envelope("STATUS", "CLIENT", "101")))
        self.assertEqual((unknown["tx_id"], unknown["state"]), ("101", "UNKNOWN"))
        with self.assertRaises(ProtocolError):
            node.final_decision("101", "GLOBAL_COMMIT", self.fp)
        node.final_decision("101", "GLOBAL_ABORT", self.fp)
        count = len(node.wal.records)
        self.assertEqual(node.prepare(self.prepare_message())["type"], "VOTE_ABORT")
        node.final_decision("101", "GLOBAL_ABORT", self.fp)
        self.assertEqual(len(node.wal.records), count)
        with self.assertRaises(ProtocolError):
            node.final_decision("101", "GLOBAL_COMMIT", self.fp)

    def test_commit_without_votes_is_rejected_during_replay(self):
        node = Coordinator(self.config)
        node.wal.append("START_2PC", "101", operations=self.ops, fingerprint=self.fp,
                        participants=list(PARTICIPANTS))
        node.wal.append("GLOBAL_COMMIT", "101", fingerprint=self.fp, votes={})
        with self.assertRaises(LogCorruption):
            Coordinator(self.config)

    def test_contradictory_terminal_records_are_rejected(self):
        node = Participant(self.config, "P1")
        node.prepare(self.prepare_message())
        node.final_decision("101", "GLOBAL_COMMIT", self.fp)
        node.wal.append("GLOBAL_ABORT", "101", fingerprint=self.fp)
        with self.assertRaises(LogCorruption):
            Participant(self.config, "P1")

    def test_unknown_event_and_changed_initial_data_are_rejected(self):
        node = Participant(self.config, "P1")
        altered = Config(self.config.addresses, self.config.data_dir, {"x": 999})
        with self.assertRaises(LogCorruption):
            Participant(altered, "P1")
        node.wal.append("BOGUS", "101", fingerprint=self.fp)
        with self.assertRaises(LogCorruption):
            Participant(self.config, "P1")

    def test_forged_vote_sender_is_never_counted(self):
        node = Coordinator(replace(self.config, retry_interval=0.005))
        tx = CoordinatorTx("101", self.ops, self.fp)

        async def forged(*args, **kwargs):
            return envelope("VOTE_COMMIT", "P2", "101", fingerprint=self.fp)

        async def check():
            with patch("twopc.nodes.request", side_effect=forged):
                return await node.collect_vote(tx, "P3", asyncio.get_running_loop().time() + 0.03)

        self.assertEqual(asyncio.run(check()), ("P3", None))
        self.assertEqual(tx.votes, {})


if __name__ == "__main__":
    unittest.main()
