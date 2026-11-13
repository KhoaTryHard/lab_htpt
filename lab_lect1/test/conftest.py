"""Shared API and process fixtures."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from latency_lab.server import create_app


@pytest.fixture
def valid() -> dict[str, str]:
    dataset = Path(__file__).parent / "fixtures" / "validation.json"
    return json.loads(dataset.read_text(encoding="utf-8"))["valid"]


@pytest.fixture
def client():
    with TestClient(create_app(allow_test_reset=True)) as api:
        yield api
