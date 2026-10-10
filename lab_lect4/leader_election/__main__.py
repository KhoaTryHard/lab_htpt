import argparse
import sys

from .runner import run_demo
from .trace import Trace


def main(argv=None):
    parser = argparse.ArgumentParser(description="Five-thread TCP Ring election: P5 crash -> P4")
    parser.add_argument("--base-port", type=int, default=55000)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--quiet", action="store_true", help="Write log files without console trace")
    args = parser.parse_args(argv)
    if not 1024 <= args.base_port <= 65531:
        parser.error("base-port must be between 1024 and 65531")
    trace = Trace(args.log_dir, console=not args.quiet)
    try:
        addresses = {node_id: ("127.0.0.1", args.base_port + node_id - 1)
                     for node_id in range(1, 6)}
        result = run_demo(addresses, trace)
        return 0 if result.passed else 1
    except KeyboardInterrupt:
        trace.emit(None, "INTERRUPTED", "Ctrl+C; all node threads and sockets cleaned up")
        return 130
    finally:
        trace.close()


if __name__ == "__main__":
    sys.exit(main())
