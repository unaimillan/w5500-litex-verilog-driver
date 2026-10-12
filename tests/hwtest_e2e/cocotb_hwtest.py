"""cocotb tests of the hardware test top (sim_top.py): traffic modes end to end through the core.

Run via test_hwtest_e2e.py. The W5500 is the behavioural model from tests/models; every datagram
it sends is checked with the host receiver's ``StreamChecker``.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb.utils import get_sim_time

from tests.models.w5500_model import W5500Model, W5500SpiSlave
from tests.sim.cocotb_smoke import CLK_PERIOD_NS
from w5500_litex_verilog_driver.config import PRESETS

from .receiver import StreamChecker
from .sim_top import SIM_PERIODS
from .traffic import MODE_FULL, MODE_LITTLE, MODE_MIDDLE, MODE_OFF

CFG    = PRESETS[os.environ["W5500_PRESET"]]
CHUNKS = CFG.tx_chunks_per_datagram
INIT_CYCLES  = CFG.cycles(CFG.reset_hold_us + CFG.init_wait_us + 2000)
DRAIN_CYCLES = CFG.cycles(2000)
PACKET_RATE_OK = os.environ.get("W5500_PACKET_RATE_OK") == "1"
SAMPLE_RATE = 44_100


async def bring_up(dut) -> W5500Model:
    model = W5500Model()
    W5500SpiSlave(dut.w5500_spi_sclk, dut.w5500_spi_cs_n, dut.w5500_spi_mosi,
                  dut.w5500_spi_miso, model).start()
    dut.rst.value   = 1
    dut.mode.value  = MODE_OFF
    dut.stall.value = 0
    dut.clear.value = 0
    dut.w5500_int_n.value = 1
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await wait_for(dut, lambda: dut.status_ready.value == 1, INIT_CYCLES, "status_ready")
    return model


async def wait_for(dut, cond, cycles, what):
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        assert dut.status_error.value == 0, f"status_error while waiting for {what}"
        if cond():
            return
    raise AssertionError(f"timeout after {cycles} cycles waiting for {what}")


def sent_chunks(dut) -> int:
    return int(dut.gen_chunks_sent.value)


async def run_mode(dut, mode, n_chunks, cycles_per_chunk) -> float:
    """Run ``mode`` until ``n_chunks`` more chunks are accepted; returns chunks/s."""
    first = sent_chunks(dut)
    dut.mode.value = mode
    await wait_for(dut, lambda: sent_chunks(dut) >= first + 1, 2 * cycles_per_chunk, "1st chunk")
    t0, c0 = get_sim_time("ns"), sent_chunks(dut)
    await wait_for(dut, lambda: sent_chunks(dut) >= c0 + n_chunks,
                   (n_chunks + 2) * cycles_per_chunk, f"{n_chunks} chunks")
    rate = (sent_chunks(dut) - c0) / ((get_sim_time("ns") - t0) * 1e-9)
    dut._log.info(f"mode {mode}: {rate:.0f} chunks/s")
    return rate


async def stop_and_check(dut, model) -> StreamChecker:
    """Mode off, wait until every whole datagram has left the W5500 model, check the stream."""
    dut.mode.value = MODE_OFF
    await ClockCycles(dut.clk, 100)  # A chunk in flight completes.
    expected = sent_chunks(dut) // CHUNKS  # A partial datagram stays in the socket buffer.
    await wait_for(dut, lambda: len(model.sent) >= expected, DRAIN_CYCLES, "datagrams")
    chk = StreamChecker()
    for dgram in model.sent:
        assert (dgram.ip, dgram.port) == (CFG.remote_ip, CFG.remote_port)
        chk.feed(dgram.payload)
    s = chk.stats
    assert len(model.sent) == expected
    assert s.chunks == expected * CHUNKS
    assert s.malformed == 0
    return chk


@cocotb.test()
async def modes_without_loss(dut):
    """little -> middle -> full: every chunk arrives in order, no overruns."""
    model = await bring_up(dut)
    await run_mode(dut, MODE_LITTLE, 3, SIM_PERIODS[MODE_LITTLE])
    await run_mode(dut, MODE_MIDDLE, 10, SIM_PERIODS[MODE_MIDDLE])
    rate = await run_mode(dut, MODE_FULL, 30, SIM_PERIODS[MODE_MIDDLE])
    chk = await stop_and_check(dut, model)

    assert chk.stats.lost == 0
    assert dut.gen_overruns.value == 0
    assert dut.gen_overrun.value == 0
    if PACKET_RATE_OK:
        assert rate >= SAMPLE_RATE, f"full mode {rate:.0f} chunks/s < {SAMPLE_RATE}"


@cocotb.test()
async def overrun_skips_counter(dut):
    """Middle mode with the source cut off for ~3.5 periods: the dropped chunks are counted and
    show up as exactly that many lost chunks at the receiver; KEY1 (clear) resets the counters."""
    model = await bring_up(dut)
    period = SIM_PERIODS[MODE_MIDDLE]
    await run_mode(dut, MODE_MIDDLE, 3, period)
    dut.stall.value = 1
    await ClockCycles(dut.clk, period * 7 // 2)
    dut.stall.value = 0
    await run_mode(dut, MODE_MIDDLE, 4 * CHUNKS, period)
    chk = await stop_and_check(dut, model)

    overruns = int(dut.gen_overruns.value)
    assert overruns >= 2
    assert dut.gen_overrun.value == 1
    assert chk.stats.lost == overruns

    dut.clear.value = 1
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)
    assert (int(dut.gen_overruns.value), int(dut.gen_chunks_sent.value)) == (0, 0)
    assert dut.gen_overrun.value == 0
