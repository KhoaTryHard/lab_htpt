"""Run the default scenario repeatedly and preserve every real trace."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leader_election.runner import run_demo
from leader_election.trace import Trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--base-port", type=int, default=55100)
    parser.add_argument("--output", default="logs/stress")
    args = parser.parse_args()
    if args.runs < 1 or not 1024 <= args.base_port <= 65531:
        parser.error("runs must be positive and base-port must allow five valid ports")
    output = Path(args.output)
    results = []
    for index in range(1, args.runs + 1):
        trace = Trace(output / f"run-{index:02d}", console=False)
        try:
            addresses = {node: ("127.0.0.1", args.base_port + node - 1) for node in range(1, 6)}
            result = run_demo(addresses, trace)
            results.append({"run": index, **result.__dict__})
            print(f"Run {index:02d}/{args.runs}: {'PASS' if result.passed else 'FAIL'} "
                  f"failover={result.failover_seconds:.3f}s rounds={result.completed_rounds}", flush=True)
        finally:
            trace.close()
    summary = {"runs": args.runs, "passed": sum(r["passed"] for r in results),
               "results": results}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Summary: {summary['passed']}/{args.runs} PASS", flush=True)
    return 0 if summary["passed"] == args.runs else 1


if __name__ == "__main__":
    sys.exit(main())
