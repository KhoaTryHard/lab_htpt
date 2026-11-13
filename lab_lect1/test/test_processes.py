"""Run real services to verify shutdown releases both listening ports."""

import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from latency_lab.processes import LabServices
from latency_lab.server import ROOT


def free_ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def assert_ports_released(ports: tuple[int, int]) -> None:
    for port in ports:
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_context_cleans_up_on_failure() -> None:
    ports = free_ports()
    with pytest.raises(RuntimeError, match="test interruption"):
        with LabServices(server_port=ports[0], proxy_port=ports[1]) as services:
            children = list(services.children)
            raise RuntimeError("test interruption")
    assert all(child.poll() is not None for child in children)
    assert_ports_released(ports)


def test_demo_interrupt_releases_both_ports() -> None:
    ports = free_ports()
    child = subprocess.Popen(
        [sys.executable, "-m", "latency_lab.demo"],
        cwd=ROOT,
        env={**os.environ, "SERVER_PORT": str(ports[0]), "PROXY_PORT": str(ports[1])},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert child.poll() is None, "Demo exited before it became ready"
                try:
                    if client.get(f"http://127.0.0.1:{ports[1]}/api/health").is_success:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Demo did not become ready")
        child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        assert child.wait(timeout=15) == 0
        assert_ports_released(ports)
    finally:
        if child.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], check=False)
            else:
                child.terminate()
            child.wait(timeout=10)
