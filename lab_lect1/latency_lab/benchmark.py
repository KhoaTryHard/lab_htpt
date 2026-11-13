"""Measure frontend validation using Playwright Python against the Python services."""

import logging
import math
import os
import signal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import Request, Response, sync_playwright

from latency_lab.demo import interrupted
from latency_lab.processes import LabServices
from latency_lab.reporting import MODES, RTTS, write_reports
from latency_lab.server import ROOT

SCENARIO_DATA = {
    "invalid_format": "broken",
    "valid": "bench-valid@example.test",
    "duplicate": "taken@example.test",
}


def measure(iterations: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with LabServices(server_port=3101, proxy_port=3100), sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            with httpx.Client(base_url="http://127.0.0.1:3101", trust_env=False) as client:
                for rtt in RTTS:
                    for scenario, email in SCENARIO_DATA.items():
                        for mode in MODES:
                            for iteration in range(1, iterations + 1):
                                client.post("/api/test/reset").raise_for_status()
                                page.goto(f"http://127.0.0.1:3100/?rtt={rtt}")
                                page.locator(f'input[value="{mode}"]').check()
                                data = {
                                    "fullName": "Nguyễn Văn A",
                                    "email": email,
                                    "password": "password1",
                                    "confirmPassword": "password1",
                                }
                                for field, value in data.items():
                                    page.locator(f'[data-field="{field}"]').fill(value)
                                requests: list[Request] = []
                                responses: list[Response] = []

                                def on_request(request: Request, captured=requests) -> None:
                                    if urlsplit(request.url).path == "/api/register":
                                        captured.append(request)

                                def on_response(response: Response, captured=responses) -> None:
                                    if urlsplit(response.url).path == "/api/register":
                                        captured.append(response)

                                page.on("request", on_request)
                                page.on("response", on_response)
                                try:
                                    page.locator("#submit-button").click()
                                    page.wait_for_function("() => window.__lab.last !== null")
                                    result = page.evaluate("window.__lab.last")
                                    expected = (
                                        "client-validation-error"
                                        if scenario == "invalid_format" and mode == "client"
                                        else "success"
                                        if scenario == "valid"
                                        else "server-error"
                                    )
                                    count = 0 if expected == "client-validation-error" else 1
                                    status = (
                                        201
                                        if scenario == "valid"
                                        else (409 if scenario == "duplicate" else 422)
                                    )
                                    if result["outcome"] != expected or len(requests) != count:
                                        raise RuntimeError(
                                            f"Unexpected outcome: {rtt}/{scenario}/{mode}: "
                                            f"{result}, requests={len(requests)}"
                                        )
                                    if count and (
                                        result.get("status") != status or len(responses) != 1
                                    ):
                                        raise RuntimeError(f"Unexpected response: {result}")
                                    feedback = result["feedbackMs"]
                                    if not math.isfinite(feedback) or feedback < 0:
                                        raise RuntimeError(
                                            f"Invalid feedback measurement: {feedback}"
                                        )
                                    rows.append(
                                        {
                                            "rtt": rtt,
                                            "scenario": scenario,
                                            "mode": mode,
                                            "iteration": iteration,
                                            "feedback_ms": feedback,
                                            "http_request_count": len(requests),
                                            "request_body_bytes": sum(
                                                len(request.post_data_buffer or b"")
                                                for request in requests
                                            ),
                                            "response_body_bytes": sum(
                                                len(response.body()) for response in responses
                                            ),
                                            "outcome": result["outcome"],
                                        }
                                    )
                                finally:
                                    page.remove_listener("request", on_request)
                                    page.remove_listener("response", on_response)
                            logging.info(
                                "Measured RTT=%s %s/%s (%s runs)", rtt, scenario, mode, iterations
                            )
        finally:
            browser.close()
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupted)
    iterations = int(os.environ.get("BENCH_ITERATIONS", "30"))
    if iterations < 1:
        raise ValueError("BENCH_ITERATIONS must be a positive integer")
    output_dir = Path(os.environ.get("BENCH_OUTPUT_DIR", str(ROOT / "reports"))).resolve()
    try:
        rows = measure(iterations)
        write_reports(rows, output_dir, iterations)
        logging.info("Wrote %s benchmark rows to %s", len(rows), output_dir)
    except KeyboardInterrupt:
        logging.info("Benchmark interrupted; server, proxy, and browser stopped.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
