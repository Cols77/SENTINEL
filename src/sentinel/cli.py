"""Command-line entry points for dry-run, supervised real runs, and emergency."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any

from .cube.site import load_site
from .emergency import EmergencyHotkey, EmergencySender
from .models import RunReport
from .reporting import ProgressLogger, write_report, write_video_manifest
from .safe_tools import FixedPositionYawTools, ToolRefusal, YawCommand
from .pose.mapcheck import compare_frames
from .pose.persistence import ensure_schema
from .pose.preview import Preview
from .pose.replay import FrameRecorder, ReplayClient
from .pose.scoring import Thresholds, load_truth, score_runs
from .pose.stream import run_pose_stream, throttled_altitude_source
from .pose.tagmap import load_tag_map

try:  # Task 1/3 may be landed independently in the shared worktree.
    from .dry_run import DryRunClient
except ImportError:  # pragma: no cover - temporary clean-baseline state
    DryRunClient = None  # type: ignore[assignment,misc]

try:
    from .runner import FlightTestRunner
except ImportError:  # pragma: no cover - temporary clean-baseline state
    FlightTestRunner = None  # type: ignore[assignment,misc]


def _add_flight_arguments(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--real", action="store_true")
    parser.add_argument("--log", type=Path, default=Path("artifacts/run.jsonl"))
    parser.add_argument("--progress-log", type=Path)
    parser.add_argument("--tello-ip", default="192.168.10.1")
    parser.add_argument("--timeout", type=float, default=7.0)
    parser.add_argument("--video-path", type=Path,
                        help="record the raw H.264 stream and retain video through the run")
    parser.add_argument("--video-only", action="store_true",
                        help="capture video without takeoff or movement (requires --real)")
    parser.add_argument("--video-duration", type=float, default=30.0)
    parser.add_argument("--local-ip")
    parser.add_argument("--interface-name")
    parser.add_argument("--interface-index", type=int)
    parser.add_argument("--min-battery", type=int, default=40)
    parser.add_argument("--confirm", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run the approved command sequence")
    _add_flight_arguments(run)

    cube_run = subparsers.add_parser(
        "cube-run", help="fly a waypoint mission inside the operating cube")
    _add_flight_arguments(cube_run)
    cube_run.add_argument("--site", type=Path, required=True)
    cube_run.add_argument("--mission", type=Path, required=True)
    cube_run.add_argument("--pose", choices=("off", "passive", "reanchor"), default="off")
    cube_run.add_argument("--sr063-amended", action="store_true",
                          help="assert that SR-063 has been amended to permit re-anchoring")
    cube_run.add_argument("--tag-map", type=Path)
    cube_run.add_argument("--db", type=Path, default=Path("artifacts/pose.db"))
    cube_run.add_argument("--mission-log", type=Path, default=Path("artifacts/cube-mission.jsonl"))
    cube_run.add_argument("--capture-dir", type=Path)

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

    pose_stream = subparsers.add_parser(
        "pose-stream",
        help="stream video only and persist fail-closed pose reports",
    )
    pose_source = pose_stream.add_mutually_exclusive_group(required=True)
    pose_source.add_argument("--real", action="store_true")
    pose_source.add_argument(
        "--replay",
        type=Path,
        help="offline: replay an mp4/avi file or a directory of PNG frames",
    )
    pose_stream.add_argument(
        "--record-dir",
        type=Path,
        help="save raw frames from a live run here for later --replay",
    )
    pose_stream.add_argument(
        "--preview",
        action="store_true",
        help="open a live window of what the drone sees (q/Esc stops the stream)",
    )
    pose_stream.add_argument(
        "--preview-board",
        default="7x6",
        help="checkerboard inner corners COLSxROWS to detect in the preview",
    )
    pose_stream.add_argument(
        "--allow-nominal",
        action="store_true",
        help="permit a tag map whose camera calibration is nominal (uncalibrated)",
    )
    pose_stream.add_argument("--confirm", action="store_true")
    pose_stream.add_argument("--tag-map", type=Path, required=True)
    pose_stream.add_argument("--db", type=Path, default=Path("artifacts/pose.db"))
    pose_stream.add_argument("--max-frames", type=int)
    pose_stream.add_argument("--duration", type=float)
    pose_stream.add_argument("--run-id", help="default: a fresh UUID4")
    pose_stream.add_argument("--tello-ip", default="192.168.10.1")
    pose_stream.add_argument("--local-ip")
    pose_stream.add_argument("--interface-name")
    pose_stream.add_argument("--interface-index", type=int)
    pose_stream.add_argument("--timeout", type=float, default=7.0)

    pose_score = subparsers.add_parser(
        "pose-score",
        help="score stored pose runs against a ground-truth file (offline)",
    )
    pose_score.add_argument("--db", type=Path, default=Path("artifacts/pose.db"))
    pose_score.add_argument("--truth", type=Path, required=True)
    pose_score.add_argument(
        "--tag-map", type=Path, required=True,
        help="tag map used for the scored runs; nominal calibration fails the score",
    )
    pose_score.add_argument(
        "--allow-nominal", action="store_true",
        help="score even though the tag map calibration is nominal (uncalibrated)",
    )
    pose_score.add_argument("--p95-cm", type=float, default=Thresholds.p95_cm)
    pose_score.add_argument("--heading-deg", type=float, default=Thresholds.heading_deg)
    pose_score.add_argument("--min-samples", type=int, default=Thresholds.min_samples)

    map_check = subparsers.add_parser(
        "pose-map-check", help="offline: single-tag vs joint PnP spread and per-stand residual")
    map_check.add_argument("--frames", type=Path, required=True,
                           help="directory of recorded PNG frames")
    map_check.add_argument("--tag-map", type=Path, required=True)

    site_map = subparsers.add_parser(
        "cube-site-map", help="generate the tag-map-v1 file from a cube-site-v1 file")
    site_map.add_argument("--site", type=Path, required=True)
    site_map.add_argument("--calibration-from", type=Path, required=True,
                          help="existing tag map whose camera_calibration is reused")
    site_map.add_argument("--out", type=Path, required=True)

    layout = subparsers.add_parser(
        "cube-layout-check", help="predict pose accuracy over the cube for a site file")
    layout.add_argument("--site", type=Path, required=True)
    layout.add_argument("--calibration-from", type=Path, required=True)
    layout.add_argument("--alt", type=float, action="append",
                        help="altitude in metres; repeatable (default 1.0 1.4 1.8)")
    layout.add_argument("--step", type=float, default=0.5)
    layout.add_argument("--trials", type=int, default=60)

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
    pending.add_argument("--audit", type=Path)

    review_plan = subparsers.add_parser(
        "review-plan", help="confirm or reject the specific plan awaiting approval"
    )
    review_plan.add_argument("--db", type=Path, required=True)
    review_plan.add_argument("--run-id", required=True)
    review_plan.add_argument("--plan-id", required=True)
    review_plan.add_argument("--expected-state-version", type=int, required=True)
    review_plan.add_argument(
        "--decision", choices=("CONFIRM", "REJECT"), required=True
    )
    review_plan.add_argument("--audit", type=Path, required=True)

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
    from .coordinator.proposal_review import pending_proposal_review, read_audit_jsonl
    from .coordinator.repository import CoordinatorRepository

    audit_records = []
    if args.audit and args.audit.exists():
        audit_records, warnings = read_audit_jsonl(args.audit)
        for warning in warnings:
            print(f"pending warning: {warning}", file=sys.stderr)
    repository = CoordinatorRepository.open(args.db)
    try:
        catalogue = load_catalogue_plans()
        proposal = pending_proposal_review(
            repository, args.run_id, catalogue, audit_records
        )
        pending = None if proposal is not None else pending_decision(
            repository, args.run_id, catalogue
        )
    except KeyError:
        print(f"no such run: {args.run_id}")
        return 2
    finally:
        repository.close()

    if proposal is not None:
        identity = proposal["review_identity"]
        proposed = proposal["proposed_plan"]
        print(f"proposed_plan: {proposed['label']}: {proposed['description']}")
        print(f"  plan_id: {identity['plan_id']}")
        print(f"  expected_state_version: {identity['expected_state_version']}")
        recommendation = proposed["recommendation"]
        if recommendation is not None:
            print(f"  reason: {json.dumps(recommendation['reason'])}")
            print(f"  facts: {json.dumps(recommendation['facts'], sort_keys=True)}")
        print("other_plans:")
        for plan in proposal["other_plans"]:
            print(f"  {plan['label']}: {plan['description']}")
        return 0
    if pending is None:
        print("no pending operator decision")
        return 0
    print(f"decision_point: {pending['decision_point']}")
    for option in pending["options"]:
        print(f"  {option['label']}: {option['description']}")
    return 0


def _run_review_plan(args: Any) -> int:
    """Submit an operator decision for the exact persisted pending plan."""
    from .coordinator.command_gateway import CommandGateway
    from .coordinator.errors import CoordinatorError
    from .coordinator.proposal_review import submit_plan_review
    from .coordinator.repository import CoordinatorRepository
    from .coordinator.shadow import JsonlAuditSink

    repository = CoordinatorRepository.open(args.db)
    try:
        try:
            result = submit_plan_review(
                repository,
                CommandGateway(repository),
                args.run_id,
                args.decision,
                plan_id=args.plan_id,
                expected_state_version=args.expected_state_version,
                sink=JsonlAuditSink(args.audit),
            )
        except CoordinatorError as exc:
            print(f"review-plan failed: {exc}")
            return 2
    finally:
        repository.close()
    print(f"review-plan OK: {result}")
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

def _run_pose_stream(args: Any) -> int:
    """Run bounded video-only pose estimation without flight commands."""
    replay = args.replay is not None
    if replay and args.record_dir is not None:
        print("--record-dir applies to live (--real) runs only.")
        return 1
    if not replay and not args.confirm:
        print("Real pose streams require --confirm after the physical preflight.")
        return 1
    if args.max_frames is None and args.duration is None:
        print("pose-stream requires --max-frames and/or --duration.")
        return 1
    if args.max_frames is not None and args.max_frames < 1:
        print("--max-frames must be at least 1.")
        return 1
    if args.duration is not None and args.duration <= 0:
        print("--duration must be positive.")
        return 1

    client: Any | None = None
    conn: sqlite3.Connection | None = None
    preview = None
    try:
        tag_map = load_tag_map(str(args.tag_map))
        if tag_map.calibration_source == "nominal" and not args.allow_nominal:
            print(
                "Tag map camera calibration is nominal (uncalibrated); positions "
                "are not trustworthy. Run scripts/calibrate_camera.py or pass "
                "--allow-nominal."
            )
            return 1
        args.db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(args.db)
        ensure_schema(conn)
        recorder = None
        altitude_source = None
        if replay:
            client = ReplayClient(args.replay)
            source_kind = "REPLAYED"
        else:
            client = build_real_client(args)
            client.connect()
            source_kind = "LIVE"
            if args.record_dir is not None:
                recorder = FrameRecorder(args.record_dir)
            # Read-only height query, throttled; failure just leaves z=n/a.
            altitude_source = throttled_altitude_source(
                lambda: client.height_cm(), scale=0.01
            )
        run_id = args.run_id or str(uuid.uuid4())

        def print_report(report) -> None:  # live, as each pose is persisted
            z = "n/a" if report.altitude_m is None else report.altitude_m
            print(
                f"pose x={report.point[0]} y={report.point[1]} "
                f"z={z} heading={report.heading_deg}",
                flush=True,
            )

        preview = None
        if args.preview:
            try:
                cols, rows = (int(v) for v in args.preview_board.lower().split("x"))
            except ValueError:
                print("--preview-board must look like 7x6.")
                return 1
            preview = Preview(board_pattern=(cols, rows))
        reports = run_pose_stream(
            client,
            tag_map,
            conn,
            run_id=run_id,
            max_frames=args.max_frames,
            duration_s=args.duration,
            source_kind=source_kind,
            recorder=recorder,
            altitude_source=altitude_source,
            on_frame=preview,
            on_report=print_report,
        )
        print(f"pose-stream complete: {len(reports)} report(s), run_id={run_id}")
        return 0
    except KeyboardInterrupt:
        print("pose-stream interrupted", flush=True)
        return 130
    except Exception as exc:
        print(f"Pose stream failed: {exc}", flush=True)
        return 1
    finally:
        if preview is not None:
            preview.close()
        if client is not None:
            client.close()
        if conn is not None:
            conn.close()


def _run_pose_score(args: Any) -> int:
    """Score stored pose runs against ground truth (offline, read-only)."""
    try:
        truth = load_truth(str(args.truth))
        tag_map = load_tag_map(str(args.tag_map))
        result = score_runs(
            str(args.db),
            truth,
            Thresholds(p95_cm=args.p95_cm, heading_deg=args.heading_deg,
                       min_samples=args.min_samples),
            calibration_source=tag_map.calibration_source,
            allow_nominal=args.allow_nominal,
        )
    except Exception as exc:
        print(f"pose-score failed: {exc}", flush=True)
        return 1
    for run_id, run in result["runs"].items():
        if run["n"] == 0:
            print(f"{run_id}: FAIL (no stored poses)")
            continue
        line = (
            f"{run_id}: {'PASS' if run['passed'] else 'FAIL'} n={run['n']} "
            f"median={run['median_error_cm']:.1f}cm p95={run['p95_error_cm']:.1f}cm "
            f"heading_p95={run['heading_p95_deg']:.1f}deg"
        )
        metrics_ok = (run["p95_error_cm"] <= args.p95_cm
                      and run["heading_p95_deg"] <= args.heading_deg)
        if metrics_ok and not run["enough_samples"]:
            line += f" (n={run['n']} below min_samples={args.min_samples})"
        print(line)
        print(
            f"    bias dx={run['bias_dx_cm']:+.1f}cm dy={run['bias_dy_cm']:+.1f}cm "
            f"dheading={run['bias_heading_deg']:+.1f}deg "
            f"spread p95={run['spread_p95_cm']:.1f}cm/{run['heading_spread_p95_deg']:.1f}deg"
        )
    if not result["calibration_ok"]:
        print(
            "pose-score: FAIL - tag map camera calibration is nominal (uncalibrated); "
            "calibrate the camera or pass --allow-nominal."
        )
    threshold_desc = f"p95<={args.p95_cm}cm, heading<={args.heading_deg}deg"
    if args.min_samples > 0:
        threshold_desc += f", n>={args.min_samples}"
    print(f"pose-score: {'PASS' if result['passed'] else 'FAIL'} ({threshold_desc})")
    return 0 if result["passed"] else 1


def _run_pose_map_check(args: Any) -> int:
    import cv2

    paths = sorted(args.frames.glob("*.png"))
    if not paths:
        print(f"pose-map-check: no .png frames in {args.frames}", flush=True)
        return 1
    result = compare_frames((cv2.imread(str(p)) for p in paths), load_tag_map(str(args.tag_map)))
    print(f"pose-map-check: {result['multi_tag_frames']} of {result['frames']} frames saw 2+ tags")
    for name in ("single", "joint"):
        row = result[name]
        if row["n"]:
            print(f"  {name:<6} n={row['n']} spread p95={row['spread_p95_cm']:.2f}cm "
                  f"median=({row['median_x_units']:.2f}, {row['median_y_units']:.2f}) units")
    for zone, row in result["zones"].items():
        flag = "  <- re-measure this stand" if row["median_joint_reprojection_px"] > 1.0 else ""
        print(f"  {zone}: joint reprojection median "
              f"{row['median_joint_reprojection_px']:.2f}px over {row['n']} frames{flag}")
    return 0 if result["multi_tag_frames"] else 1


def _run_cube_site_map(args: Any) -> int:
    try:
        site = load_site(args.site)
        calibration = json.loads(
            args.calibration_from.read_text(encoding="utf-8"))["camera_calibration"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"cube-site-map failed: {exc}", flush=True)
        return 1
    doc = site.tag_map_doc(calibration)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    env = site.envelope()
    print(f"cube-site-map: wrote {len(doc['tags'])} tags to {args.out}; commanded bounds "
          f"x {env.min_x_cm}-{env.max_x_cm} y {env.min_y_cm}-{env.max_y_cm} "
          f"z {env.min_z_cm}-{env.max_z_cm} cm")
    return 0


def _run_cube_layout_check(args: Any) -> int:
    from .pose.layout import predict_grid
    from .pose.tagmap import TagMap

    try:
        site = load_site(args.site)
        calibration = json.loads(
            args.calibration_from.read_text(encoding="utf-8"))["camera_calibration"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"cube-layout-check failed: {exc}", flush=True)
        return 1
    env = site.envelope()

    def span(lo_cm: int, hi_cm: int) -> list[float]:
        count = max(1, int((hi_cm - lo_cm) / (args.step * 100.0)))
        return [round((lo_cm + i * (hi_cm - lo_cm) / count) / 100.0, 2) for i in range(count + 1)]

    xs, ys = span(env.min_x_cm, env.max_x_cm), span(env.min_y_cm, env.max_y_cm)
    alts = args.alt or [1.0, 1.4, 1.8]
    cells = predict_grid(TagMap(site.tag_map_doc(calibration)), xs, ys, alts, trials=args.trials)
    print("cube-layout-check: predicted real p95 horizontal error in cm (simulation x 2.5); "
          "'--' = no tag fully in frame")
    blind = 0
    for alt in alts:
        print(f" altitude {alt:.1f} m, standoff from the tag line shown per row")
        print("          " + "".join(f"x={x:<5.2f}" for x in xs))
        for y in reversed(ys):
            row = [c for c in cells if c["alt_m"] == alt and c["y_m"] == y]
            blind += sum(1 for c in row if c["p95_cm"] is None)
            text = "".join(f"{'--' if c['p95_cm'] is None else c['p95_cm']:<7}" for c in row)
            print(f"   {site.line_y_m - y:4.2f} m  {text}")
    print(f" {blind} blind cell(s): stops there need \"localise\": false")
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

    if args.command == "review-plan":
        return _run_review_plan(args)

    if args.command == "pose-stream":
        return _run_pose_stream(args)

    if args.command == "pose-score":
        return _run_pose_score(args)

    if args.command == "pose-map-check":
        return _run_pose_map_check(args)
    if args.command == "cube-site-map":
        return _run_cube_site_map(args)
    if args.command == "cube-layout-check":
        return _run_cube_layout_check(args)

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

    cube_conn: sqlite3.Connection | None = None
    if args.command == "cube-run":
        if args.video_only:
            print("cube-run does not support --video-only.")
            return 1
        if args.pose == "reanchor" and not args.sr063_amended:
            print("--pose reanchor lets pose correct motion, which SR-063 forbids as written. "
                  "Amend SR-063 first, then pass --sr063-amended.")
            return 1
        if args.pose != "off" and args.tag_map is None:
            print(f"--pose {args.pose} requires --tag-map.")
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

        if args.command == "cube-run":
            from .cube.mission import CubeMissionRunner, load_mission

            runner_type = CubeMissionRunner
        elif FlightTestRunner is None:
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
        if args.command == "cube-run":
            for key in ("retain_video_stream", "video_only", "video_only_duration_s"):
                runner_kwargs.pop(key, None)
            tag_map = None
            if args.pose != "off":
                tag_map = load_tag_map(str(args.tag_map))
                if args.real and tag_map.calibration_source == "nominal":
                    raise RuntimeError("cube-run --real refuses a nominal (uncalibrated) tag map")
                args.db.parent.mkdir(parents=True, exist_ok=True)
                cube_conn = sqlite3.connect(args.db)
                ensure_schema(cube_conn)
            runner_kwargs.update({
                "mission": load_mission(args.mission), "site": load_site(args.site),
                "pose_mode": args.pose, "tag_map": tag_map, "conn": cube_conn,
                "run_id": str(uuid.uuid4()), "mission_log": args.mission_log,
                "capture_dir": args.capture_dir,
            })
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
        if cube_conn is not None:
            cube_conn.close()
