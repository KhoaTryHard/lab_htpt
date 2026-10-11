"""Bounded newline-framed JSON RPC over TCP; no shared-memory transport."""

import asyncio
import hashlib
import json

from .config import PARTICIPANTS

MAX_FRAME = 1024 * 1024
DECISIONS = {"GLOBAL_COMMIT", "GLOBAL_ABORT"}


class ProtocolError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def transaction_id(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ProtocolError("tx_id must be a nonempty string of at most 128 characters")
    return value


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_fingerprint(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise ProtocolError("Invalid transaction fingerprint")
    return value


def normalize_operations(operations):
    if not isinstance(operations, dict) or set(operations) != set(PARTICIPANTS):
        raise ProtocolError("operations must contain exactly P1, P2 and P3")
    result = {}
    for node in PARTICIPANTS:
        items = operations[node]
        if not isinstance(items, list) or not items:
            raise ProtocolError(f"{node} needs at least one SET operation")
        keys = set()
        normalized = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"op", "key", "value"}:
                raise ProtocolError("Each operation needs op, key, value")
            key = item["key"]
            if item["op"] != "SET" or not isinstance(key, str) or not key or len(key) > 256:
                raise ProtocolError("Only SET with a nonempty key (max 256 characters) is supported")
            if type(item["value"]) is not int or key in keys:
                raise ProtocolError("SET needs an integer value and unique keys per participant")
            keys.add(key)
            normalized.append(dict(item))
        result[node] = normalized
    return result


def envelope(kind, sender, tx_id=None, **fields):
    message = {"type": kind, "sender": sender, **fields}
    if tx_id is not None:
        message["tx_id"] = tx_id
    return message


def encode(message):
    raw = (canonical(message) + "\n").encode("utf-8")
    if len(raw) > MAX_FRAME:
        raise ProtocolError("Message exceeds 1 MiB")
    return raw


async def read_message(reader):
    try:
        raw = await reader.readline()
    except (ValueError, asyncio.LimitOverrunError) as exc:
        raise ProtocolError("Message exceeds framing limit") from exc
    if not raw or not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise ProtocolError("Missing, incomplete or oversized JSON frame")
    try:
        message = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Invalid UTF-8 JSON") from exc
    if (not isinstance(message, dict) or not isinstance(message.get("type"), str)
            or not isinstance(message.get("sender"), str)):
        raise ProtocolError("Message must contain type and sender")
    return message


async def request(address, message, timeout=2.0):
    async def exchange():
        reader, writer = await asyncio.open_connection(*address, limit=MAX_FRAME)
        try:
            writer.write(encode(message))
            await writer.drain()
            return await read_message(reader)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    return await asyncio.wait_for(exchange(), timeout)
