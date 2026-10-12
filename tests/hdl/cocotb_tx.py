"""cocotb behaviour tests for implementations with a TX path (see tests/test_cocotb.py).

The W5500 is the behavioural model from tests/models; the preset comes from ``W5500_PRESET``.
Implementations under test send one datagram per ``tx_frame_bytes`` input bytes.
"""

import os
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from cocotb.utils import get_sim_time

from tests.hdl.cocotb_smoke import CLK_PERIOD_NS, reset
from tests.models.w5500_model import W5500Model, W5500SpiSlave
from w5500_litex_verilog_driver import w5500 as W
from w5500_litex_verilog_driver.config import PRESETS

CFG = PRESETS[os.environ.get("W5500_PRESET", "default32")]
WORDS = CFG.tx_frame_bytes // 4
assert CFG.clk_freq == 1_000_000_000 // CLK_PERIOD_NS, "testbench clock must match clk_freq"

# Bring-up = reset hold + PLL wait + a few hundred SPI frames; generous margin.
INIT_CYCLES = CFG.cycles(CFG.reset_hold_us + CFG.init_wait_us + 2000)
# Generous upper bound for one buffered frame to leave as a datagram (soc_v1: ~135 us).
FRAME_CYCLES = CFG.cycles(1000)

SAMPLE_RATE = 44_100  # Hz, real-time source tests.


def attach(dut, **faults) -> W5500Model:
    model = W5500Model(**faults)
    W5500SpiSlave(dut.w5500_spi_sclk, dut.w5500_spi_cs_n, dut.w5500_spi_mosi,
                  dut.w5500_spi_miso, model).start()
    return model


async def wait_for(dut, cond, cycles, what):
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if cond():
            return
    raise AssertionError(f"timeout after {cycles} cycles waiting for {what}")


async def wait_ready(dut):
    """Wait for status_ready; tx_ready must stay low until then."""
    for _ in range(INIT_CYCLES):
        await RisingEdge(dut.clk)
        assert dut.status_error.value == 0, "status_error during bring-up"
        if dut.status_ready.value == 1:
            return
        assert dut.tx_ready.value == 0, "tx_ready before status_ready"
    raise AssertionError("status_ready not raised")


async def push(dut, word):
    """Present one word (valid high, data stable) and return once it has been accepted."""
    dut.tx_valid.value = 1
    dut.tx_data.value = word
    await RisingEdge(dut.clk)
    while dut.tx_ready.value == 0:
        await RisingEdge(dut.clk)


async def send_words(dut, words, gap_prob=0.3):
    """Drive the tx stream (valid held with stable data until accepted, random idle gaps;
    gap_prob=0: back-to-back, valid never drops between words)."""
    for word in words:
        while random.random() < gap_prob:
            dut.tx_valid.value = 0
            await RisingEdge(dut.clk)
        await push(dut, word)
    dut.tx_valid.value = 0


