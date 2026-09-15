"""Command-line entry points for dry-run, supervised real runs, and emergency."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

from .emergency import EmergencyHotkey, EmergencySender
from .models import RunReport
from .reporting import write_report

try:  # Task 1/3 may be landed independently in the shared worktree.
    from .dry_run import DryRunClient
except ImportError:  # pragma: no cover - temporary clean-baseline state
    DryRunClient = None  # type: ignore[assignment,misc]

try:
    from .runner import FlightTestRunner
except ImportError:  # pragma: no cover - temporary clean-baseline state
    FlightTestRunner = None  # type: ignore[assignment,misc]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run the approved command sequence")
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--real", action="store_true")
    run.add_argument("--log", type=Path, default=Path("artifacts/run.jsonl"))
    run.add_argument("--tello-ip", default="192.168.10.1")
    run.add_argument("--timeout", type=float, default=7.0)
    run.add_argument("--local-ip")
    run.add_argument("--interface-name")
    run.add_argument("--interface-index", type=int)
    run.add_argument("--min-battery", type=int, default=40)
    run.add_argument("--confirm", action="store_true")

    emergency = subparsers.add_parser(
        "emergency", help="send an immediate motor-stop command"
    )
    emergency.add_argument("--tello-ip", default="192.168.10.1")
    emergency.add_argument("--local-ip")
    emergency.add_argument("--interface-name")
    emergency.add_argument("--interface-index", type=int)
    return parser


def _select_adapter(args: Any) -> tuple[Any, Any, Any]:
    from .network import (
        NetworkAdapterSelector,
        capture_default_route,
        discover_adapters,
    )

    before = capture_default_route()
    adapters = tuple(discover_adapters())
    if getattr(before, "source_ip", None):
        adapters = tuple(
            replace(adapter, is_default_route=True)
            if adapter.local_ip == before.source_ip
            else adapter
            for adapter in adapters
        )

    selected = NetworkAdapterSelector(adapters).select(
        local_ip=getattr(args, "local_ip", None),
        interface_name=getattr(args, "interface_name", None),
        interface_index=getattr(args, "interface_index", None),
    )
    after = capture_default_route()
    NetworkAdapterSelector.assert_default_route_unchanged(before, after)
    return selected, before, after


def resolve_local_ip(args: Any) -> str:
    selected, _before, _after = _select_adapter(args)
    return selected.local_ip


def build_real_client(args: Any) -> Any:
    selected, before, after = _select_adapter(args)
    try:
        from .djitellopy_backend import DjitellopyClient
    except ImportError:  # compatibility with the earlier transport name
        from .udp_client import UdpTelloClient as DjitellopyClient

    client = DjitellopyClient(
        local_ip=selected.local_ip,
        tello_ip=getattr(args, "tello_ip", "192.168.10.1"),
        timeout=getattr(args, "timeout", 7.0),
    )
    # Keep orchestration metadata at the application boundary without making
    # the DroneClient protocol carry network-selector implementation details.
    client._sentinel_adapter = selected
    client._sentinel_route_before = before
    client._sentinel_route_after = after
    from .network import capture_default_route

    client._sentinel_route_provider = capture_default_route
    return client


def _status_exit_code(status: str) -> int:
    return {"PASS": 0, "FAIL": 1, "EMERGENCY_ABORTED": 2}.get(status, 1)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "emergency":
        try:
            local_ip = resolve_local_ip(args)
            EmergencySender(local_ip=local_ip, tello_ip=args.tello_ip).send()
            return 0
        except Exception as exc:
            print(f"Emergency send failed: {exc}")
            return 1

    if args.real and not args.confirm:
        print("Real mode requires --confirm after the physical preflight.")
        return 1

    try:
        if args.dry_run:
            if DryRunClient is None:
                from .dry_run import DryRunClient as client_type
            else:
                client_type = DryRunClient
            client = client_type()
        else:
            client = build_real_client(args)

        if FlightTestRunner is None:
            from .runner import FlightTestRunner as runner_type
        else:
            runner_type = FlightTestRunner
        runner_kwargs = {}
        if args.real:
            runner_kwargs = {
                "adapter": getattr(client, "_sentinel_adapter", None),
                "route_before": getattr(client, "_sentinel_route_before", None),
                "route_after": getattr(client, "_sentinel_route_after", None),
                "route_snapshot_provider": getattr(
                    client, "_sentinel_route_provider", None
                ),
            }
        runner = runner_type(
            client,
            "dry-run" if args.dry_run else "real",
            args.min_battery,
            **runner_kwargs,
        )
        for attr in ("_sentinel_adapter", "_sentinel_route_before", "_sentinel_route_after"):
            if hasattr(client, attr):
                setattr(
                    runner,
                    {
                        "_sentinel_adapter": "adapter",
                        "_sentinel_route_before": "route_before",
                        "_sentinel_route_after": "route_after",
                    }[attr],
                    getattr(client, attr),
                )

        hotkey = None
        if args.real:
            print("EMERGENCY: press E to stop motors immediately.")
            print("Fallback: run `python -m sentinel emergency` from another terminal.")
            hotkey = EmergencyHotkey(runner.trigger_emergency)
            hotkey.start()
        try:
            report = runner.run(confirm=args.confirm or args.dry_run)
        finally:
            if hotkey is not None:
                hotkey.stop()

        write_report(report, args.log)
        return _status_exit_code(report.status)
    except Exception as exc:
        try:
            write_report(
                RunReport(
                    status="FAIL",
                    mode="real" if args.real else "dry-run",
                    backend=(
                        getattr(client, "backend_name", None)
                        if "client" in locals()
                        else None
                    ),
                    error=str(exc),
                ),
                args.log,
            )
        except Exception:
            pass
        print(f"Run failed before report generation: {exc}")
        return 1
