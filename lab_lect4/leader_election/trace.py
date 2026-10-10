from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time


class Trace:
    """Serialize complete records to console and both evidence files."""

    def __init__(self, log_dir: str | Path | None = None, console: bool = True):
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.console = console
        self.records: list[dict] = []
        self.text_file = self.json_file = None
        if log_dir is not None:
            directory = Path(log_dir)
            directory.mkdir(parents=True, exist_ok=True)
            self.text_file = (directory / "trace.log").open("w", encoding="utf-8")
            self.json_file = (directory / "trace.jsonl").open("w", encoding="utf-8")

    def emit(self, node: int | None, event: str, detail: str, **fields):
        with self.lock:
            elapsed = time.monotonic() - self.started
            record = {"time_utc": datetime.now(timezone.utc).isoformat(),
                      "elapsed": round(elapsed, 6), "node": node,
                      "event": event, "detail": detail, **fields}
            self.records.append(record)
            label = "SYSTEM" if node is None else f"P{node}"
            line = f"{elapsed:8.3f}s [{label}] {event}: {detail}"
            if fields.get("election_id"):
                origin, counter = fields["election_id"]
                line += f" | round=P{origin}:{counter}"
            if "participants" in fields:
                line += f" ids={list(fields['participants'])}"
            if self.console:
                print(line, flush=True)
            if self.text_file:
                self.text_file.write(line + "\n")
                self.text_file.flush()
                self.json_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.json_file.flush()

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [dict(record) for record in self.records]

    def close(self):
        with self.lock:
            for stream in (self.text_file, self.json_file):
                if stream:
                    stream.close()
            self.text_file = self.json_file = None


def election_counts(records: list[dict]) -> dict[str, int]:
    """Count actual received protocol frames, excluding heartbeat and failed sends."""
    counts = {}
    for record in records:
        if record["event"] == "RECV" and record.get("message_type") in ("ELECTION", "COORDINATOR"):
            origin, counter = record["election_id"]
            key = f"P{origin}:{counter}"
            counts[key] = counts.get(key, 0) + 1
    return counts

