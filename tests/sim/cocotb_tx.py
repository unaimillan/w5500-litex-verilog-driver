"""cocotb behaviour tests for implementations with a TX path (see tests/sim/test_cocotb.py).

The W5500 is the behavioural model from tests/models; the preset comes from ``W5500_PRESET``.
Implementations under test send one datagram per ``tx_chunks_per_datagram`` chunks of
``tx_chunk_bytes`` input bytes. Test sizes (``packets``) count chunks.
"""

import os
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from cocotb.utils import get_sim_time

from tests.models.w5500_model import W5500Model, W5500SpiSlave
from tests.sim.cocotb_smoke import CLK_PERIOD_NS, reset
from w5500_litex_verilog_driver import w5500 as W
from w5500_litex_verilog_driver.config import PRESETS

CFG = PRESETS[os.environ.get("W5500_PRESET", "default32")]
WORDS  = CFG.tx_chunk_bytes // 4          # Words per chunk.
CHUNKS = CFG.tx_chunks_per_datagram       # Chunks per datagram.
assert CFG.clk_freq == 1_000_000_000 // CLK_PERIOD_NS, "testbench clock must match clk_freq"

# Bring-up = reset hold + PLL wait + a few hundred SPI frames; generous margin.
INIT_CYCLES = CFG.cycles(CFG.reset_hold_us + CFG.init_wait_us + 2000)
# Generous upper bound for the buffered input to leave as datagrams once the source is done.
DRAIN_CYCLES = CFG.cycles(2000)

SAMPLE_RATE = 44_100  # Hz, real-time source tests.
# The (impl, preset) under test sustains one chunk per 1/SAMPLE_RATE (see tests/sim/test_cocotb.py).
PACKET_RATE_OK = os.environ.get("W5500_PACKET_RATE_OK") == "1"


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


def random_chunks(n):
    assert n % CHUNKS == 0, f"{n} chunks do not fill whole datagrams of {CHUNKS}"
    return [[random.getrandbits(32) for _ in range(WORDS)] for _ in range(n)]


def check_sent(model, chunks):
    """Every CHUNKS chunks left the W5500 as one datagram, in order, to the remote endpoint."""
    dgrams = [[w for c in chunks[i:i + CHUNKS] for w in c] for i in range(0, len(chunks), CHUNKS)]
    assert len(model.sent) == len(dgrams)
    for i, (dgram, words) in enumerate(zip(model.sent, dgrams, strict=True)):
        assert dgram.payload == expected_payload(words), f"datagram {i} payload"
        assert dgram.ip == CFG.remote_ip
        assert dgram.port == CFG.remote_port


def log_rate(dut, what, n_chunks, start_ns):
    us = (get_sim_time("ns") - start_ns) / 1000
    dut._log.info(f"{what}: {us / n_chunks:.1f} us per chunk, "
                  f"{us * CHUNKS / n_chunks:.1f} us per datagram "
                  f"({n_chunks * CFG.tx_chunk_bytes / us:.2f} MB/s)")


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
async def chunks_become_datagrams(dut):
    """Every CHUNKS x tx_chunk_bytes input bytes are sent as one datagram to the remote endpoint."""
    random.seed(1)
    model = attach(dut)
    await reset(dut)
    await wait_ready(dut)

    chunks = random_chunks(3 * CHUNKS)
    start = get_sim_time("ns")
    for words in chunks:
        await send_words(dut, words)
    await wait_for(dut, lambda: len(model.sent) == 3, DRAIN_CYCLES, "datagrams")
    log_rate(dut, f"{len(chunks)} chunks, random gaps", len(chunks), start)

    check_sent(model, chunks)
    assert dut.status_error.value == 0


@cocotb.test()
@cocotb.parametrize(packets=[10, 100])
async def back_to_back_chunks(dut, packets):
    """tx_valid never drops: the source is throttled by tx_ready only; nothing is lost."""
    random.seed(packets)
    model = attach(dut)
    await reset(dut)
    await wait_ready(dut)

    chunks = random_chunks(packets)
    start = get_sim_time("ns")
    await send_words(dut, [w for words in chunks for w in words], gap_prob=0)
    await wait_for(dut, lambda: len(model.sent) == packets // CHUNKS, DRAIN_CYCLES, "datagrams")
    log_rate(dut, f"{packets} back-to-back chunks", packets, start)

    check_sent(model, chunks)
    assert dut.status_error.value == 0


async def realtime_test(dut, packets, words_per_tick):
    random.seed(packets)
    model = attach(dut)
    await reset(dut)
    await wait_ready(dut)

    chunks = random_chunks(packets)
    words = [w for c in chunks for w in c]
    ticks = [words[i:i + words_per_tick] for i in range(0, len(words), words_per_tick)]
    min_slack = await send_realtime(dut, ticks, 1e9 / SAMPLE_RATE)
    dut._log.info(f"{len(ticks)} ticks at {SAMPLE_RATE} Hz, {words_per_tick} word(s) each: "
                  f"min slack {min_slack / 1000:.1f} us")
    await wait_for(dut, lambda: len(model.sent) == packets // CHUNKS, DRAIN_CYCLES, "datagrams")

    check_sent(model, chunks)
    assert dut.status_error.value == 0


@cocotb.test()
@cocotb.parametrize(packets=[10, 100])
async def realtime_44k1_word_rate(dut, packets):
    """One 32-bit word per 1/44100 s (audio sample rate): every word is accepted before the
    next one is due and every datagram is sent."""
    await realtime_test(dut, packets, 1)


@cocotb.test(expect_fail=not PACKET_RATE_OK)
@cocotb.parametrize(packets=[10, 100])
async def realtime_44k1_packet_rate(dut, packets):
    """One whole chunk per 1/44100 s (~1.76 MB/s for 40-byte chunks). Must pass where
    W5500_PACKET_RATE_OK is set (soc_v2 on the stream preset); elsewhere (soc_v1: ~0.3 MB/s, see
    docs/architecture.md) it is an expected failure with an overrun, and an unexpected pass means
    the implementation now sustains this rate."""
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
    await send_words(dut, list(range(WORDS * CHUNKS)))  # One datagram.
    await wait_for(dut, lambda: dut.status_error.value == 1, 100_000, "status_error")
    await ClockCycles(dut.clk, 100)
    assert dut.status_error.value == 1
    assert dut.status_ready.value == 0
    assert dut.tx_ready.value == 0
    assert model.sent == []

