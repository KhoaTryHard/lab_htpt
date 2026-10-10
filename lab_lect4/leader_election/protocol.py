from dataclasses import dataclass, replace
import json

MAX_FRAME = 8192
RoundId = tuple[int, int]


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Message:
    type: str
    sender_id: int
    origin_id: int | None = None
    election_id: RoundId | None = None
    participants: tuple[int, ...] = ()
    leader_id: int | None = None

    def as_dict(self) -> dict:
        result = {"type": self.type, "sender_id": self.sender_id}
        if self.type != "HEARTBEAT":
            result.update(origin_id=self.origin_id, election_id=self.election_id,
                          participants=self.participants)
        if self.leader_id is not None:
            result["leader_id"] = self.leader_id
        return result

    def encode(self) -> bytes:
        data = json.dumps(self.as_dict(), separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_FRAME:
            raise ProtocolError("Frame exceeds 8 KiB")
        return data + b"\n"


def decode(data: bytes, members: tuple[int, ...]) -> Message:
    if len(data) > MAX_FRAME:
        raise ProtocolError("Frame exceeds 8 KiB")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("Invalid JSON/UTF-8") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Message must be an object")

    def valid_id(item):
        return type(item) is int and item in members

    kind = value.get("type")
    sender = value.get("sender_id")
    if kind not in ("ELECTION", "COORDINATOR", "HEARTBEAT") or not valid_id(sender):
        raise ProtocolError("Invalid message type or sender")
    leader = value.get("leader_id")
    if kind == "HEARTBEAT":
        if not valid_id(leader) or leader != sender:
            raise ProtocolError("Heartbeat must be sent by its leader")
        return Message(kind, sender, leader_id=leader)
    origin = value.get("origin_id")
    round_id = value.get("election_id")
    participants = value.get("participants")
    if not valid_id(origin):
        raise ProtocolError("Invalid origin")
    if (not isinstance(round_id, list) or len(round_id) != 2
            or type(round_id[0]) is not int or round_id[0] != origin
            or type(round_id[1]) is not int or round_id[1] < 1):
        raise ProtocolError("Invalid election ID")
    if (not isinstance(participants, list) or not participants
            or not all(valid_id(item) for item in participants)
            or len(set(participants)) != len(participants)
            or participants[0] != origin or sender not in participants):
        raise ProtocolError("Invalid participant list")
    if kind == "COORDINATOR" and (not valid_id(leader) or leader != max(participants)):
        raise ProtocolError("Coordinator must be the highest participant")
    if kind == "ELECTION" and leader is not None:
        raise ProtocolError("ELECTION must not announce a leader")
    return Message(kind, sender, origin, tuple(round_id), tuple(participants), leader)


def advance_election(message: Message, node_id: int) -> Message:
    """Append once, or convert a completed collection at its origin."""
    if message.type != "ELECTION":
        raise ProtocolError("Expected ELECTION")
    if node_id == message.origin_id:
        return replace(message, type="COORDINATOR", sender_id=node_id,
                       leader_id=max(message.participants))
    if node_id in message.participants:
        raise ProtocolError("Election revisited a non-origin node")
    return replace(message, sender_id=node_id,
                   participants=message.participants + (node_id,))


def successors(node_id: int, members: tuple[int, ...]) -> tuple[int, ...]:
    index = members.index(node_id)
    return members[index + 1:] + members[:index]

