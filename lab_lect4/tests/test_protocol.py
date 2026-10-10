import json
import unittest

from leader_election.protocol import (Message, ProtocolError, advance_election,
                                      decode, successors)

MEMBERS = (1, 2, 3, 4, 5)


class ProtocolTests(unittest.TestCase):
    def test_collect_and_announce_at_origin(self):
        message = Message("ELECTION", 2, 2, (2, 1), (2,))
        for node_id in (3, 4, 1):
            message = advance_election(message, node_id)
        self.assertEqual(message.participants, (2, 3, 4, 1))
        result = advance_election(message, 2)
        self.assertEqual(result.type, "COORDINATOR")
        self.assertEqual(result.leader_id, 4)
        self.assertEqual(result.origin_id, 2)
        self.assertEqual(result.election_id, (2, 1))

    def test_successor_order_wraps_without_self(self):
        self.assertEqual(successors(4, MEMBERS), (5, 1, 2, 3))
        self.assertEqual(successors(5, MEMBERS), (1, 2, 3, 4))

    def test_non_origin_cannot_appear_twice(self):
        with self.assertRaises(ProtocolError):
            advance_election(Message("ELECTION", 3, 2, (2, 1), (2, 3)), 3)

    def test_concurrent_origins_preserve_distinct_rounds(self):
        for origin in range(1, 5):
            message = Message("ELECTION", origin, origin, (origin, 7), (origin,))
            for node in successors(origin, (1, 2, 3, 4)):
                message = advance_election(message, node)
            result = advance_election(message, origin)
            self.assertEqual(result.leader_id, 4)
            self.assertEqual(result.election_id, (origin, 7))
            self.assertEqual(set(result.participants), {1, 2, 3, 4})

    def test_valid_wire_messages_roundtrip(self):
        for message in (Message("ELECTION", 3, 2, (2, 1), (2, 3)),
                        Message("COORDINATOR", 1, 2, (2, 1), (2, 3, 4, 1), 4),
                        Message("HEARTBEAT", 4, leader_id=4)):
            self.assertEqual(decode(message.encode().rstrip(b"\n"), MEMBERS), message)

    def test_reject_bad_fields_and_invalid_winner(self):
        valid = Message("ELECTION", 2, 2, (2, 1), (2,)).as_dict()
        mutations = [{"sender_id": True}, {"origin_id": 9}, {"election_id": [2, 0]},
                     {"election_id": [3, 1]}, {"participants": [2, 2]},
                     {"participants": [3, 2]}, {"participants": [2, 9]},
                     {"type": "OK"}, {"type": "COORDINATOR", "leader_id": 1}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ProtocolError):
                decode(json.dumps({**valid, **mutation}).encode(), MEMBERS)
        for frame in (b"[]", b"not-json", b"\xff", b"x" * 8193,
                      b"[" * 2000 + b"0" + b"]" * 2000):
            with self.subTest(frame=frame[:10]), self.assertRaises(ProtocolError):
                decode(frame, MEMBERS)


if __name__ == "__main__":
    unittest.main()
