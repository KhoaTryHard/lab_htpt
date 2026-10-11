"""Pure validation and state transitions, also used to validate WAL replay."""

from dataclasses import dataclass, field

from .config import PARTICIPANTS
from .protocol import (DECISIONS, ProtocolError, fingerprint, normalize_operations,
                       transaction_id, validate_fingerprint)
from .wal import LogCorruption


@dataclass
class CoordinatorTx:
    tx_id: str
    operations: dict
    fingerprint: str
    state: str = "WAIT"
    decision: str | None = None
    done: bool = False
    votes: dict = field(default_factory=dict)
    acks: set = field(default_factory=set)
    reason: str | None = None

    def status(self):
        return {"tx_id": self.tx_id, "state": self.state, "decision": self.decision,
                "completed": self.done, "fingerprint": self.fingerprint,
                "votes": dict(self.votes), "acked": sorted(self.acks),
                "missing_acks": [] if self.done else sorted(set(PARTICIPANTS) - self.acks),
                "reason": self.reason}


@dataclass
class ParticipantTx:
    tx_id: str
    fingerprint: str
    operations: list = field(default_factory=list)
    state: str = "INIT"
    vote: str | None = None
    decision: str | None = None

    def status(self):
        return {"tx_id": self.tx_id, "state": self.state, "vote": self.vote,
                "decision": self.decision, "fingerprint": self.fingerprint}


def can_commit(votes):
    return set(votes) == set(PARTICIPANTS) and all(
        votes[node] == "VOTE_COMMIT" for node in PARTICIPANTS)


def replay_coordinator(records):
    transactions = {}
    try:
        for record in records:
            tx_id = transaction_id(record["tx_id"])
            data, event = record["data"], record["event"]
            tx = transactions.get(tx_id)
            if event == "START_2PC":
                ops = normalize_operations(data["operations"])
                fp = fingerprint(ops)
                if tx or data["fingerprint"] != fp or data["participants"] != list(PARTICIPANTS):
                    raise ValueError("Invalid/duplicate START_2PC")
                if any(not old.done for old in transactions.values()):
                    raise ValueError("Overlapping coordinator transactions")
                transactions[tx_id] = CoordinatorTx(tx_id, ops, fp)
            elif event in DECISIONS:
                if not tx or tx.state != "WAIT" or data["fingerprint"] != tx.fingerprint:
                    raise ValueError("Invalid/contradictory coordinator decision")
                tx.decision = event
                tx.state = "COMMIT" if event == "GLOBAL_COMMIT" else "ABORT"
                tx.reason = data.get("reason")
                tx.votes = data.get("votes", {})
                if not isinstance(tx.votes, dict) or not set(tx.votes) <= set(PARTICIPANTS):
                    raise ValueError("Invalid saved votes")
                if any(v not in {"VOTE_COMMIT", "VOTE_ABORT"} for v in tx.votes.values()):
                    raise ValueError("Invalid saved vote type")
                if event == "GLOBAL_COMMIT" and not can_commit(tx.votes):
                    raise ValueError("Durable COMMIT lacks all three votes")
            elif event == "END_2PC":
                if not tx or not tx.decision or tx.done:
                    raise ValueError("Invalid END_2PC")
                tx.done = True
                tx.acks = set(PARTICIPANTS)
            else:
                raise ValueError(f"Unknown coordinator event: {event}")
    except (ValueError, KeyError, TypeError) as exc:
        raise LogCorruption(f"Coordinator replay rejected: {exc}") from exc
    return transactions


def apply_participant_record(transactions, store, locks, record):
    tx_id = transaction_id(record["tx_id"])
    event, data = record["event"], record["data"]
    fp = validate_fingerprint(data["fingerprint"])
    tx = transactions.get(tx_id)
    if tx and tx.fingerprint != fp:
        raise ProtocolError("Transaction fingerprint changed")
    if event == "INIT":
        if tx:
            raise ProtocolError("Duplicate participant INIT")
        ops = data["operations"]
        # Reuse operation validation without weakening the local SET contract.
        normalize_operations({p: ops for p in PARTICIPANTS})
        transactions[tx_id] = ParticipantTx(tx_id, fp, ops)
        return
    if event == "VOTE_COMMIT":
        if not tx or tx.state != "INIT" or data["operations"] != tx.operations:
            raise ProtocolError("VOTE_COMMIT without matching INIT")
        if any(op["key"] in locks for op in tx.operations):
            raise ProtocolError("Overlapping prepared locks")
        tx.state, tx.vote = "READY", "VOTE_COMMIT"
        for op in tx.operations:
            locks[op["key"]] = tx_id
    elif event == "VOTE_ABORT":
        if not tx or tx.state != "INIT":
            raise ProtocolError("VOTE_ABORT outside INIT")
        tx.state, tx.vote = "ABORT", "VOTE_ABORT"
    elif event in DECISIONS:
        if tx and tx.decision:
            raise ProtocolError("Duplicate/contradictory terminal WAL record")
        if event == "GLOBAL_COMMIT" and (not tx or tx.state != "READY"):
            raise ProtocolError("GLOBAL_COMMIT requires durable READY")
        if not tx:
            tx = ParticipantTx(tx_id, fp)
            transactions[tx_id] = tx
        tx.decision = event
        tx.state = "COMMIT" if event == "GLOBAL_COMMIT" else "ABORT"
        if event == "GLOBAL_COMMIT":
            for op in tx.operations:
                store[op["key"]] = op["value"]
        for op in tx.operations:
            if locks.get(op["key"]) == tx_id:
                del locks[op["key"]]
    else:
        raise ProtocolError(f"Unknown participant event: {event}")


def replay_participant(records, initial_values):
    transactions, store, locks = {}, dict(initial_values), {}
    try:
        for record in records:
            apply_participant_record(transactions, store, locks, record)
    except (ValueError, KeyError, TypeError) as exc:
        raise LogCorruption(f"Participant replay rejected: {exc}") from exc
    return transactions, store, locks
