"""Container-only init wrapper; production process ownership stays with systemd."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    stopping = False
    child: subprocess.Popen | None = None

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        child = subprocess.Popen([sys.executable, "-m", "aegis.service", "--config", args.config])
        status = child.wait()
        child = None
        if stopping:
            break
        if status == 0:
            time.sleep(1)
            break
        time.sleep(0.5)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
