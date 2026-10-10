from dataclasses import dataclass, replace
import threading
import time
from types import MappingProxyType

from .protocol import Message, ProtocolError, advance_election, successors
from .transport import Transport


@dataclass(frozen=True)
class Config:
    heartbeat_interval: float = 0.5
    failure_timeout: float = 2.0
    socket_timeout: float = 0.4
    election_timeout: float = 5.0

    def __post_init__(self):
        if any(value <= 0 for value in (self.heartbeat_interval, self.failure_timeout,
                                       self.socket_timeout, self.election_timeout)):
            raise ValueError("All timing values must be positive")
        if self.failure_timeout <= self.heartbeat_interval:
            raise ValueError("Failure timeout must exceed heartbeat interval")


class Node(threading.Thread):
    def __init__(self, node_id, addresses, start_event, trace, config=Config()):
        super().__init__(name=f"P{node_id}", daemon=False)
        self.node_id = node_id
        self.addresses = MappingProxyType(dict(addresses))
        self.members = tuple(sorted(addresses))
        self.config = config
        self.start_event = start_event
        self.trace = trace
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.snapshot_lock = threading.Lock()
        self.transport = None
        self.leader_id = None
        self.last_heartbeat = 0.0
        self.next_heartbeat = 0.0
        self.startup_deadline = 0.0
        self.local_counter = 0
        self.own_round = None
        self.own_deadline = 0.0
        self.rounds: dict[tuple[int, int], str] = {}
        self.seen_elections = set()
        self.seen_coordinators = set()
        self.completed: list[tuple[int, int]] = []
        self.timeouts = 0
        self.error = None
        self.running = False
        self._view = {}
        self._publish()

    def _publish(self):
        with self.snapshot_lock:
            self._view = {"id": self.node_id, "leader_id": self.leader_id,
                          "running": self.running, "error": self.error,
                          "active_rounds": tuple(self.rounds),
                          "completed": tuple(self.completed), "timeouts": self.timeouts,
                          "pending_io": len(self.transport.connections) if self.transport else 0}

    def snapshot(self):
        with self.snapshot_lock:
            return dict(self._view)

    def crash(self):
        """Local control only: no leave message, no peer notification."""
        self.stop_event.set()

    def run(self):
        try:
            self.transport = Transport(self.addresses[self.node_id], self.members,
                                       self.config.socket_timeout, self._receive,
                                       lambda reason: self.trace.emit(self.node_id, "INVALID", reason))
            self.trace.emit(self.node_id, "READY", f"TCP listener {self.addresses[self.node_id]}")
            self.running = True
            self._publish()
            self.ready.set()
            while not self.start_event.wait(0.02):
                if self.stop_event.is_set():
                    return
            if self.stop_event.is_set():
                return
            self.startup_deadline = time.monotonic() + self.config.election_timeout
            if self.node_id == min(self.members):
                self._start_election("bootstrap")
            while not self.stop_event.is_set():
                self.transport.poll()
                if self.stop_event.is_set():
                    break
                self._tick(time.monotonic())
                self._publish()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.trace.emit(self.node_id, "NODE_ERROR", self.error)
        finally:
            if self.transport:
                self.transport.close()
                self.transport = None
            self.running = False
            self._publish()
            self.ready.set()
            self.trace.emit(self.node_id, "STOPPED", "Thread exited; all sockets closed")

    def _start_election(self, reason):
        if self.own_round is not None:
            return
        self.local_counter += 1
        round_id = (self.node_id, self.local_counter)
        self.own_round = round_id
        self.own_deadline = time.monotonic() + self.config.election_timeout
        self.rounds[round_id] = "collecting"
        self.seen_elections.add(round_id)
        message = Message("ELECTION", self.node_id, self.node_id, round_id, (self.node_id,))
        self.trace.emit(self.node_id, "ELECTION_START", reason, election_id=round_id,
                        participants=message.participants)
        self._forward(message)

    def _forward(self, message):
        candidates = successors(self.node_id, self.members)
        message = replace(message, sender_id=self.node_id)

        def attempt(index):
            if self.stop_event.is_set():
                return
            if index == len(candidates):
                if message.origin_id == self.node_id:
                    # A singleton ring is completed locally; no TCP self-send needed.
                    self._receive(message)
                else:
                    self.trace.emit(self.node_id, "ROUND_STALLED", "No reachable successor",
                                    election_id=message.election_id)
                return
            destination = candidates[index]

            def finished(success, reason):
                fields = {"destination": destination, **message.as_dict()}
                fields.pop("type")
                self.trace.emit(self.node_id, "SEND" if success else "SEND_FAILED",
                                f"{message.type} -> P{destination}" + (f" ({reason})" if reason else ""),
                                message_type=message.type, **fields)
                if success:
                    if message.type == "COORDINATOR" and message.origin_id != self.node_id:
                        self.rounds.pop(message.election_id, None)
                else:
                    self.trace.emit(self.node_id, "BYPASS", f"Skip P{destination}; try next ring member",
                                    destination=destination, election_id=message.election_id,
                                    message_type=message.type)
                    attempt(index + 1)

            self.transport.send(self.addresses[destination], message, finished)

        attempt(0)

    def _install_leader(self, message):
        changed = self.leader_id != message.leader_id
        self.leader_id = message.leader_id
        self.last_heartbeat = time.monotonic()
        if changed and self.leader_id == self.node_id:
            self.next_heartbeat = 0.0
        self.trace.emit(self.node_id, "LEADER_ACCEPTED", f"Leader is P{self.leader_id}",
                        leader_id=self.leader_id, election_id=message.election_id,
                        participants=message.participants)

    def _receive(self, message):
        if self.stop_event.is_set():
            return
        fields = message.as_dict()
        fields.pop("type")
        self.trace.emit(self.node_id, "RECV", f"{message.type} <- P{message.sender_id}",
                        message_type=message.type, source=message.sender_id, **fields)
        if message.type == "HEARTBEAT":
            if message.leader_id == self.leader_id:
                self.last_heartbeat = time.monotonic()
            return
        round_id = message.election_id
        if message.type == "ELECTION":
            if message.origin_id == self.node_id:
                if self.rounds.get(round_id) != "collecting":
                    return
                coordinator = advance_election(message, self.node_id)
                self.rounds[round_id] = "announcing"
                self._install_leader(coordinator)
                self.trace.emit(self.node_id, "ELECTION_COLLECTED",
                                f"Collected {list(message.participants)}; winner P{coordinator.leader_id}",
                                election_id=round_id, participants=message.participants,
                                leader_id=coordinator.leader_id)
                self._forward(coordinator)
            elif round_id not in self.seen_elections:
                try:
                    forwarded = advance_election(message, self.node_id)
                except ProtocolError as exc:
                    self.trace.emit(self.node_id, "INVALID", str(exc))
                    return
                self.seen_elections.add(round_id)
                self.rounds[round_id] = "participating"
                self._forward(forwarded)
        elif message.type == "COORDINATOR":
            if self.node_id not in message.participants:
                self.trace.emit(self.node_id, "INVALID", "Coordinator excludes receiver")
                return
            if message.origin_id == self.node_id:
                if self.rounds.get(round_id) != "announcing":
                    return
                self.rounds.pop(round_id)
                self.completed.append(round_id)
                if self.own_round == round_id:
                    self.own_round = None
                self.trace.emit(self.node_id, "ROUND_COMPLETE", "COORDINATOR returned to origin",
                                election_id=round_id, leader_id=message.leader_id,
                                participants=message.participants)
            elif round_id not in self.seen_coordinators:
                self.seen_coordinators.add(round_id)
                self._install_leader(message)
                self.rounds[round_id] = "forwarding-coordinator"
                self._forward(message)

    def _tick(self, now):
        if self.own_round and now >= self.own_deadline:
            expired = self.own_round
            self.rounds.pop(expired, None)
            self.own_round = None
            self.trace.emit(self.node_id, "ELECTION_TIMEOUT", "Election watchdog expired",
                            election_id=expired)
            self._start_election("watchdog-retry")
        if self.leader_id is None:
            if not self.own_round and now >= self.startup_deadline:
                self._start_election("startup-timeout")
        elif self.leader_id == self.node_id:
            if now >= self.next_heartbeat:
                self.next_heartbeat = now + self.config.heartbeat_interval
                heartbeat = Message("HEARTBEAT", self.node_id, leader_id=self.node_id)
                for destination in self.members:
                    if destination != self.node_id:
                        self.transport.send(self.addresses[destination], heartbeat,
                                            lambda success, reason, target=destination:
                                            self.trace.emit(self.node_id,
                                                "HEARTBEAT_SEND" if success else "HEARTBEAT_FAILED",
                                                f"HEARTBEAT -> P{target}", destination=target,
                                                leader_id=self.node_id, reason=reason))
        elif not self.own_round and now - self.last_heartbeat >= self.config.failure_timeout:
            self.timeouts += 1
            self.trace.emit(self.node_id, "FAILURE_TIMEOUT", f"No heartbeat from P{self.leader_id}",
                            suspected_leader=self.leader_id)
            self._start_election("heartbeat-timeout")

