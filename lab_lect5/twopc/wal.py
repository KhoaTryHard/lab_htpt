"""Synchronous WAL. A complete corrupt record is never silently skipped."""

import json
import os
from pathlib import Path

from .protocol import canonical


class StorageError(RuntimeError):
    pass


class LogCorruption(StorageError):
    pass


class WAL:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records = []
        self.failed = False
        try:
            with self.path.open("a+b") as stream:
                stream.seek(0)
                raw = stream.read()
                complete = raw.rfind(b"\n") + 1
                for index, line in enumerate(raw[:complete].splitlines(), 1):
                    try:
                        record = json.loads(line.decode("utf-8"))
                    except (ValueError, UnicodeError) as exc:
                        raise LogCorruption(f"Invalid WAL record {index}") from exc
                    if (not isinstance(record, dict)
                            or set(record) != {"seq", "event", "tx_id", "data"}
                            or type(record["seq"]) is not int or record["seq"] != index
                            or not isinstance(record["event"], str)
                            or not isinstance(record["data"], dict)
                            or (record["tx_id"] is not None
                                and not isinstance(record["tx_id"], str))):
                        raise LogCorruption(f"Invalid WAL structure/sequence at {index}")
                    self.records.append(record)
                if complete != len(raw):
                    stream.seek(complete)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as exc:
            raise StorageError(f"Cannot open/recover WAL: {exc}") from exc

    def append(self, event, tx_id=None, **data):
        if self.failed:
            raise StorageError("WAL is poisoned after a failed append; restart required")
        record = {"seq": len(self.records) + 1, "event": event,
                  "tx_id": tx_id, "data": data}
        raw = (canonical(record) + "\n").encode("utf-8")
        try:
            with self.path.open("ab") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            self.failed = True
            raise StorageError(f"WAL append/fsync failed: {exc}") from exc
        self.records.append(record)
        return record
