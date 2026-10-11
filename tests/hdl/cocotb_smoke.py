"""cocotb smoke tests run against every implementation (see tests/test_cocotb.py).

Only checks invariants that hold for the stubs as well as for real implementations; behaviour
tests (init sequence, TX/RX datagrams) come with the first real implementation.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from tests.models.w5500_model import W5500SpiSlave

CLK_PERIOD_NS = 20  # 50 MHz, matches the default preset.


def start(dut):
    """Start the clock and drive every DUT input to its idle value; reset asserted."""
    dut.rst.value = 1
    dut.tx_valid.value = 0
    dut.tx_data.value = 0
    dut.tx_last.value = 0
    dut.rx_ready.value = 0
    dut.w5500_int_n.value = 1
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def reset(dut, cycles=10):
    start(dut)
    await ClockCycles(dut.clk, cycles)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def idle_in_reset(dut):
    """No SPI traffic, no stream activity and W5500 held in reset while `rst` is high."""
    W5500SpiSlave(dut.w5500_spi_sclk, dut.w5500_spi_cs_n, dut.w5500_spi_mosi,
                  dut.w5500_spi_miso).start()
    start(dut)
    for _ in range(50):
        await RisingEdge(dut.clk)
        assert dut.w5500_spi_cs_n.value == 1
        assert dut.w5500_rst_n.value == 0
        assert dut.tx_ready.value == 0
        assert dut.rx_valid.value == 0
        assert dut.status_ready.value == 0


@cocotb.test()
async def rx_valid_holds_until_ready(dut):
    """Stream rule: once rx_valid rises it stays (with stable data) until rx_ready."""
    await reset(dut)
    prev_valid = prev_data = None
    for _ in range(2000):
        await RisingEdge(dut.clk)
        valid, data = int(dut.rx_valid.value), int(dut.rx_data.value)
        if prev_valid:
            assert valid, "rx_valid dropped without rx_ready"
            assert data == prev_data, "rx_data changed while waiting for rx_ready"
        prev_valid, prev_data = valid, data
