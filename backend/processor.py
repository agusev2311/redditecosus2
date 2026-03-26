from __future__ import annotations

import signal
import threading

from app import create_app


def main() -> None:
    create_app()

    stop_event = threading.Event()

    def _handle_signal(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    while not stop_event.wait(3600):
        pass


if __name__ == "__main__":
    main()
