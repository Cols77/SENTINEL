"""Command-line entry points for dry-run, supervised real runs, and emergency."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

from .emergency import EmergencyHotkey, EmergencySender
from .models import RunReport
from .reporting import ProgressLogger, write_report, write_video_manifest
from .safe_tools import FixedPositionYawTools, ToolRefusal, YawCommand

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
    run.add_argument("--progress-log", type=Path)
    run.add_argument("--tello-ip", default="192.168.10.1")
    run.add_argument("--timeout", type=float, default=7.0)
    run.add_argument(
        "--video-path",
        type=Path,
        help="record the raw H.264 stream and retain video through the run",
    )
    run.add_argument(
        "--video-only",
        action="store_true",
        help="capture video without takeoff or movement (requires --real)",
    )
    run.add_argument("--video-duration", type=float, default=30.0)
    run.add_argument("--local-ip")
    run.add_argument("--interface-name")
    run.add_argument("--interface-index", type=int)
    run.add_argument("--min-battery", type=int, default=40)
    run.add_argument("--confirm", action="store_true")

    scan = subparsers.add_parser(
        "yaw-scan",
        help="take off, record, rotate about Z only, record, and land",
    )
    scan_mode = scan.add_mutually_exclusive_group(required=True)
    scan_mode.add_argument("--dry-run", action="store_true")
    scan_mode.add_argument("--real", action="store_true")
    scan.add_argument("--confirm", action="store_true")
    scan.add_argument("--log", type=Path, default=Path("artifacts/yaw-scan.jsonl"))
    scan.add_argument("--progress-log", type=Path)
    scan.add_argument("--video-path", type=Path, required=True)
    scan.add_argument("--dwell-seconds", type=float, default=2.0)
    scan.add_argument(
        "--turn",
        action="append",
        type=_parse_yaw_argument,
        help="bounded turn in the form cw:90 or ccw:45; repeat up to 360 degrees",
    )
    scan.add_argument("--tello-ip", default="192.168.10.1")
    scan.add_argument("--local-ip")
    scan.add_argument("--interface-name")
    scan.add_argument("--interface-index", type=int)
    scan.add_argument("--timeout", type=float, default=7.0)

    probe = subparsers.add_parser(
        "probe",
        help="test Tello adapter, command-mode connectivity, and read-only telemetry",
    )
    probe_mode = probe.add_mutually_exclusive_group(required=True)
    probe_mode.add_argument("--dry-run", action="store_true")
    probe_mode.add_argument("--real", action="store_true")
    probe.add_argument("--confirm", action="store_true")
    probe.add_argument("--log", type=Path, default=Path("artifacts/probe.jsonl"))
    probe.add_argument("--progress-log", type=Path)
    probe.add_argument("--tello-ip", default="192.168.10.1")
    probe.add_argument("--local-ip")
    probe.add_argument("--interface-name")
    probe.add_argument("--interface-index", type=int)
    probe.add_argument("--timeout", type=float, default=7.0)

    emergency = subparsers.add_parser(
        "emergency", help="send an immediate motor-stop command"
    )
    emergency.add_argument("--tello-ip", default="192.168.10.1")
    emergency.add_argument("--local-ip")
    emergency.add_argument("--interface-name")
    emergency.add_argument("--interface-index", type=int)

    pending = subparsers.add_parser(
        "pending", help="show the pending coordinator operator decision for a run, if any"
    )
    pending.add_argument("--db", type=Path, required=True)
    pending.add_argument("--run-id", required=True)

    decide = subparsers.add_parser(
        "decide", help="submit the operator's choice at a coordinator judgment point"
    )
    decide.add_argument("--db", type=Path, required=True)
    decide.add_argument("--run-id", required=True)
    decide.add_argument("--label", required=True)
    decide.add_argument("--audit", type=Path)
    return parser


def _parse_yaw_argument(value: str) -> YawCommand:
    if not isinstance(value, str) or value.count(":") != 1:
        raise argparse.ArgumentTypeError("turn must use the form cw:90 or ccw:45")
    direction, degrees_text = value.split(":")
    try:
        degrees = int(degrees_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("turn degrees must be an integer") from exc
    try:
        return YawCommand(direction, degrees)
    except ToolRefusal as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _select_adapter(
    args: Any, *, reachability_probe: Any | None = None
) -> tuple[Any, Any, Any]:
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

    selector_kwargs = {}
    if reachability_probe is not None:
        selector_kwargs["reachability_probe"] = reachability_probe
    selected = NetworkAdapterSelector(adapters, **selector_kwargs).select(
        local_ip=getattr(args, "local_ip", None),
        interface_name=getattr(args, "interface_name", None),
        interface_index=getattr(args, "interface_index", None),
    )
    after = capture_default_route()
    NetworkAdapterSelector.assert_default_route_unchanged(before, after)
    return selected, before, after


def resolve_local_ip(args: Any, *, verify_reachability: bool = True) -> str:
    if not verify_reachability and getattr(args, "local_ip", None):
        return args.local_ip
    reachability_probe = None
    if not verify_reachability:
        reachability_probe = lambda _adapter: True
    selected, _before, _after = _select_adapter(
        args, reachability_probe=reachability_probe
    )
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
        video_record_path=getattr(args, "video_path", None),
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


def _default_progress_path(report_path: Path) -> Path:
    suffix = report_path.suffix or ".jsonl"
    return report_path.with_name(f"{report_path.stem}.progress{suffix}")


def _default_yaw_scan_commands() -> tuple[YawCommand, ...]:
    return tuple(YawCommand("cw", 90) for _ in range(4))


def _run_yaw_scan(args: Any) -> int:
    if args.real and not args.confirm:
        print("Real yaw scans require --confirm after the physical preflight.")
        return 1

    progress: ProgressLogger | None = None
    client: Any | None = None
    if args.real:
        progress = ProgressLogger(
            args.progress_log or _default_progress_path(args.log)
        )
        progress.emit("run_started", "starting bounded yaw-only scan")
        progress.emit("network", "selecting a safe Tello adapter")

    try:
        if args.dry_run:
            if DryRunClient is None:
                from .dry_run import DryRunClient as client_type
            else:
                client_type = DryRunClient
            client = client_type()
        else:
            client = build_real_client(args)
            assert progress is not None
            progress.emit(
                "network",
                f"selected adapter {client._sentinel_adapter.local_ip}",
            )

        tools = FixedPositionYawTools(client, record_path=args.video_path)
        commands = tuple(args.turn or _default_yaw_scan_commands())
        if args.real:
            print("EMERGENCY: press E to stop motors immediately.")
            print("Fallback: run `python -m sentinel emergency` from another terminal.")
            assert progress is not None
            progress.emit("preflight", "yaw-only safety controller ready")
            hotkey = EmergencyHotkey(tools.emergency_stop)
            hotkey.start()
        else:
            hotkey = None
        try:
            frames = tools.scan(commands, args.dwell_seconds)
        finally:
            if hotkey is not None:
                hotkey.stop()

        report = RunReport(
            status="PASS",
            mode="real-yaw-scan" if args.real else "dry-run-yaw-scan",
            commands=tuple(client.command_history),
            video_frame=frames[-1] if frames else None,
            backend=getattr(client, "backend_name", None),
        )
        write_report(report, args.log)
        if args.video_path.is_file() and args.video_path.stat().st_size > 0:
            write_video_manifest(args.video_path, report)
        return 0
    except Exception as exc:
        if progress is not None:
            progress.emit("run_failed", str(exc))
        report = RunReport(
            status="FAIL",
            mode="real-yaw-scan" if args.real else "dry-run-yaw-scan",
            commands=tuple(client.command_history) if client is not None else (),
            backend=getattr(client, "backend_name", None),
            error=str(exc),
        )
        try:
            write_report(report, args.log)
        except Exception:
            pass
        print(f"Yaw scan failed: {exc}", flush=True)
        return 1
    finally:
        if progress is not None:
            progress.close()


def _run_connection_probe(args: Any) -> int:
    """Run a non-flight Tello connectivity and telemetry diagnostic."""

    if args.real and not args.confirm:
        print("Real connection probes require --confirm.")
        return 1

    progress: ProgressLogger | None = None
    client: Any | None = None
    if args.real:
        progress = ProgressLogger(
            args.progress_log or _default_progress_path(args.log)
        )
        progress.emit("run_started", "starting preflight-only connection probe")
        progress.emit("network", "selecting and probing a safe Tello adapter")

    try:
        if args.dry_run:
            if DryRunClient is None:
                from .dry_run import DryRunClient as client_type
            else:
                client_type = DryRunClient
            client = client_type()
        else:
            client = build_real_client(args)
            assert progress is not None
            progress.emit(
                "network",
                f"selected adapter {client._sentinel_adapter.local_ip}",
            )

        client.connect()
        battery = client.battery()
        height = client.height_cm()
        speed = client.speed_cm_per_second()
        if type(battery) is not int or not 0 <= battery <= 100:
            raise ToolRefusal(f"probe received invalid battery telemetry: {battery!r}")
        if type(height) is not int or height < 0:
            raise ToolRefusal(f"probe received invalid height telemetry: {height!r}")
        if type(speed) is not int or speed < 0:
            raise ToolRefusal(f"probe received invalid speed telemetry: {speed!r}")
        print(
            f"Connection OK: battery={battery}% height={height}cm speed={speed}cm/s",
            flush=True,
        )
        if progress is not None:
            progress.emit(
                "probe_passed",
                "command mode and read-only telemetry validated",
                battery=battery,
                height_cm=height,
                speed_cm_per_second=speed,
            )
        report = RunReport(
            status="PASS",
            mode="real-probe" if args.real else "dry-run-probe",
            commands=tuple(client.command_history),
            backend=getattr(client, "backend_name", None),
        )
        write_report(report, args.log)
        return 0
    except Exception as exc:
        if progress is not None:
            progress.emit("run_failed", str(exc))
        report = RunReport(
            status="FAIL",
            mode="real-probe" if args.real else "dry-run-probe",
            commands=tuple(client.command_history) if client is not None else (),
            backend=getattr(client, "backend_name", None),
            error=str(exc),
        )
        try:
            write_report(report, args.log)
        except Exception:
            pass
        print(f"Connection probe failed: {exc}", flush=True)
        return 1
    finally:
        if client is not None:
            client.close()
        if progress is not None:
            progress.close()


def _run_pending(args: Any) -> int:
    """`sentinel pending --db PATH --run-id UUID`: print the pending
    coordinator judgment-point decision, if any. Operator-facing only --
    ``pending_decision`` never surfaces advisor output."""
    from .coordinator.legal_tools import load_catalogue_plans
    from .coordinator.operator_choice import pending_decision
    from .coordinator.repository import CoordinatorRepository

    repository = CoordinatorRepository.open(args.db)
    try:
        pending = pending_decision(repository, args.run_id, load_catalogue_plans())
    except KeyError:
        print(f"no such run: {args.run_id}")
        return 2
    finally:
        repository.close()

    if pending is None:
        print("no pending operator decision")
        return 0
    print(f"decision_point: {pending['decision_point']}")
    for option in pending["options"]:
        print(f"  {option['label']}: {option['description']}")
    return 0


def _run_decide(args: Any) -> int:
    """`sentinel decide --db PATH --run-id UUID --label LABEL [--audit PATH]`:
    submit the operator's choice among the current legal candidates at a
    judgment point. Exits 0 on success, 2 on an invalid label (or run)."""
    from .coordinator.command_gateway import CommandGateway
    from .coordinator.errors import CoordinatorError
    from .coordinator.legal_tools import load_catalogue_plans
    from .coordinator.operator_choice import submit_choice
    from .coordinator.repository import CoordinatorRepository
    from .coordinator.shadow import JsonlAuditSink, MemoryAuditSink

    repository = CoordinatorRepository.open(args.db)
    try:
        gateway = CommandGateway(repository)
        sink = JsonlAuditSink(args.audit) if args.audit else MemoryAuditSink()
        try:
            result = submit_choice(repository, gateway, args.run_id, args.label,
                                    catalogue=load_catalogue_plans(), sink=sink)
        except CoordinatorError as exc:
            print(f"decide failed: {exc}")
            return 2
        except KeyError:
            print(f"no such run: {args.run_id}")
            return 2
    finally:
        repository.close()
    print(f"decide OK: {result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "emergency":
        if args.local_ip:
            print(
                f"[emergency] using explicit local IP {args.local_ip}; "
                "skipping default-route and Tello ACK checks",
                flush=True,
            )
        else:
            print("[emergency] resolving the Tello adapter...", flush=True)
        try:
            local_ip = resolve_local_ip(args, verify_reachability=False)
            print(
                f"[emergency] sending motor-stop from {local_ip} "
                f"to {args.tello_ip}:8889...",
                flush=True,
            )
            bytes_sent = EmergencySender(
                local_ip=local_ip, tello_ip=args.tello_ip
            ).send()
            print(
                f"[emergency] sent {bytes_sent} bytes. "
                "Tello provides no emergency-command acknowledgement.",
                flush=True,
            )
            return 0
        except Exception as exc:
            print(f"[emergency] FAILED: {exc}", flush=True)
            return 1

    if args.command == "yaw-scan":
        return _run_yaw_scan(args)

    if args.command == "probe":
        return _run_connection_probe(args)

    if args.command == "pending":
        return _run_pending(args)

    if args.command == "decide":
        return _run_decide(args)

    if args.real and not args.confirm:
        print("Real mode requires --confirm after the physical preflight.")
        return 1
    if args.video_only and not args.real:
        print("--video-only requires --real.")
        return 1
    if args.video_only and args.video_path is None:
        print("--video-only requires --video-path.")
        return 1
    if args.video_duration < 0:
        print("--video-duration must not be negative.")
        return 1

    progress: ProgressLogger | None = None
    if args.real:
        progress = ProgressLogger(
            args.progress_log or _default_progress_path(args.log)
        )
        progress.emit("run_started", "starting real run")
        progress.emit("network", "selecting a safe Tello adapter")

    try:
        if args.dry_run:
            if DryRunClient is None:
                from .dry_run import DryRunClient as client_type
            else:
                client_type = DryRunClient
            client = client_type()
        else:
            client = build_real_client(args)
            assert progress is not None
            progress.emit(
                "network",
                f"selected adapter {client._sentinel_adapter.local_ip}",
            )

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
                "progress": progress.emit,
                "retain_video_stream": args.video_path is not None,
                "video_only": args.video_only,
                "video_only_duration_s": args.video_duration,
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
        if (
            args.video_path is not None
            and args.video_path.is_file()
            and args.video_path.stat().st_size > 0
        ):
            write_video_manifest(args.video_path, report)
        return _status_exit_code(report.status)
    except Exception as exc:
        if progress is not None:
            progress.emit("run_failed", str(exc))
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
        print(f"Run failed: {exc}", flush=True)
        return 1
    finally:
        if progress is not None:
            progress.close()
