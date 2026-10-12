"""Simulation top of the hardware test: ChunkGenerator + generated core, without board I/O.

Same wiring as ``de10lite.HwTestTop`` (the core is an ``Instance`` of the generated file), plus a
``stall`` input that cuts the generator off the core to provoke overruns. Ports:
``clk, rst, mode[1:0], stall, clear``, ``w5500_*`` (as on the core), ``status_ready/error``,
``gen_chunks_sent[31:0], gen_overruns[15:0], gen_overrun``.
"""

from __future__ import annotations

from litex.build.generic_platform import Pins, Subsignal
from litex.gen import LiteXModule
from migen import ClockDomain, Constant, Instance, Signal

from w5500_litex_verilog_driver.core import PORT_GROUPS
from w5500_litex_verilog_driver.platform import VirtualPlatform

from .traffic import MODE_LITTLE, MODE_MIDDLE, ChunkGenerator

TOP  = "hwtest_sim"
CORE = "w5500_udp"

# Little is scaled down (500 us instead of 10 ms per chunk) to keep simulations short.
SIM_PERIODS = {MODE_LITTLE: 25_000, MODE_MIDDLE: 12_500}

_io = [
    ("clk",   0, Pins(1)),
    ("rst",   0, Pins(1)),
    ("mode",  0, Pins(2)),
    ("stall", 0, Pins(1)),
    ("clear", 0, Pins(1)),
    ("w5500", 0, *[Subsignal(n, Pins(w)) for n, _, w in PORT_GROUPS["w5500"]]),
    ("status", 0, Subsignal("ready", Pins(1)), Subsignal("error", Pins(1))),
    ("gen", 0,
        Subsignal("chunks_sent", Pins(32)),
        Subsignal("overruns",    Pins(16)),
        Subsignal("overrun",     Pins(1)),
    ),
]


class _SimTop(LiteXModule):
    def __init__(self, platform, cfg, core_path):
        self.cd_sys = ClockDomain()
        self.comb += [
            self.cd_sys.clk.eq(platform.request("clk")),
            self.cd_sys.rst.eq(platform.request("rst")),
        ]
        platform.add_source(core_path)

        self.gen = gen = ChunkGenerator(SIM_PERIODS)
        stall    = platform.request("stall")
        tx_valid = Signal()
        tx_ready = Signal()
        w5500    = platform.request("w5500")
        status   = platform.request("status")
        out      = platform.request("gen")
        self.comb += [
            gen.mode.eq(platform.request("mode")),
            gen.clear.eq(platform.request("clear")),
            gen.enable.eq(status.ready),
            tx_valid.eq(gen.valid & ~stall),
            gen.ready.eq(tx_ready & ~stall),
            out.chunks_sent.eq(gen.chunks_sent),
            out.overruns.eq(gen.overruns),
            out.overrun.eq(gen.overrun),
        ]
        self.specials += Instance(CORE,
            i_clk            = self.cd_sys.clk,
            i_rst            = self.cd_sys.rst,
            o_w5500_spi_sclk = w5500.spi_sclk,
            o_w5500_spi_cs_n = w5500.spi_cs_n,
            o_w5500_spi_mosi = w5500.spi_mosi,
            i_w5500_spi_miso = w5500.spi_miso,
            o_w5500_rst_n    = w5500.rst_n,
            i_w5500_int_n    = w5500.int_n,
            i_tx_valid       = tx_valid,
            o_tx_ready       = tx_ready,
            i_tx_data        = gen.data,
            i_tx_last        = gen.last,
            o_rx_valid       = Signal(),
            i_rx_ready       = Constant(1, 1),
            o_rx_data        = Signal(cfg.data_width),
            o_rx_last        = Signal(),
            o_status_ready   = status.ready,
            o_status_error   = status.error,
        )


def build_sim_top(cfg, core_path, output_dir) -> str:
    """Write ``<output_dir>/hwtest_sim.v`` (sim top + the generated core); returns its path."""
    platform = VirtualPlatform(_io, name=TOP)
    return platform.build(_SimTop(platform, cfg, str(core_path)), build_dir=str(output_dir),
                          build_name=TOP)
