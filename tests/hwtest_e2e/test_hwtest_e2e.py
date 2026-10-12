"""Hardware end-to-end harness, checked before it goes to the board.

- Receiver (``receiver.py``) unit tests: stream checking on synthetic payloads.
- cocotb (Verilator): ``ChunkGenerator`` + generated core + W5500 model in the three traffic modes
  (``cocotb_hwtest.py``), for the implementation/preset pairs the board test uses.
"""

import shutil

import pytest

from w5500_litex_verilog_driver.config import PRESETS

from ..common import BUILD_DIR, run_cocotb
from .de10lite import DEFAULT_PRESET
from .receiver import CHUNK_BYTES, CNT_MOD, EOF, SOF, StreamChecker, parse_chunk
from .sim_top import TOP, build_sim_top

# (impl, preset) that must sustain one chunk per 1/44100 s in full mode (cf. tests/sim).
PACKET_RATE_OK = {("soc_v2", "stream")}


def chunk(first: int, sof=SOF, eof=EOF) -> bytes:
    cnts = b"".join(((first + i) % CNT_MOD).to_bytes(4, "big") for i in range(8))
    return sof + cnts + eof


def test_chunk_format():
    c = chunk(0xFFFE)
    assert len(c) == CHUNK_BYTES == 40
    assert c.startswith(b"<--|") and c.endswith(b"|-->")
    assert parse_chunk(c) == 0xFFFE  # Counter wraps inside the chunk.


def test_checker_contiguous_across_datagrams():
    chk = StreamChecker()
    chk.feed(chunk(0) + chunk(8))
    chk.feed(chunk(16))
    assert (chk.stats.chunks, chk.stats.lost, chk.stats.malformed) == (3, 0, 0)


def test_checker_counter_wraps():
    chk = StreamChecker()
    chk.feed(chunk(CNT_MOD - 8) + chunk(0))
    assert (chk.stats.chunks, chk.stats.lost) == (2, 0)


def test_checker_counts_lost_chunks():
    chk = StreamChecker()
    chk.feed(chunk(0))
    chk.feed(chunk(32))  # Chunks 8, 16, 24 missing.
    assert (chk.stats.chunks, chk.stats.lost, chk.stats.malformed) == (2, 3, 0)


@pytest.mark.parametrize("bad", [
    chunk(0, sof=b"<--+"),
    chunk(0, eof=b"|--<"),
    chunk(0)[:8] + b"\x00\x01\x00\x07" + chunk(0)[12:],  # Upper half not zero, cnt1 wrong.
    chunk(0)[:-1],                                        # Short.
])
def test_checker_malformed(bad):
    chk = StreamChecker()
    chk.feed(bad)
    assert chk.stats.malformed >= 1
    assert chk.stats.chunks == 0


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not installed")
@pytest.mark.parametrize(("impl", "preset"), sorted(DEFAULT_PRESET.items()))
def test_hwtest_sim(generated, impl, preset):
    out = BUILD_DIR / f"hwtest-{preset}-{impl}"
    top = build_sim_top(PRESETS[preset], generated(preset, impl), out / "gateware")
    run_cocotb(top, "tests.hwtest_e2e.cocotb_hwtest", out / "sim", preset, top=TOP,
               extra_env={"W5500_PACKET_RATE_OK": "1" if (impl, preset) in PACKET_RATE_OK else ""})

