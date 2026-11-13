"""Own and clean up the two single-process services, including on Windows."""

import os
import socket
import subprocess
import sys
import time
from types import TracebackType

import httpx

from latency_lab.server import ROOT


class LabServices:
    def __init__(
        self,
        *,
        server_port: int = 3001,
        proxy_port: int = 3000,
        rtt_ms: float = 0,
        allow_test_reset: bool = True,
    ) -> None:
        self.server_port = server_port
        self.proxy_port = proxy_port
        self.rtt_ms = rtt_ms
        self.allow_test_reset = allow_test_reset
        self.children: list[subprocess.Popen] = []

    def check_running(self) -> None:
        for child in self.children:
            if child.poll() is not None:
                raise RuntimeError(f"Lab service exited unexpectedly (code {child.returncode})")

    def __enter__(self) -> "LabServices":
        if self.server_port == self.proxy_port:
            raise ValueError("SERVER_PORT and PROXY_PORT must differ")
        # Fail early instead of accidentally using an unrelated service on an occupied port.
        for port in (self.server_port, self.proxy_port):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        env = {
            **os.environ,
            "SERVER_PORT": str(self.server_port),
            "PROXY_PORT": str(self.proxy_port),
            "RTT_MS": str(self.rtt_ms),
            "ALLOW_TEST_RESET": "1" if self.allow_test_reset else "0",
            "PYTHONUTF8": "1",
        }
        try:
            for module in ("latency_lab.server", "latency_lab.proxy"):
                child = subprocess.Popen(
                    [sys.executable, "-m", module],
                    cwd=ROOT,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
                self.children.append(child)
            deadline = time.monotonic() + 15
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                while time.monotonic() < deadline:
                    self.check_running()
                    try:
                        response = client.get(f"http://127.0.0.1:{self.proxy_port}/api/health")
                        if response.is_success and response.json().get("ok") is True:
                            return self
                    except (httpx.RequestError, ValueError):
                        pass
                    time.sleep(0.1)
            raise RuntimeError("Timed out waiting for lab services")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        for child in reversed(self.children):
            if child.poll() is None:
                if os.name == "nt":
                    # Windows venv python.exe may launch another interpreter process.
                    subprocess.run(
                        ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    child.terminate()
        for child in self.children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        self.children.clear()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
