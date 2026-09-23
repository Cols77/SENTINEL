"""Does the Tello OBEY commands from a UDP port other than the one that sent its first `command`?

Motors off, drone on the ground. Run it right after power-on, before anything else talks to the
drone. It matters for the emergency motor cut: DjitellopyClient.emergency() sends from a fresh
ephemeral port; if the Tello ignored other ports, the cut would be silently dropped.

    uv run python scripts/tello_port_probe.py

Method: `command` from port 8889 (what DJITelloPy uses), then `speed N` from a second, ephemeral
port, then `speed?` from 8889. Every reply goes to the FIRST port whatever port asked (measured
2026-09-22), so the queue on 8889 is drained before each question: the first version of this
script read the misrouted `ok` to `speed N` as the answer to `speed?` and reported IGNORED.
"""
import socket
import sys
import time

TELLO = ("192.168.10.1", 8889)


def drain(sock: socket.socket, wait: float = 0.5) -> list[str]:
    sock.settimeout(wait)
    replies = []
    while True:
        try:
            replies.append(sock.recvfrom(1024)[0].decode("ascii", errors="backslashreplace").strip())
        except OSError:
            return replies


def ask(sock: socket.socket, command: str, wait: float = 1.0) -> list[str]:
    drain(sock, 0.2)
    sock.sendto(command.encode("ascii"), TELLO)
    time.sleep(wait)
    return drain(sock)


def main() -> int:
    local_ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.2"
    main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    main_sock.bind((local_ip, 8889))
    other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    other.bind((local_ip, 0))
    print(f"main port 8889, second port {other.getsockname()[1]}")
    if ask(main_sock, "command") != ["ok"]:
        print("No 'ok' to 'command' on 8889: power-cycle the drone and run this first.")
        return 1
    before = ask(main_sock, "speed?")
    target = "40" if before and before[-1].startswith("30") else "30"
    drain(main_sock, 0.2)
    other.sendto(f"speed {target}".encode("ascii"), TELLO)
    time.sleep(1.0)
    on_main, on_other = drain(main_sock), drain(other)
    after = ask(main_sock, "speed?")
    ask(main_sock, "speed 20")
    accepted = bool(after) and after[-1].startswith(target)
    print(f"speed before {before}; 'speed {target}' from the second port -> replies on 8889 {on_main}, "
          f"on the second port {on_other}; speed after {after}")
    print("RESULT: the second port was " + ("OBEYED" if accepted else "IGNORED") +
          ("" if accepted else " - an emergency sent from a fresh port would be dropped"))
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
