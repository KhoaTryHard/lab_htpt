"""Write the same CSV/JSON contract and a readable, standalone HTML report."""

import csv
import html
import json
import math
from pathlib import Path
from typing import Any

RTTS = (0, 200, 500, 1000)
SCENARIOS = ("invalid_format", "valid", "duplicate")
MODES = ("server", "client")
CSV_FIELDS = (
    "rtt",
    "scenario",
    "mode",
    "iteration",
    "feedback_ms",
    "http_request_count",
    "request_body_bytes",
    "response_body_bytes",
    "outcome",
)


def percentile(values: list[float], percent: int) -> float:
    """Nearest-rank percentile, matching the former JavaScript implementation."""
    return sorted(values)[max(0, math.ceil(percent / 100 * len(values)) - 1)]


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for rtt in RTTS:
        for scenario in SCENARIOS:
            for mode in MODES:
                selected = [
                    row
                    for row in rows
                    if (row["rtt"], row["scenario"], row["mode"]) == (rtt, scenario, mode)
                ]
                timings = [row["feedback_ms"] for row in selected]
                summary.append(
                    {
                        "rtt": rtt,
                        "scenario": scenario,
                        "mode": mode,
                        "n": len(selected),
                        "median_feedback_ms": percentile(timings, 50),
                        "p95_feedback_ms": percentile(timings, 95),
                        "requests": sum(row["http_request_count"] for row in selected),
                        "request_body_bytes": sum(row["request_body_bytes"] for row in selected),
                        "response_body_bytes": sum(row["response_body_bytes"] for row in selected),
                        "outcomes": "|".join(dict.fromkeys(row["outcome"] for row in selected)),
                    }
                )
    return summary


def render_analysis(summary: list[dict[str, Any]], total: int, iterations: int) -> str:
    lines = [
        "# Phân tích benchmark",
        "",
        f"Ma trận gồm {total} lượt đo: {iterations} lượt cho mỗi RTT, tình huống và chế độ. "
        "Backend và proxy dùng Python/FastAPI. RTT được chia đều trước request và response.",
        "",
        "| RTT | Server validation median (ms) | Local validation median (ms) "
        "| Request server/local |",
        "|---:|---:|---:|---:|",
    ]
    for rtt in RTTS:
        server, client = [
            row for row in summary if row["rtt"] == rtt and row["scenario"] == "invalid_format"
        ]
        lines.append(
            f"| {rtt} | {server['median_feedback_ms']:.1f} | "
            f"{client['median_feedback_ms']:.1f} | {server['requests']} / {client['requests']} |"
        )
    lines += [
        "",
        "Với lỗi định dạng, local validation phản hồi trong thời gian xử lý JavaScript và không "
        "gửi request. Server validation cần một round-trip nên thời gian tăng theo RTT. Dữ liệu "
        "hợp lệ hoặc email trùng vẫn cần server trong cả hai chế độ.",
        "",
        "## Liên hệ 8 giả định sai",
        "",
        "- **Latency is zero:** kiểm chứng trực tiếp bằng chênh lệch khi RTT tăng.",
        "- **Bandwidth is infinite / Transport cost is zero:** local validation tránh request "
        "và body không cần thiết cho lỗi định dạng.",
        "- **The network is reliable:** UI xử lý lỗi kết nối; local validation vẫn phát hiện "
        "lỗi định dạng nhưng không đảm bảo đăng ký được khi mạng hỏng.",
        "- **The network is secure / homogeneous / topology does not change / one administrator:** "
        "không được giải quyết bởi validation; cần cơ chế riêng.",
        "",
        "Đây là mô phỏng localhost, không đại diện cho packet loss, jitter, TLS handshake hoặc "
        "WAN bandwidth. Byte đo là body, không gồm header/TLS và chi phí tải JavaScript ban đầu. "
        "feedback_ms đo đến cập nhật DOM, chưa tính lúc trình duyệt hoàn tất paint.",
    ]
    return "\n".join(lines) + "\n"


def render_html(summary: list[dict[str, Any]]) -> str:
    cards = []
    for scenario in SCENARIOS:
        lines = [f'      <article class="card"><strong>{scenario}</strong>']
        for rtt in RTTS:
            pair = [row for row in summary if row["scenario"] == scenario and row["rtt"] == rtt]
            maximum = max(1, *(row["median_feedback_ms"] for row in pair))
            lines.append(f"        <div><small>{rtt} ms</small>")
            for row in pair:
                width = max(2, min(100, row["median_feedback_ms"] / maximum * 100))
                lines += [
                    f"          <div><small>{row['mode']}: {row['median_feedback_ms']:.1f} ms "
                    f"({row['requests']} req)</small>",
                    f'            <div class="bar {row["mode"]}" style="width:{width:.2f}%"></div>',
                    "          </div>",
                ]
            lines.append("        </div>")
        lines.append("      </article>")
        cards.append("\n".join(lines))
    table_rows = []
    for row in summary:
        cells = (
            f"{row['rtt']} ms",
            row["scenario"],
            row["mode"],
            row["n"],
            f"{row['median_feedback_ms']:.1f}",
            f"{row['p95_feedback_ms']:.1f}",
            row["requests"],
            row["request_body_bytes"],
            row["response_body_bytes"],
            row["outcomes"],
        )
        table_rows.append(
            "        <tr>\n"
            + "\n".join(f"          <td>{html.escape(str(cell))}</td>" for cell in cells)
            + "\n        </tr>"
        )
    return (
        """<!doctype html>
<!-- Bao cao benchmark Python/FastAPI; co the mo truc tiep trong trinh duyet. -->
<html lang="vi">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Latency Hiding Benchmark</title>
    <style>
      body { font: 15px system-ui; background: #091423; color: #eaf4ff; margin: 32px; }
      main { max-width: 1200px; margin: auto; }
      h1 { color: #63d9ed; }
      p { color: #a9bad0; }
      .chart { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 14px; }
      .card { border: 1px solid #2a4668; border-radius: 12px; padding: 14px;
        background: #10223a; }
      .bar { height: 12px; background: #63d9ed; border-radius: 5px; margin: 5px 0 12px; }
      .bar.client { background: #a3edbb; }
      .table-scroll { overflow-x: auto; }
      table { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 13px; }
      th, td { border-bottom: 1px solid #263d58; padding: 7px; text-align: left; }
      th { color: #ffd38a; }
    </style>
  </head>
  <body>
    <main>
      <h1>Latency Hiding · Benchmark</h1>
      <p>Backend Python/FastAPI. Median feedback theo round-trip mô phỏng.
        Local validation giữ lỗi định dạng ở trình duyệt; server validation chờ response.</p>
      <section class="chart" id="chart">
"""
        + "\n".join(cards)
        + """
      </section>
      <div class="table-scroll">
        <table>
          <thead>
            <tr><th>RTT</th><th>Tình huống</th><th>Mode</th><th>n</th><th>Median ms</th>
              <th>P95 ms</th><th>Requests</th><th>Request bytes</th><th>Response bytes</th>
              <th>Outcome</th></tr>
          </thead>
          <tbody id="rows">
"""
        + "\n".join(table_rows)
        + """
          </tbody>
        </table>
      </div>
    </main>
  </body>
</html>
"""
    )


def write_reports(rows: list[dict[str, Any]], output_dir: Path, iterations: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "benchmark.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "analysis.md").write_text(
        render_analysis(summary, len(rows), iterations), encoding="utf-8"
    )
    (output_dir / "report.html").write_text(render_html(summary), encoding="utf-8")
