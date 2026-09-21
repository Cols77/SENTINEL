"""Bench test B1: does a Tello honour an abort sent while a move is executing?

MOTORS ON. One run per invocation. Protocol and gates: docs/wp5-rehearsal-manifest.md
("Bench test B1"). Run it from your own terminal, not through an agent, so the keys work:

    E = emergency motor cut        L = land now

Variants:  N  no abort (baseline: how far and how long a plain move really is)
           A  land            sent --delay seconds after the move
           B  stop, then land sent 0.3 s later

Talks raw SDK over UDP on purpose: the production client blocks until a move answers, and the
question here is exactly what the drone does with a second command before that answer. Height and
velocity come from the state stream (port 8890), so no query command disturbs the test.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

TELLO = ("192.168.10.1", 8889)
MIN_BATTERY = 50
HOVER_S = 3.0
FAILSAFE_LAND_S = 12.0      # after the move is sent, land regardless
GIVE_UP_S = 30.0
CLEARANCE_MARGIN_CM = 100


class Bench:
    def __init__(self, local_ip: str) -> None:
        self.t0 = time.monotonic()
        self.events: list[dict] = []
        self.states: list[dict] = []
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.cmd.bind((local_ip, 8889))   # the Tello answers the port that sent its first command
        self.cmd.settimeout(0.2)
        self.state = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.state.bind((local_ip, 8890))
        self.state.settimeout(0.2)
        self.video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.video.bind((local_ip, 11111))
        self.video.settimeout(0.2)
        self.video_file = None
        self.video_index: list[list] = []   # [t, byte_offset] per packet, for frame timing offline
        for target in (self._receive, self._receive_state, self._keys, self._record_video):
            threading.Thread(target=target, daemon=True).start()

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def log(self, kind: str, text: str) -> None:
        with self.lock:
            self.events.append({"t": self.now(), "kind": kind, "text": text})
        print(f"[{self.now():7.3f}] {kind:<5} {text}", flush=True)

    def send(self, command: str) -> None:
        self.log("send", command)
        self.cmd.sendto(command.encode("ascii"), TELLO)

    def ask(self, command: str, timeout: float = 5.0) -> str | None:
        """Send and wait for the next response. Only used while nothing else is in flight."""
        seen = len([e for e in self.events if e["kind"] == "recv"])
        self.send(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            replies = [e for e in self.events if e["kind"] == "recv"]
            if len(replies) > seen:
                return replies[-1]["text"]
            time.sleep(0.02)
        return None

    def height_cm(self) -> int | None:
        return self.states[-1].get("h") if self.states else None

    def _receive(self) -> None:
        while not self.done.is_set():
            try:
                data, _ = self.cmd.recvfrom(1024)
            except (socket.timeout, OSError):
                continue
            self.log("recv", data.decode("ascii", errors="replace").strip())

    def _receive_state(self) -> None:
        while not self.done.is_set():
            try:
                data, _ = self.state.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            row = {"t": self.now()}
            for field in data.decode("ascii", errors="replace").strip().split(";"):
                key, _, value = field.partition(":")
                if key in ("h", "tof", "vgx", "vgy", "vgz", "bat", "yaw"):
                    try:
                        row[key] = int(float(value))
                    except ValueError:
                        pass
            self.states.append(row)

    def _record_video(self) -> None:
        offset = 0
        while not self.done.is_set():
            try:
                data, _ = self.video.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            if self.video_file is not None:
                self.video_index.append([self.now(), offset])
                self.video_file.write(data)
                offset += len(data)

    def _keys(self) -> None:
        if os.name != "nt":
            return
        import msvcrt
        while not self.done.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                if key == "e":
                    self.send("emergency")
                elif key == "l":
                    self.send("land")
            time.sleep(0.02)


def tello_local_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(TELLO)
        return probe.getsockname()[0]
    finally:
        probe.close()


def summarise(bench: Bench, variant: str, delay: float) -> dict:
    sends = {e["text"]: e["t"] for e in bench.events if e["kind"] == "send"}
    move_t = next((t for text, t in sends.items() if text.split()[0] in ("forward", "back", "left", "right")), None)
    abort_t = sends.get("stop" if variant == "B" else "land") if variant != "N" else None
    replies = [e for e in bench.events if e["kind"] == "recv" and move_t is not None and e["t"] > move_t]
    summary = {"variant": variant, "delay_s": delay, "move_sent_t": move_t, "abort_sent_t": abort_t,
               "replies_after_move": [[e["t"], e["text"]] for e in replies]}
    if abort_t is not None:
        after = [s for s in bench.states if s["t"] >= abort_t]
        still = next((s["t"] for s in after if abs(s.get("vgx", 0)) <= 1 and abs(s.get("vgy", 0)) <= 1), None)
        # the state stream's h field reads 0-10 while hovering at 90 cm; the time-of-flight
        # sensor (tof, cm) is the honest height. 6553 means out of range (on the ground, tilted).
        start_tof = next((s["tof"] for s in after if s.get("tof", 6553) < 500), None)
        descending = next((s["t"] for s in after if start_tof is not None
                           and s.get("tof", 6553) < start_tof - 15), None)
        landed = next((s["t"] for s in after if s.get("tof", 6553) <= 15 or s.get("tof") == 6553), None)
        summary.update({
            "abort_to_horizontal_stop_s": None if still is None else round(still - abort_t, 2),
            "abort_to_descent_s": None if descending is None else round(descending - abort_t, 2),
            "abort_to_landed_s": None if landed is None else round(landed - abort_t, 2)})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", choices=("N", "A", "B"), required=True)
    parser.add_argument("--delay", type=float, default=2.0, help="seconds after the move is sent")
    parser.add_argument("--distance", type=int, default=200)
    parser.add_argument("--direction", choices=("forward", "back", "left", "right"), default="forward",
                        help="back = fly away from the tags while facing them, so the video localises every frame")
    parser.add_argument("--speed", type=int, default=50, help="cm/s (measured: a 200 cm move takes ~10 s regardless)")
    parser.add_argument("--local-ip", default=None)
    parser.add_argument("--clear-ahead-cm", type=int, required=True,
                        help="tape-measured free distance in the direction of the move")
    parser.add_argument("--out", default="artifacts/b1")
    parser.add_argument("--confirm", action="store_true",
                        help="I hold G4 consent, the run is clear, and I am at the keyboard")
    args = parser.parse_args()
    # 2026-09-21: forward 200 from 195 cm in front of a door flew into it. Never again.
    if args.clear_ahead_cm < args.distance + CLEARANCE_MARGIN_CM:
        print(f"Refusing: {args.direction} {args.distance} needs at least {args.distance + CLEARANCE_MARGIN_CM} cm "
              f"clear in the direction of the move; you measured {args.clear_ahead_cm}.")
        return 2
    if not args.confirm:
        print("Refusing: motors-on test. Pass --confirm once the area is clear.")
        return 2

    bench = Bench(args.local_ip or tello_local_ip())
    airborne = False
    try:
        if bench.ask("command") != "ok":
            print("No answer to 'command'. Is this machine on the Tello Wi-Fi?")
            return 1
        battery = bench.ask("battery?")
        if battery is None or not battery.isdigit() or int(battery) < MIN_BATTERY:
            print(f"Battery {battery}% is below {MIN_BATTERY}%. Not taking off.")
            return 1
        bench.ask(f"speed {args.speed}")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        video_path = out / f"b1-{args.variant}-{args.delay:g}s-{stamp}.h264"
        bench.video_file = video_path.open("wb")
        if bench.ask("streamon") != "ok":
            bench.log("note", "streamon not acknowledged; no video this run")
        print("\n  E = emergency motor cut     L = land now\n", flush=True)
        if bench.ask("takeoff", timeout=20.0) != "ok":
            print("Takeoff was not acknowledged. Sending land.")
            bench.send("land")
            return 1
        airborne = True
        time.sleep(HOVER_S)

        bench.send(f"{args.direction} {args.distance}")
        move_sent = time.monotonic()
        if args.variant != "N":
            time.sleep(args.delay)
            if args.variant == "A":
                bench.send("land")
            else:
                bench.send("stop")
                time.sleep(0.3)
                bench.send("land")

        failsafe_sent = False
        while time.monotonic() - move_sent < GIVE_UP_S:
            height, elapsed = bench.height_cm(), time.monotonic() - move_sent
            tof = bench.states[-1].get("tof", 100) if bench.states else 100
            if elapsed > 3.0 and (tof <= 15 or tof == 6553):
                airborne = False
                break
            if not failsafe_sent and elapsed > FAILSAFE_LAND_S:
                bench.log("note", "failsafe: landing")
                bench.send("land")
                failsafe_sent = True
            time.sleep(0.05)
        if airborne:
            print("\n*** STILL AIRBORNE AFTER 30 s - press E for the motor cut ***\n", flush=True)
        time.sleep(2.0)
        bench.log("note", f"battery after: {bench.ask('battery?')}")
    except KeyboardInterrupt:
        bench.send("land")
    finally:
        bench.done.set()
        time.sleep(0.3)
        if bench.video_file is not None:
            bench.video_file.close()
        summary = summarise(bench, args.variant, args.delay)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"b1-{args.variant}-{args.delay:g}s-{time.strftime('%H%M%S')}.json"
        path.write_text(json.dumps({"summary": summary, "events": bench.events,
                                    "states": bench.states,
                                    "video_packets": bench.video_index}, indent=1), encoding="utf-8")
        print("\n" + json.dumps(summary, indent=2))
        print(f"\nsaved {path}\nNow tape the distance from the takeoff mark to where it landed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