async def send_realtime(dut, chunks, period_ns):
    """Real-time source that cannot stall: chunk k becomes due at t0 + k * period_ns (rounded
    to clk edges, no drift); its words go out back-to-back and must all be accepted before the
    next chunk is due, otherwise it is an overrun. Returns the smallest slack in ns."""
    def now():  # Integer ns: float sim times accumulate error that Timer() rejects.
        return round(get_sim_time("ns"))

    t0 = now()

    def due(k):
        return t0 + round(k * period_ns / CLK_PERIOD_NS) * CLK_PERIOD_NS

    min_slack = None
    for k, chunk in enumerate(chunks):
        wait = due(k) - now()
        if wait > CLK_PERIOD_NS:  # Idle without per-cycle polling, then land on the due edge.
            await Timer(wait - CLK_PERIOD_NS // 2, "ns")
            await RisingEdge(dut.clk)
        for word in chunk:
            await push(dut, word)
        dut.tx_valid.value = 0
        slack = due(k + 1) - now()
        assert slack >= 0, f"overrun: chunk {k} accepted {-slack} ns after chunk {k + 1} was due"
        min_slack = slack if min_slack is None else min(min_slack, slack)
    return min_slack


def random_frames(n):
    return [[random.getrandbits(32) for _ in range(WORDS)] for _ in range(n)]


def check_sent(model, frames):
    """Every frame left the W5500 as one datagram, in order, to the remote endpoint."""
    assert len(model.sent) == len(frames)
    for i, (dgram, words) in enumerate(zip(model.sent, frames, strict=True)):
        assert dgram.payload == expected_payload(words), f"datagram {i} payload"
        assert dgram.ip == CFG.remote_ip
        assert dgram.port == CFG.remote_port


def expected_payload(words) -> bytes:
    return b"".join(w.to_bytes(4, "big") for w in words)  # tx_data[31:24] goes first.


@cocotb.test()
async def init_configures_w5500(dut):
    """Bring-up writes the configured network/socket registers and opens the UDP socket."""
    model = attach(dut)
    await reset(dut)
    await wait_ready(dut)

    common, sreg = W.BSB_COMMON, W.bsb_socket_reg(CFG.socket)
    assert model.read(common, W.GAR, 4) == CFG.gateway_int.to_bytes(4, "big")
    assert model.read(common, W.SUBR, 4) == CFG.subnet_int.to_bytes(4, "big")
    assert model.read(common, W.SHAR, 6) == CFG.mac_int.to_bytes(6, "big")
    assert model.read(common, W.SIPR, 4) == CFG.ip_int.to_bytes(4, "big")
    assert model.read(sreg, W.Sn_PORT, 2) == CFG.local_port.to_bytes(2, "big")
    assert model.read(sreg, W.Sn_DIPR, 4) == CFG.remote_ip_int.to_bytes(4, "big")
    assert model.read(sreg, W.Sn_DPORT, 2) == CFG.remote_port.to_bytes(2, "big")
    assert model.read(sreg, W.Sn_SR)[0] == W.SOCK_UDP
    assert model.sent == []


@cocotb.test()
async def frames_become_datagrams(dut):
    """Every tx_frame_bytes input bytes are sent as one datagram to the remote endpoint."""
    random.seed(1)
    model = attach(dut)
    await reset(dut)
    await wait_ready(dut)

    frames = random_frames(3)
    start = get_sim_time("ns")
    for words in frames:
        await send_words(dut, words)
    await wait_for(dut, lambda: len(model.sent) == len(frames), 200_000, "datagrams")
    per_frame_us = (get_sim_time("ns") - start) / len(frames) / 1000
    dut._log.info(f"{len(frames)} frames, {per_frame_us:.1f} us per frame "
                  f"({CFG.tx_frame_bytes / per_frame_us:.2f} MB/s)")

    check_sent(model, frames)
    assert dut.status_error.value == 0


@cocotb.test()
@cocotb.parametrize(packets=[10, 100])
async def back_to_back_frames(dut, packets):
    """tx_valid never drops: the source is throttled by tx_ready only; nothing is lost."""
    random.seed(packets)
    model = attach(dut)
    await reset(dut)
    await wait_ready(dut)

    frames = random_frames(packets)
    start = get_sim_time("ns")
    await send_words(dut, [w for words in frames for w in words], gap_prob=0)
    await wait_for(dut, lambda: len(model.sent) == packets, 2 * FRAME_CYCLES, "datagrams")
    per_frame_us = (get_sim_time("ns") - start) / packets / 1000
    dut._log.info(f"{packets} back-to-back frames, {per_frame_us:.1f} us per frame "
                  f"({CFG.tx_frame_bytes / per_frame_us:.2f} MB/s)")

    check_sent(model, frames)
    assert dut.status_error.value == 0


async def realtime_test(dut, packets, words_per_tick):
    random.seed(packets)
    model = attach(dut)
    await reset(dut)
    await wait_ready(dut)

    frames = random_frames(packets)
    words = [w for f in frames for w in f]
    chunks = [words[i:i + words_per_tick] for i in range(0, len(words), words_per_tick)]
    min_slack = await send_realtime(dut, chunks, 1e9 / SAMPLE_RATE)
    dut._log.info(f"{len(chunks)} ticks at {SAMPLE_RATE} Hz, {words_per_tick} word(s) each: "
                  f"min slack {min_slack / 1000:.1f} us")
    await wait_for(dut, lambda: len(model.sent) == packets, 2 * FRAME_CYCLES, "datagrams")

    check_sent(model, frames)
    assert dut.status_error.value == 0


@cocotb.test()
@cocotb.parametrize(packets=[10, 100])
async def realtime_44k1_word_rate(dut, packets):
    """One 32-bit word per 1/44100 s (audio sample rate), one datagram per tx_frame_bytes:
    every word is accepted before the next one is due and every frame is sent."""
    await realtime_test(dut, packets, 1)


@cocotb.test(expect_fail=True)
@cocotb.parametrize(packets=[10, 100])
async def realtime_44k1_packet_rate(dut, packets):
    """One whole frame per 1/44100 s (~1.76 MB/s for 40-byte frames). Beyond soc_v1 (~0.3 MB/s,
    see docs/architecture.md), so it is expected to fail with an overrun; an unexpected pass
    means an implementation now sustains this rate."""
    await realtime_test(dut, packets, WORDS)


@cocotb.test()
async def bad_version_is_sticky_error(dut):
    """Wrong VERSIONR: status_error rises and stays; ready / tx_ready stay low."""
    attach(dut, version=0x00)
    await reset(dut)
    await wait_for(dut, lambda: dut.status_error.value == 1, INIT_CYCLES, "status_error")
    dut.tx_valid.value = 1
    for _ in range(1000):
        await RisingEdge(dut.clk)
        assert dut.status_error.value == 1
        assert dut.status_ready.value == 0
        assert dut.tx_ready.value == 0
    dut.tx_valid.value = 0


@cocotb.test()
async def no_link_holds_ready_low(dut):
    """No PHY link: bring-up waits in the link poll (the error after link_timeout_ms is too far
    out to simulate) - socket not opened, neither ready nor error yet."""
    model = attach(dut, link_up=False)
    await reset(dut)
    # Bring-up stops in the link poll: register setup done, socket never opened.
    await ClockCycles(dut.clk, INIT_CYCLES)
    assert dut.status_ready.value == 0
    assert model.read(W.bsb_socket_reg(CFG.socket), W.Sn_SR)[0] == W.SOCK_CLOSED
    assert dut.status_error.value == 0, "error before link_timeout_ms"


@cocotb.test()
async def arp_timeout_is_sticky_error(dut):
    """Sn_IR.TIMEOUT after SEND: sticky error, nothing sent, input blocked."""
    model = attach(dut, arp_fail=True)
    await reset(dut)
    await wait_ready(dut)
    await send_words(dut, list(range(WORDS)))
    await wait_for(dut, lambda: dut.status_error.value == 1, 100_000, "status_error")
    await ClockCycles(dut.clk, 100)
    assert dut.status_error.value == 1
    assert dut.status_ready.value == 0
    assert dut.tx_ready.value == 0
    assert model.sent == []

