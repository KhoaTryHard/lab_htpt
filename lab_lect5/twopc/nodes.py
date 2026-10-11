"""Async TCP servers. State/WAL sections contain no awaits and are serialized
by the event loop; network waits never hold a state lock.
"""

import asyncio
import os

from .config import PARTICIPANTS
from .protocol import (DECISIONS, MAX_FRAME, ProtocolError, encode, envelope,
                       fingerprint, normalize_operations, read_message, request,
                       transaction_id, validate_fingerprint)
from .state import (CoordinatorTx, apply_participant_record, can_commit,
                    replay_coordinator, replay_participant)
from .wal import LogCorruption, StorageError, WAL

CRASH_EXIT = 86
STORAGE_EXIT = 74
FAULT_POINTS = (
    "after_start", "before_decision", "after_decision", "after_first_decision",
    "after_init", "after_prepare", "omit_vote", "drop_ack",
)
NETWORK_ERRORS = (OSError, TimeoutError, ProtocolError)


class Faults:
    def __init__(self, points=(), tx_id=None):
        self.points = set(points)
        self.tx_id = tx_id
        self.used = set()

    def matches(self, point, tx_id):
        return point in self.points and (self.tx_id is None or self.tx_id == tx_id)

    def once(self, point, tx_id):
        key = (point, tx_id)
        if not self.matches(point, tx_id) or key in self.used:
            return False
        self.used.add(key)
        return True


