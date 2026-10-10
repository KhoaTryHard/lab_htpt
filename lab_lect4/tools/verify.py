"""Run unittest and save machine-readable verification evidence."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class EvidenceStream:
    def __init__(self):
        self.buffer = io.StringIO()

    def write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()
        self.buffer.write(text)

    def flush(self):
        sys.stdout.flush()


def main():
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    stream = EvidenceStream()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = ROOT / "logs" / "verification"
    output.mkdir(parents=True, exist_ok=True)
    (output / "unittest.log").write_text(stream.buffer.getvalue(), encoding="utf-8")
    summary = {"time_utc": datetime.now(timezone.utc).isoformat(),
               "python": sys.version.split()[0], "tests": result.testsRun,
               "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(result.skipped), "passed": result.wasSuccessful(),
               "seconds": round(time.monotonic() - started, 3)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
