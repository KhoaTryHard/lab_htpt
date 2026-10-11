"""Command-line entry points for servers, clients and reproducible demos."""

import argparse
import asyncio
import json
import math
import sys
import time
import uuid
from pathlib import Path

from .config import Config, PARTICIPANTS
from .harness import Cluster, SCENARIOS, exercise
from .nodes import Coordinator, FAULT_POINTS, Faults, Participant
from .protocol import ProtocolError, envelope, request
from .wal import StorageError


def parser():
    root = argparse.ArgumentParser(description="TCP 2PC simulator with durable WAL and crash recovery")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("coordinator", "participant"):
        item = commands.add_parser(name)
        item.add_argument("--config", default="config.json")
        item.add_argument("--failpoint", action="append", choices=FAULT_POINTS, default=[])
        item.add_argument("--fault-tx", help="Only inject faults for this tx_id")
        if name == "participant":
            item.add_argument("--id", required=True, choices=PARTICIPANTS)
            item.add_argument("--lock-key", action="append", default=[],
                              help="Simulate a local lock conflict on this key")
    item = commands.add_parser("submit")
    item.add_argument("--config", default="config.json")
    item.add_argument("--tx-id", required=True)
    item.add_argument("--file", required=True, help="JSON operations keyed by P1/P2/P3")
    item.add_argument("--wait", type=float, default=15.0,
                      help="Seconds to poll completion; 0 returns acceptance immediately")
    item = commands.add_parser("status")
    item.add_argument("--config", default="config.json")
    item.add_argument("--tx-id", required=True)
    item.add_argument("--node", choices=("COORD", *PARTICIPANTS), default="COORD")
    item = commands.add_parser("get")
    item.add_argument("--config", default="config.json")
    item.add_argument("--node", required=True, choices=PARTICIPANTS)
    item.add_argument("--key", required=True)
    item = commands.add_parser("demo")
    item.add_argument("--scenario", choices=(*SCENARIOS, "all"), default="all-commit")
    item.add_argument("--output-dir", help="New empty directory for logs, WALs and demo config")
    item.add_argument("--quiet", action="store_true", help="Print only the result JSON")
    return root


async def client(args, config):
    if args.command == "get":
        return await request(config.address(args.node), envelope("GET", "CLIENT", key=args.key),
                             config.request_timeout)
    if args.command == "status":
        return await request(config.address(args.node),
                             envelope("STATUS", "CLIENT", args.tx_id), config.request_timeout)
    if not math.isfinite(args.wait) or args.wait < 0:
        raise ValueError("--wait must be finite and nonnegative")
    operations = json.loads(Path(args.file).read_text(encoding="utf-8"))
    try:
        result = await request(config.address("COORD"),
                               envelope("SUBMIT", "CLIENT", args.tx_id, operations=operations),
                               config.request_timeout)
    except (OSError, TimeoutError, ProtocolError) as exc:
        return {"type": "UNCONFIRMED", "tx_id": args.tx_id,
                "message": f"Submission response unavailable ({exc}); query STATUS using the same tx_id"}
    deadline = asyncio.get_running_loop().time() + args.wait
    while result.get("type") == "STATUS" and not result.get("completed") and args.wait > 0:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            result["message"] = "Wait deadline reached; decision/completion may still progress. Query STATUS."
            break
        await asyncio.sleep(min(config.retry_interval, remaining))
        try:
            result = await request(config.address("COORD"),
                                   envelope("STATUS", "CLIENT", args.tx_id), config.request_timeout)
        except (OSError, TimeoutError, ProtocolError):
            continue
    return result


def demo(args):
    directory = (Path(args.output_dir) if args.output_dir else
                 Path("runs") / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}").resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Demo output directory must be new or empty (existing WALs are preserved)")
    directory.mkdir(parents=True, exist_ok=True)
    scenarios = SCENARIOS if args.scenario == "all" else (args.scenario,)
    results = []
    for name in scenarios:
        with Cluster(directory / name) as cluster:
            try:
                result = exercise(cluster, name)
            except Exception:
                print(cluster.diagnostics(), file=sys.stderr)
                raise
        if not args.quiet:
            print(f"\n=== {name}: per-node trace (no global clock ordering) ===")
            print(cluster.diagnostics())
        results.append({"scenario": name, "passed": True,
                        "decision": result["decision"], "completed": result["completed"],
                        "artifacts": str(cluster.directory)})
    return {"results": results, "output_dir": str(directory)}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            result = demo(args)
        else:
            config = Config.load(args.config)
            if args.command in {"coordinator", "participant"}:
                allowed = ({"after_start", "before_decision", "after_decision", "after_first_decision"}
                           if args.command == "coordinator" else
                           {"after_init", "after_prepare", "after_decision", "omit_vote", "drop_ack"})
                if not set(args.failpoint) <= allowed:
                    raise ValueError("Failpoint does not apply to this server role")
                faults = Faults(args.failpoint, args.fault_tx)
                node = (Coordinator(config, faults) if args.command == "coordinator" else
                        Participant(config, args.id, faults, args.lock_key))
                asyncio.run(node.serve())
                return 0
            result = asyncio.run(client(args, config))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("type") == "ERROR":
            return 1
        if result.get("type") == "UNCONFIRMED":
            return 2
        if args.command == "submit" and args.wait > 0 and not result.get("completed"):
            return 2
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, StorageError, AssertionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