class Node:
    def __init__(self, config, node_id, faults=None):
        self.config, self.node_id = config, node_id
        self.faults = faults or Faults()
        self.tasks = set()
        self.wal = WAL(config.data_dir / node_id / "wal.jsonl")
        expected = {"node": node_id, "participants": list(PARTICIPANTS)}
        if node_id != "COORD":
            expected["initial_values"] = config.initial_values
        if not self.wal.records:
            self.wal.append("META", **expected)
        meta = self.wal.records[0]
        if meta["event"] != "META" or meta["tx_id"] is not None or meta["data"] != expected:
            raise LogCorruption("WAL metadata does not match node/configuration")

    def trace(self, text):
        print(f"[{self.node_id}] {text}", flush=True)

    def crash(self, point, tx_id):
        if self.faults.once(point, tx_id):
            self.trace(f"Tx {tx_id} FAILPOINT {point} -> process crash (exit {CRASH_EXIT})")
            os._exit(CRASH_EXIT)

    def fatal(self, exc):
        self.trace(f"FATAL {type(exc).__name__}: {exc}")
        # A node with uncertain durable state must stop, including background work.
        os._exit(STORAGE_EXIT)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def finished(item):
            self.tasks.discard(item)
            if not item.cancelled() and item.exception() is not None:
                self.fatal(item.exception())
        task.add_done_callback(finished)
        return task

    async def connection(self, reader, writer):
        try:
            message = await asyncio.wait_for(read_message(reader), self.config.request_timeout)
            response = await self.handle(message)
            if response is not None:
                writer.write(encode(response))
                await writer.drain()
        except StorageError as exc:
            self.fatal(exc)
        except (ProtocolError, KeyError, TypeError, ValueError) as exc:
            try:
                writer.write(encode(envelope("ERROR", self.node_id, error=str(exc))))
                await writer.drain()
            except OSError:
                pass
        except (OSError, TimeoutError):
            pass
        except Exception as exc:
            self.fatal(exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def serve(self):
        server = await asyncio.start_server(self.connection, *self.config.address(self.node_id),
                                            limit=MAX_FRAME)
        self.trace(f"LISTEN {self.config.address(self.node_id)} WAL={self.wal.path}")
        self.start_background()
        try:
            async with server:
                await server.serve_forever()
        finally:
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def ping(self):
        return envelope("PONG", self.node_id)


class Coordinator(Node):
    def __init__(self, config, faults=None):
        super().__init__(config, "COORD", faults)
        self.transactions = replay_coordinator(self.wal.records[1:])
        for tx in self.transactions.values():
            if tx.state == "WAIT":
                self.decide(tx, "GLOBAL_ABORT", "recovery_without_decision", inject=False)
            if not tx.done:
                self.trace(f"Tx {tx.tx_id} RECOVER {tx.state}; retransmit durable decision")

    def start_background(self):
        for tx in self.transactions.values():
            if not tx.done:
                self.spawn(self.drive(tx))

    def status(self, tx_id):
        tx = self.transactions.get(tx_id)
        fields = tx.status() if tx else {"tx_id": tx_id, "state": "UNKNOWN",
                                        "decision": None, "completed": False}
        return envelope("STATUS", "COORD", **fields)

    async def handle(self, message):
        kind, sender = message["type"], message["sender"]
        if kind == "PING":
            return self.ping()
        tx_id = transaction_id(message.get("tx_id"))
        if kind == "STATUS":
            return self.status(tx_id)
        if kind == "SUBMIT":
            if sender != "CLIENT":
                raise ProtocolError("SUBMIT must come from CLIENT")
            ops = normalize_operations(message.get("operations"))
            fp = fingerprint(ops)
            tx = self.transactions.get(tx_id)
            if tx:
                if tx.fingerprint != fp:
                    raise ProtocolError("tx_id already belongs to a different payload")
                return self.status(tx_id)
            if any(not old.done for old in self.transactions.values()):
                return envelope("ERROR", "COORD", tx_id, error="BUSY: another transaction is incomplete")
            self.wal.append("START_2PC", tx_id, operations=ops, fingerprint=fp,
                            participants=list(PARTICIPANTS))
            tx = CoordinatorTx(tx_id, ops, fp)
            self.transactions[tx_id] = tx
            self.trace(f"Tx {tx_id} Started -> Log START_2PC (fsync)")
            self.crash("after_start", tx_id)
            self.spawn(self.drive(tx))
            return self.status(tx_id)
        if kind == "DECISION_REQUEST":
            if sender not in PARTICIPANTS:
                raise ProtocolError("Decision request requires a configured participant")
            tx = self.transactions.get(tx_id)
            if tx and message.get("fingerprint") != tx.fingerprint:
                raise ProtocolError("Decision query fingerprint mismatch")
            return envelope("DECISION_RESPONSE", "COORD", tx_id,
                            decision=tx.decision if tx else None,
                            fingerprint=tx.fingerprint if tx else None,
                            state=tx.state if tx else "UNKNOWN")
        if kind == "ACK":
            self.accept_ack(message)
            return self.status(tx_id)
        raise ProtocolError(f"Unsupported coordinator message: {kind}")

    def decide(self, tx, decision, reason, inject=True):
        if tx.decision:
            if tx.decision != decision:
                raise ProtocolError("Cannot reverse a durable decision")
            return
        if decision == "GLOBAL_COMMIT" and not can_commit(tx.votes):
            raise ProtocolError("Commit requires all three distinct COMMIT votes")
        self.wal.append(decision, tx.tx_id, fingerprint=tx.fingerprint,
                        reason=reason, votes=dict(tx.votes))
        tx.decision, tx.reason = decision, reason
        tx.state = "COMMIT" if decision == "GLOBAL_COMMIT" else "ABORT"
        self.trace(f"Tx {tx.tx_id} Log {decision} (fsync); reason={reason}")
        if inject:
            self.crash("after_decision", tx.tx_id)

    async def collect_vote(self, tx, participant, deadline):
        message = envelope("VOTE_REQUEST", "COORD", tx.tx_id,
                           operations=tx.operations, fingerprint=tx.fingerprint)
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                result = await request(self.config.address(participant), message,
                                       min(self.config.request_timeout, remaining))
                if (result.get("sender") == participant and result.get("tx_id") == tx.tx_id
                        and result.get("fingerprint") == tx.fingerprint
                        and result.get("type") in {"VOTE_COMMIT", "VOTE_ABORT"}):
                    return participant, result["type"]
                self.trace(f"Tx {tx.tx_id} Reject invalid vote from {participant}")
            except NETWORK_ERRORS:
                pass
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0:
                await asyncio.sleep(min(self.config.retry_interval, remaining))
        return participant, None

    async def voting(self, tx):
        self.trace(f"Tx {tx.tx_id} Multicast VOTE_REQUEST")
        deadline = asyncio.get_running_loop().time() + self.config.vote_timeout
        pending = {asyncio.create_task(self.collect_vote(tx, p, deadline)) for p in PARTICIPANTS}
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    participant, vote = task.result()
                    if vote:
                        tx.votes[participant] = vote
                        self.trace(f"Tx {tx.tx_id} Recv {vote} from {participant}")
                    if vote == "VOTE_ABORT":
                        self.decide(tx, "GLOBAL_ABORT", f"vote_abort:{participant}")
                        return
            if can_commit(tx.votes):
                self.crash("before_decision", tx.tx_id)
                self.decide(tx, "GLOBAL_COMMIT", "all_three_voted_commit")
            else:
                self.decide(tx, "GLOBAL_ABORT", "vote_timeout")
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def accept_ack(self, message):
        tx_id = transaction_id(message.get("tx_id"))
        tx = self.transactions.get(tx_id)
        participant = message.get("sender")
        if (not tx or not tx.decision or participant not in PARTICIPANTS
                or message.get("fingerprint") != tx.fingerprint
                or message.get("decision") != tx.decision):
            raise ProtocolError("Invalid ACK identity, transaction or decision")
        if tx.done:
            return
        tx.acks.add(participant)
        if tx.acks == set(PARTICIPANTS):
            self.wal.append("END_2PC", tx_id)
            tx.done = True
            self.trace(f"Tx {tx_id} Log END_2PC (fsync) -> {tx.state} completed, all ACKs")

    async def deliver(self, tx, participant):
        try:
            result = await request(self.config.address(participant),
                                   envelope(tx.decision, "COORD", tx.tx_id,
                                            fingerprint=tx.fingerprint),
                                   self.config.request_timeout)
            if result.get("type") == "ACK" and result.get("sender") == participant:
                self.accept_ack(result)
        except NETWORK_ERRORS:
            pass

    async def drive(self, tx):
        if not tx.decision:
            await self.voting(tx)
        self.trace(f"Tx {tx.tx_id} Multicast {tx.decision}")
        if self.faults.matches("after_first_decision", tx.tx_id):
            await self.deliver(tx, "P1")
            self.crash("after_first_decision", tx.tx_id)
        while not tx.done:
            await asyncio.gather(*(self.deliver(tx, p) for p in PARTICIPANTS if p not in tx.acks))
            if not tx.done:
                await asyncio.sleep(self.config.retry_interval)


class Participant(Node):
    def __init__(self, config, node_id, faults=None, locked_keys=()):
        if node_id not in PARTICIPANTS:
            raise ValueError("Participant id must be P1, P2 or P3")
        super().__init__(config, node_id, faults)
        self.locked_keys = set(locked_keys)
        self.transactions, self.store, self.locks = replay_participant(
            self.wal.records[1:], config.initial_values)
        for tx in list(self.transactions.values()):
            if tx.state == "INIT":
                self.record("VOTE_ABORT", tx.tx_id, fingerprint=tx.fingerprint,
                            reason="recovery_in_init")
            self.trace(f"Tx {tx.tx_id} RECOVER {tx.state}")

    def record(self, event, tx_id, **data):
        record = self.wal.append(event, tx_id, **data)
        if event in DECISIONS:
            self.crash("after_decision", tx_id)
        apply_participant_record(self.transactions, self.store, self.locks, record)
        self.trace(f"Tx {tx_id} Log {event} (fsync)")

    def start_background(self):
        self.spawn(self.resolve_ready())

    def prepare(self, message):
        tx_id = transaction_id(message.get("tx_id"))
        ops = normalize_operations(message.get("operations"))
        fp = fingerprint(ops)
        if message.get("fingerprint") != fp:
            raise ProtocolError("VOTE_REQUEST fingerprint mismatch")
        local_ops = ops[self.node_id]
        tx = self.transactions.get(tx_id)
        if tx:
            if tx.fingerprint != fp or (tx.operations and tx.operations != local_ops):
                raise ProtocolError("tx_id already belongs to a different payload")
            vote = "VOTE_COMMIT" if tx.state in {"READY", "COMMIT"} else "VOTE_ABORT"
            return envelope(vote, self.node_id, tx_id, fingerprint=fp)
        self.record("INIT", tx_id, fingerprint=fp, operations=local_ops)
        self.crash("after_init", tx_id)
        conflict = any(op["key"] in self.locks or op["key"] in self.locked_keys for op in local_ops)
        if conflict:
            self.trace(f"Tx {tx_id} Lock Conflict! -> VOTE_ABORT")
            self.record("VOTE_ABORT", tx_id, fingerprint=fp, reason="lock_conflict")
            vote = "VOTE_ABORT"
        else:
            self.record("VOTE_COMMIT", tx_id, fingerprint=fp, operations=local_ops)
            self.crash("after_prepare", tx_id)
            vote = "VOTE_COMMIT"
        self.trace(f"Tx {tx_id} Reply {vote}")
        return envelope(vote, self.node_id, tx_id, fingerprint=fp)

    def final_decision(self, tx_id, decision, fp):
        validate_fingerprint(fp)
        tx = self.transactions.get(tx_id)
        if tx and tx.fingerprint != fp:
            raise ProtocolError("Decision fingerprint mismatch")
        if tx and tx.decision:
            if tx.decision != decision:
                raise ProtocolError("Cannot reverse a durable decision")
        else:
            if decision == "GLOBAL_COMMIT" and (not tx or tx.state != "READY"):
                raise ProtocolError("GLOBAL_COMMIT without durable READY")
            self.record(decision, tx_id, fingerprint=fp)
            action = "Apply SET" if decision == "GLOBAL_COMMIT" else "Rollback"
            self.trace(f"Tx {tx_id} Recv {decision} -> {action} -> Reply ACK")
        return envelope("ACK", self.node_id, tx_id, fingerprint=fp, decision=decision)

    async def handle(self, message):
        kind = message["type"]
        if kind == "PING":
            return self.ping()
        if kind == "GET":
            key = message.get("key")
            if not isinstance(key, str) or not key:
                raise ProtocolError("GET requires a nonempty key")
            return envelope("VALUE", self.node_id, key=key,
                            found=key in self.store, value=self.store.get(key))
        if kind == "STATUS":
            tx_id = message.get("tx_id")
            if tx_id is not None:
                transaction_id(tx_id)
                tx = self.transactions.get(tx_id)
                fields = tx.status() if tx else {"tx_id": tx_id, "state": "UNKNOWN", "decision": None}
            else:
                fields = {"transactions": {k: v.status() for k, v in self.transactions.items()}}
            return envelope("STATUS", self.node_id, **fields, locks=dict(self.locks),
                            injected_conflicts=sorted(self.locked_keys))
        if message["sender"] != "COORD":
            raise ProtocolError("Only COORD may prepare or decide transactions")
        tx_id = transaction_id(message.get("tx_id"))
        if kind == "VOTE_REQUEST":
            response = self.prepare(message)
            if self.faults.matches("omit_vote", tx_id):
                if self.faults.once("omit_vote", tx_id):
                    self.trace(f"Tx {tx_id} FAILPOINT omit_vote -> suppress all vote replies")
                return None
            return response
        if kind in DECISIONS:
            response = self.final_decision(tx_id, kind, message.get("fingerprint"))
            if self.faults.once("drop_ack", tx_id):
                self.trace(f"Tx {tx_id} FAILPOINT drop_ack -> suppress first ACK")
                return None
            return response
        raise ProtocolError(f"Unsupported participant message: {kind}")

    async def resolve_ready(self):
        while True:
            await asyncio.sleep(self.config.retry_interval)
            for tx in list(self.transactions.values()):
                if tx.state != "READY":
                    continue
                try:
                    response = await request(self.config.address("COORD"),
                                             envelope("DECISION_REQUEST", self.node_id, tx.tx_id,
                                                      fingerprint=tx.fingerprint),
                                             self.config.request_timeout)
                    if (response.get("type") != "DECISION_RESPONSE"
                            or response.get("sender") != "COORD"
                            or response.get("tx_id") != tx.tx_id
                            or response.get("fingerprint") != tx.fingerprint
                            or response.get("decision") not in DECISIONS):
                        continue
                    ack = self.final_decision(tx.tx_id, response["decision"], tx.fingerprint)
                    await request(self.config.address("COORD"), ack, self.config.request_timeout)
                except NETWORK_ERRORS:
                    # A timeout/UNKNOWN is never permission to abort a prepared tx.
                    continue
