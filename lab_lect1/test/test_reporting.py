"""Keep the original percentile definition and report file contracts."""

import csv
import json

from latency_lab.reporting import CSV_FIELDS, MODES, RTTS, SCENARIOS, percentile, write_reports


def test_nearest_rank_matches_original_even_sample_size() -> None:
    assert percentile([10, 20], 50) == 10
    assert percentile([10, 20], 95) == 20
    assert percentile([7], 50) == 7


def test_report_contracts(tmp_path) -> None:
    rows = []
    for rtt in RTTS:
        for scenario in SCENARIOS:
            for mode in MODES:
                local_error = scenario == "invalid_format" and mode == "client"
                rows.append(
                    {
                        "rtt": rtt,
                        "scenario": scenario,
                        "mode": mode,
                        "iteration": 1,
                        "feedback_ms": 0.1 if local_error else rtt + 10,
                        "http_request_count": 0 if local_error else 1,
                        "request_body_bytes": 0 if local_error else 100,
                        "response_body_bytes": 0 if local_error else 80,
                        "outcome": "client-validation-error"
                        if local_error
                        else ("success" if scenario == "valid" else "server-error"),
                    }
                )
    write_reports(rows, tmp_path, iterations=1)
    with (tmp_path / "benchmark.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == list(CSV_FIELDS)
        assert len(list(reader)) == 24
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert len(summary) == 24
    assert summary[0]["median_feedback_ms"] == 10
    assert summary[1]["requests"] == 0
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert report.count('<article class="card">') == 3
    assert report.count("<td>") == 240
    assert "Latency Hiding" in report
    assert "Latency is zero" in (tmp_path / "analysis.md").read_text(encoding="utf-8")
