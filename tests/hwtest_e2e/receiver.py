#!/usr/bin/env python3
"""UDP receiver for the hardware end-to-end test: checks the counter chunks and reports rates.

Standard library only, so it also runs on a host without the project installed:

    python3 receiver.py [--port 5000] [--bind 0.0.0.0] [--expect-rate CHUNKS_PER_S]

Chunk format (see traffic.py): 40 bytes = SOF "<--|", 8 x {16'h0, cnt} big-endian, EOF "|-->";
``cnt`` continues across chunks (+8 per chunk, mod 2**16). A datagram carries one or more chunks.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from dataclasses import dataclass

SOF = b"<--|"
EOF = b"|-->"
CNT_WORDS   = 8
CHUNK_BYTES = 4 + 4 * CNT_WORDS + 4
CNT_MOD     = 1 << 16
_CNTS       = struct.Struct(f">{CNT_WORDS}I")


def parse_chunk(chunk: bytes) -> int | None:
    """First counter value of a well-formed chunk, None if malformed."""
    if len(chunk) != CHUNK_BYTES or chunk[:4] != SOF or chunk[-4:] != EOF:
        return None
    cnts = _CNTS.unpack_from(chunk, 4)
    first = cnts[0]
    if any(c != (first + i) % CNT_MOD for i, c in enumerate(cnts)):  # Also: upper 16 bits = 0.
        return None
    return first


@dataclass
class Stats:
    datagrams: int = 0
    bytes:     int = 0
    chunks:    int = 0  # Well-formed chunks.
    lost:      int = 0  # Chunks missing between two received ones (counter gap / 8).
    malformed: int = 0  # Bad SOF/EOF/counter words, or datagram length not a multiple of 40.

    def __sub__(self, other: Stats) -> Stats:
        return Stats(*(a - b for a, b in zip(vars(self).values(), vars(other).values(),
                                             strict=True)))


class StreamChecker:
    """Feeds datagram payloads; tracks counter continuity across chunks and datagrams."""

    def __init__(self):
        self.stats = Stats()
        self.next_cnt: int | None = None

    def feed(self, payload: bytes):
        s = self.stats
        s.datagrams += 1
        s.bytes     += len(payload)
        if len(payload) % CHUNK_BYTES:
            s.malformed += 1
        for off in range(0, len(payload) - CHUNK_BYTES + 1, CHUNK_BYTES):
            first = parse_chunk(payload[off:off + CHUNK_BYTES])
            if first is None:
                s.malformed += 1
                continue
            if self.next_cnt is not None and first != self.next_cnt:
                gap = (first - self.next_cnt) % CNT_MOD
                s.lost += -(-gap // CNT_WORDS)  # A gap that is not a whole chunk still counts.
            self.next_cnt = (first + CNT_WORDS) % CNT_MOD
            s.chunks += 1


def report(label: str, d: Stats, seconds: float) -> str:
    return (f"{label} {d.datagrams / seconds:8.0f} dgram/s {d.chunks / seconds:8.0f} chunk/s "
            f"{d.bytes / seconds / 1e3:8.1f} kB/s  lost {d.lost:6d}  malformed {d.malformed}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bind", default="0.0.0.0", help="Local address (default: all).")
    parser.add_argument("--port", type=int, default=5000, help="UDP port (CoreConfig.remote_port).")
    parser.add_argument("--expect-rate", type=float, metavar="CHUNKS_PER_S",
                        help="Flag seconds below this chunk rate (e.g. 44100 for soc_v2 full).")
    args = parser.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.2)
    print(f"listening on {args.bind}:{args.port} "
          f"(rcvbuf {sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)} B), Ctrl-C to stop")

    checker = StreamChecker()
    t_start = t_last = time.monotonic()
    t_first = None
    last = Stats()
    try:
        while True:
            try:
                payload, _ = sock.recvfrom(2048)
                if t_first is None:
                    t_first = time.monotonic()
                checker.feed(payload)
            except TimeoutError:
                pass
            now = time.monotonic()
            if now - t_last >= 1.0:
                d = checker.stats - last
                flag = ""
                if args.expect_rate and d.chunks and d.chunks / (now - t_last) < args.expect_rate:
                    flag = "  < expected rate"
                print(report(f"[{now - t_start:7.1f} s]", d, now - t_last) + flag, flush=True)
                last, t_last = Stats(**vars(checker.stats)), now
    except KeyboardInterrupt:
        pass

    s = checker.stats
    seconds = time.monotonic() - (t_first or t_start)
    print(f"\ntotal: {s.datagrams} datagrams, {s.chunks} chunks, {s.bytes} B, lost {s.lost}, "
          f"malformed {s.malformed}")
    if s.datagrams:
        print(report("average", s, seconds))
    return 1 if s.lost or s.malformed else 0


if __name__ == "__main__":
    sys.exit(main())
