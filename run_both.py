"""Single-process launcher for platforms like Railway where one deployable
service gets one attached volume. Psylocke 1 and Psylocke 2 share a SQLite
file on that volume, so they're launched here as two subprocesses of one
service rather than as two separate services.

If either subprocess exits, both are torn down together so the platform's
restart policy brings them back up in lockstep, rather than leaving one bot
running alone against a signals table nothing is producing for (or nothing
is consuming from).
"""
import signal
import subprocess
import sys
import time

COMMANDS = [
    [sys.executable, "-m", "psylocke.signal_bot"],
    [sys.executable, "-m", "psylocke.execution_bot"],
]


def main() -> int:
    procs = [subprocess.Popen(cmd) for cmd in COMMANDS]

    def _terminate(*_args):
        for p in procs:
            if p.poll() is None:
                p.terminate()

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    exit_code = 0
    try:
        while True:
            for p in procs:
                ret = p.poll()
                if ret is not None:
                    exit_code = ret
                    _terminate()
                    for other in procs:
                        other.wait()
                    return exit_code
            time.sleep(1)
    finally:
        _terminate()


if __name__ == "__main__":
    sys.exit(main())
