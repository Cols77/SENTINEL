"""Does the Tello ACCEPT commands from a UDP port other than the one that sent its first `command`?

Motors off, drone on the ground. Run it right after power-on, before anything else talks to the
drone. It matters for the emergency motor cut: DjitellopyClient.emergency() sends from a fresh
ephemeral port; if the Tello ignores other ports, the cut is silently dropped.

    uv run python scripts/tello_port_probe.py

Method: `command` from port 8889 (what DJITelloPy uses), then `speed 30` from a second, ephemeral
port, then `speed?` from 8889. A reading of 30 means the second port was obeyed.
"""
import socket
import sys
import time

TELLO = ("192.168.10.1", 8889)


def ask(sock: socket.socket, command: str, timeout: float = 3.0) -> str:
    sock.settimeout(timeout)
    sock.sendto(command.encode("ascii"), TELLO)
    try:
        return sock.recvfrom(1024)[0].decode("ascii", errors="backslashreplace").strip()
    except OSError as exc:
        return f"<{type(exc).__name__}>"


def main() -> int:
    local_ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.2"
    main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    main_sock.bind((local_ip, 8889))
    other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    other.bind((local_ip, 0))
    print(f"main port 8889, second port {other.getsockname()[1]}")
    if ask(main_sock, "command") != "ok":
        print("No 'ok' to 'command' on 8889: power-cycle the drone and run this first.")
        return 1
    before = ask(main_sock, "speed?")
    target = "30" if not before.startswith("30") else "40"
    reply_on_other = ask(other, f"speed {target}")
    time.sleep(0.3)
    after = ask(main_sock, "speed?")
    ask(main_sock, "speed 20")
    accepted = after.startswith(target)
    print(f"speed before {before!r}; 'speed {target}' from the second port -> reply {reply_on_other!r}; "
          f"speed after {after!r}")
    print("RESULT: the second port was " + ("OBEYED" if accepted else "IGNORED") +
          ("" if accepted else " - an emergency sent from a fresh port would be dropped"))
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
