"""Run with: uv run python -m latency_lab.demo."""

import logging
import os
import signal
import time
from types import FrameType

from latency_lab.processes import LabServices
from latency_lab.proxy import parse_rtt


def interrupted(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupted)
    try:
        with LabServices(
            server_port=int(os.environ.get("SERVER_PORT", "3001")),
            proxy_port=int(os.environ.get("PROXY_PORT", "3000")),
            rtt_ms=parse_rtt(os.environ.get("RTT_MS", "0")),
            allow_test_reset=os.environ.get("ALLOW_TEST_RESET", "1") == "1",
        ) as services:
            logging.info("Demo: http://127.0.0.1:%s — Ctrl+C to stop", services.proxy_port)
            while True:
                services.check_running()
                time.sleep(0.2)
    except KeyboardInterrupt:
        logging.info("Stopped server and proxy.")


if __name__ == "__main__":
    main()
