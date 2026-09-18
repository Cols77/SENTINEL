"""CLI entry point: ``python -m sentinel.contracts validate <file> [--schema NAME]``.

Thin wrapper over :func:`sentinel.contracts.schemas.iter_validation_errors`; no
validation logic lives here.

Exit codes: 0 valid (prints ``OK``), 1 invalid (machine-readable error list on
stdout), 2 usage or file-not-found error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sentinel.contracts.schemas import iter_validation_errors

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sentinel.contracts",
        description="Validate a JSON document against a SENTINEL contract schema.",
        add_help=False,
    )
    parser.add_argument("command", choices=["validate"], help="Subcommand.")
    parser.add_argument("file", type=Path, help="Path to the JSON document.")
    parser.add_argument(
        "--schema",
        default="root",
        metavar="NAME",
        help="Schema to validate against (default: root).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] != "validate":
        _build_parser().print_usage(sys.stderr)
        return EXIT_USAGE

    parser = _build_parser()
    args = parser.parse_args(argv)
    path: Path = args.file

    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return EXIT_USAGE

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(json.dumps([{"code": "INVALID_JSON", "message": str(exc)}]))
        return EXIT_INVALID

    try:
        errors = list(iter_validation_errors(document, schema=args.schema))
    except ValueError as exc:
        parser.error(str(exc))  # exits with status 2

    if errors:
        payload = [{"code": "INVALID_CONTRACT", "message": error.message} for error in errors]
        print(json.dumps(payload, indent=2))
        return EXIT_INVALID

    print("OK")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
