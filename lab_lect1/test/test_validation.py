"""One data set verifies Python rules and parity with the shipped browser module."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from latency_lab.server import ROOT
from latency_lab.validation import validate_registration

DATASET = json.loads(
    (Path(__file__).parent / "fixtures/validation.json").read_text(encoding="utf-8")
)
CASES = [
    (case["name"], {**DATASET["valid"], **case["changes"]}, case["errors"])
    for case in DATASET["cases"]
]
CASES += [
    ("missing-fields", {}, ["fullName", "email", "password", "confirmPassword"]),
    ("null", None, ["fullName", "email", "password", "confirmPassword"]),
    ("array", [], ["fullName", "email", "password", "confirmPassword"]),
]
for field, lengths in (
    ("fullName", (2, 80, 81)),
    ("password", (7, 8, 64, 65)),
    ("email", (254, 255)),
):
    for length in lengths:
        data = dict(DATASET["valid"])
        value = "a" * length if field != "email" else "a" * (length - 5) + "@b.co"
        data[field] = value
        if field == "password":
            data["confirmPassword"] = value
        lower, upper = (
            (2, 80) if field == "fullName" else (8, 64) if field == "password" else (1, 254)
        )
        CASES.append((f"{field}-{length}", data, [] if lower <= length <= upper else [field]))
for count in (40, 41):
    CASES.append(
        (
            f"emoji-{count}",
            {**DATASET["valid"], "fullName": "😀" * count},
            [] if count == 40 else ["fullName"],
        )
    )


@pytest.mark.parametrize("name,data,errors", CASES, ids=[case[0] for case in CASES])
def test_validation_rules(name, data, errors) -> None:
    checked = validate_registration(data)
    assert list(checked["errors"]) == errors
    assert checked["valid"] is (not errors)


def test_python_matches_javascript() -> None:
    # Node stays a documented prerequisite because Playwright Test UI uses it.
    node = shutil.which("node")
    assert node, "Install Node.js to verify browser validation parity and run Playwright UI"
    script = """
import { validateRegistration } from './shared/validation.mjs';
let input = '';
for await (const chunk of process.stdin) input += chunk;
process.stdout.write(JSON.stringify(JSON.parse(input).map(validateRegistration)));
"""
    values = [case[1] for case in CASES]
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT,
        input=json.dumps(values, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout) == [validate_registration(value) for value in values]


def test_normalization_and_messages() -> None:
    result = validate_registration({**DATASET["valid"], "email": "  A@EXAMPLE.TEST  "})
    assert result["value"]["email"] == "a@example.test"
    assert validate_registration({})["errors"]["fullName"] == "Họ tên là bắt buộc."
