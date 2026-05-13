from __future__ import annotations

import json
import os
import selectors
import signal
import sys
import termios
import time
import tty
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

BAUD_RATES = {
    1200: termios.B1200,
    2400: termios.B2400,
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def capture_serial(
    device: str,
    out: Path,
    *,
    baud: int = 9600,
    parity: str = "none",
    stopbits: int = 1,
    bytesize: int = 8,
    duration: float | None = None,
    chunk_size: int = 256,
) -> int:
    stop_at = time.monotonic() + duration if duration else None
    stopped = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    previous_sigint = signal.signal(signal.SIGINT, stop)
    previous_sigterm = signal.signal(signal.SIGTERM, stop)
    try:
        with serial_fd(device, baud, parity=parity, stopbits=stopbits, bytesize=bytesize) as fd, output_file(out) as fh:
            selector = selectors.DefaultSelector()
            selector.register(fd, selectors.EVENT_READ)
            while not stopped:
                if stop_at and time.monotonic() >= stop_at:
                    break
                events = selector.select(timeout=0.5)
                for key, _mask in events:
                    data = os.read(key.fd, chunk_size)
                    if not data:
                        continue
                    fh.write(
                        json.dumps(
                            {
                                "ts": time.time(),
                                "source": "serial",
                                "device": device,
                                "baud": baud,
                                "parity": parity,
                                "stopbits": stopbits,
                                "bytesize": bytesize,
                                "data_hex": data.hex(" ").upper(),
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    fh.flush()
        return 0
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


@contextmanager
def serial_fd(device: str, baud: int, *, parity: str, stopbits: int, bytesize: int) -> Iterator[int]:
    if baud not in BAUD_RATES:
        raise ValueError(f"unsupported baud {baud}; supported: {sorted(BAUD_RATES)}")
    if parity not in {"none", "even", "odd"}:
        raise ValueError("parity must be none, even, or odd")
    if stopbits not in {1, 2}:
        raise ValueError("stopbits must be 1 or 2")
    if bytesize not in {7, 8}:
        raise ValueError("bytesize must be 7 or 8")

    fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        original_attrs = termios.tcgetattr(fd)
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[4] = BAUD_RATES[baud]
        attrs[5] = BAUD_RATES[baud]
        attrs[2] |= termios.CLOCAL | termios.CREAD

        if stopbits == 2:
            attrs[2] |= termios.CSTOPB
        else:
            attrs[2] &= ~termios.CSTOPB

        if parity == "none":
            attrs[2] &= ~termios.PARENB
        elif parity == "even":
            attrs[2] |= termios.PARENB
            attrs[2] &= ~termios.PARODD
        else:
            attrs[2] |= termios.PARENB
            attrs[2] |= termios.PARODD

        attrs[2] &= ~termios.CSIZE
        attrs[2] |= termios.CS8 if bytesize == 8 else termios.CS7
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 1
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original_attrs)
        os.close(fd)


@contextmanager
def output_file(path: Path) -> Iterator[TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(path) == "-":
        yield sys.stdout
    else:
        with path.open("a", encoding="utf-8") as fh:
            yield fh
