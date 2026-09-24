"""Takeoff check for the room-agent live stack: connect, take off, hover, land.

For the operator to run BY HAND before a live room-agent flight, with the
Tello on the pad and the cube clear:

    python scripts/room_takeoff_check.py --execute [--local-ip 192.168.10.2]

It uses the same connection the live room session flies with
(``sentinel.room.live_stack.TelloLink`` over ``sentinel.network`` adapter
selection and ``DjitellopyClient``): SDK ``command``, a battery read from the
state stream, ``takeoff``, a hover of about 3 s at the takeoff height (the
minimum; no climb is commanded), then ``land``. ``E`` stops the motors at any
time (the direct emergency datagram). Any failure after takeoff still sends
``land``. Without ``--execute`` it refuses and sends nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Callable

MAX_HOVER_S = 10.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="room_takeoff_check", description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true",
                        help="required: this flies the real Tello (motors on)")
    parser.add_argument("--local-ip", default=None,
                        help="the laptop's address on the Tello Wi-Fi (default: automatic selection)")
    parser.add_argument("--hover-s", type=float, default=3.0, help=f"hover time, at most {MAX_HOVER_S:.0f} s")
    parser.add_argument("--minimum-battery", type=int, default=0)
    return parser


def run_check(*, build_client: Callable[[], object] | None = None, local_ip: str | None = None,
              hover_s: float = 3.0, minimum_battery: int = 0, sleep: Callable[[float], None] = time.sleep,
              arm_emergency: Callable[[Callable[[], object]], object] | None = None,
              out=sys.stdout) -> int:
    from sentinel.room.live_stack import TelloLink

    if not 0 < hover_s <= MAX_HOVER_S:
        print(f"refused: --hover-s must be in (0, {MAX_HOVER_S:.0f}]", file=sys.stderr)
        return 2
    link = TelloLink(build_client=build_client, local_ip=local_ip)
    hotkey = None
    took_off = False
    result = {"connected": False, "battery_percent": None, "took_off": False, "landed": False}
    try:
        client = link.client()  # SDK "command"
        result["connected"] = True
        battery = client.battery()  # state stream: no SDK command
        result["battery_percent"] = battery
        if not isinstance(battery, int) or battery < minimum_battery:
            print(f"refused: battery {battery!r}% is below {minimum_battery}%", file=sys.stderr)
            return 2
        if arm_emergency is None:
            from sentinel.emergency import EmergencyHotkey

            def arm_emergency(stop):
                armed = EmergencyHotkey(stop)
                armed.start()
                return armed
        print("EMERGENCY: press E to stop motors immediately.", file=sys.stderr)
        hotkey = arm_emergency(client.emergency)
        took_off = True  # from here on, every exit lands
        response = client.takeoff()
        result["took_off"] = str(response).strip().lower() == "ok"
        if not result["took_off"]:
            print(f"takeoff not acknowledged: {response!r}", file=sys.stderr)
        sleep(hover_s)
        response = client.land()
        result["landed"] = str(response).strip().lower() == "ok"
        took_off = False
        print(json.dumps(result), file=out)
        return 0 if result["took_off"] and result["landed"] else 1
    except BaseException as exc:
        if took_off:
            try:
                client.land()
            except Exception:
                pass
        if isinstance(exc, KeyboardInterrupt):
            print("interrupted: land sent", file=sys.stderr)
            return 130
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if hotkey is not None and hasattr(hotkey, "stop"):
            hotkey.stop()
        link.close()


def main(argv: list[str] | None = None, *, build_client: Callable[[], object] | None = None,
         sleep: Callable[[float], None] = time.sleep, arm_emergency=None, out=sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        print("refused: the takeoff check flies the real Tello; pass --execute to run it", file=sys.stderr)
        return 2
    return run_check(build_client=build_client, local_ip=args.local_ip, hover_s=args.hover_s,
                     minimum_battery=args.minimum_battery, sleep=sleep, arm_emergency=arm_emergency, out=out)


if __name__ == "__main__":
    sys.exit(main())
