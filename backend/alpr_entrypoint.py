"""ALPR investigation entrypoint — minimal platform hook.

This is a NEW, separate entrypoint: it does not modify runtime.mcp_specialist
(the existing fixture MCP server keeps its own entrypoint and behaviour) or
any other platform module, so original behaviour is fully preserved.

Modes are explicit — there is no default:

- ``--mode offline-fixture``: runs against tests.alpr_fixture (labelled
  NOT LIVE / synthetic in output). Useful for demos and CI.
- ``--mode live``: builds the live reader via backend.alpr_live.build_reader.
  Requires --config pointing at a JSON file with the full live configuration
  including ``live_confirmed: true``, non-bootstrap user, role
  GAB_QUERY_READONLY and a private key path. Any gap => exit 2 (fail closed).
  This slice ships no credentials; live execution additionally needs an admin
  GRANT of GAB_QUERY_READONLY to a query user.

Usage:
    python -m backend.alpr_entrypoint --mode offline-fixture --eval
    python -m backend.alpr_entrypoint --mode offline-fixture --case CASE-001
    python -m backend.alpr_entrypoint --mode live --config live.json --case CASE-001
"""
import argparse
import json
import sys

from backend.alpr_live import AlprLiveConfigError, build_reader
from backend.alpr_reader import AlprReaderDenied
from backend.alpr_specialists import investigate_case


def _decimal_safe(obj):
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON serializable: {type(obj)}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="alpr-investigate", description=__doc__)
    parser.add_argument("--mode", required=True, choices=["offline-fixture", "live"],
                        help="explicit execution mode; there is no default")
    parser.add_argument("--config", help="JSON config file (required for live)")
    parser.add_argument("--case", help="single case id, e.g. CASE-001")
    parser.add_argument("--eval", action="store_true",
                        help="run the six-case eval (offline-fixture only)")
    args = parser.parse_args(argv)

    try:
        if args.mode == "offline-fixture":
            from tests.alpr_fixture import FakeSqlApiTransport
            reader_factory = lambda: build_reader(  # noqa: E731
                {"mode": "offline"}, transport=FakeSqlApiTransport())
        else:
            if args.eval:
                print("--eval is offline-only: gold must stay on the eval side; "
                      "live acceptance uses the probe plan instead", file=sys.stderr)
                return 2
            config = {}
            if args.config:
                config = json.loads(open(args.config).read())
            live_reader = build_reader(config)  # raises unless fully confirmed
            reader_factory = lambda: live_reader  # noqa: E731

        if args.eval:
            from backend.alpr_eval import run_eval
            results = run_eval(lambda case_id: investigate_case(reader_factory(), case_id))
            print(json.dumps(results, indent=2, default=_decimal_safe))
            return 0 if results["passed"] == results["total"] else 1
        if not args.case:
            parser.error("--case or --eval required")
        report = investigate_case(reader_factory(), args.case)
        print(json.dumps(report, indent=2, default=_decimal_safe))
        return 0
    except (AlprLiveConfigError, AlprReaderDenied) as error:
        print(f"REFUSED (fail closed): {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
