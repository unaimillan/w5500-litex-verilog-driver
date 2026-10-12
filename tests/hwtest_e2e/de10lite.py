"""DE10-Lite hardware end-to-end test: generated W5500 core + counter traffic source.

The core is generated into ``<output-dir>/core/w5500_udp.v`` and integrated the way a user design
would: the single Verilog file is added as a source and instantiated as a black box. The rest is
a small LiteX design for the Terasic DE10-Lite with the W5500 Lite on an Arduino shield.

    uv run python -m tests.hwtest_e2e.de10lite --impl soc_v2 [--preset stream] [--build]

Without ``--build`` only the Verilog and the Quartus project are written; with it Quartus runs
and the bitstream lands in ``<output-dir>/gateware/hwtest_de10lite.sof``. See README.md.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys

from litex.build.generic_platform import IOStandard, Pins, Subsignal
from litex.gen import LiteXModule
from litex.soc.cores.clock.common import AsyncResetSynchronizer
from litex_boards.platforms import terasic_de10lite
from migen import Array, Cat, ClockDomain, ClockSignal, Constant, If, Instance, ResetSignal, Signal
from migen.genlib.cdc import MultiReg

from w5500_litex_verilog_driver.cli import generate
from w5500_litex_verilog_driver.config import PRESETS, CoreConfig
from w5500_litex_verilog_driver.impl import unsupported_reason

from .traffic import ChunkGenerator, default_periods

TOP  = "hwtest_de10lite"
CORE = "w5500_udp"

# Preset used for each implementation unless --preset is given.
DEFAULT_PRESET = {"soc_v1": "de10lite", "soc_v2": "stream"}

# W5500 Lite on the Arduino shield: SPI on D10..D13, pin mapping from verilog-mic-array by
# Mike Kuskov (https://github.com/unaimillan/verilog-mic-array). RSTn is on the shield RESET line
# (ARDUINO_RESET_N, connector pin 16 = F16). INTn is not wired.
_w5500_io = [
    ("w5500", 0,
        Subsignal("cs_n",  Pins("arduino:10")),
        Subsignal("mosi",  Pins("arduino:11")),
        Subsignal("miso",  Pins("arduino:12")),
        Subsignal("sclk",  Pins("arduino:13")),
        Subsignal("rst_n", Pins("arduino:16")),
        IOStandard("3.3-V LVTTL"),
    ),
]

# Board I/O is asynchronous to clk50 (switches, keys) or static to the eye (LEDs, 7-segment).
# derive_clock_uncertainty: Quartus otherwise warns that clk50 has no uncertainty assignment.
_extra_sdc = [
    "derive_clock_uncertainty",
    "set_false_path -from [get_ports {user_sw*}]",
    "set_false_path -from [get_ports {user_btn*}]",
    "set_false_path -to [get_ports {user_led*}]",
    "set_false_path -to [get_ports {seven_seg*}]",
]

# 7-segment patterns (segments a..g = bits 0..6, active high; the board pins are active low).
_HEX_SEGMENTS = [0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07,
                 0x7F, 0x6F, 0x77, 0x7C, 0x39, 0x5E, 0x79, 0x71]


class _CRG(LiteXModule):
    """clk50 straight to sys (the core is generated for 50 MHz); reset = power-on | KEY0."""

    def __init__(self, platform):
        self.cd_sys = ClockDomain()
        self.cd_por = ClockDomain(reset_less=True)

        clk50 = platform.request("clk50")
        por   = Signal(16, reset=2**16 - 1)  # ~1.3 ms power-on reset.
        self.comb += [
            self.cd_sys.clk.eq(clk50),
            self.cd_por.clk.eq(clk50),
        ]
        self.sync.por += If(por != 0, por.eq(por - 1))
        key0_pressed = ~platform.request("user_btn", 0)  # KEY0, active low.
        self.specials += AsyncResetSynchronizer(self.cd_sys, (por != 0) | key0_pressed)


class HwTestTop(LiteXModule):
    def __init__(self, platform, cfg: CoreConfig, core_path: str):
        self.crg = _CRG(platform)

        # Generated core as a black box.
        platform.add_source(core_path)
        w5500  = platform.request("w5500")
        ready  = Signal()
        error  = Signal()
        self.gen = gen = ChunkGenerator(default_periods(cfg.clk_freq))
        self.specials += Instance(CORE,
            i_clk            = ClockSignal("sys"),
            i_rst            = ResetSignal("sys"),
            o_w5500_spi_sclk = w5500.sclk,
            o_w5500_spi_cs_n = w5500.cs_n,
            o_w5500_spi_mosi = w5500.mosi,
            i_w5500_spi_miso = w5500.miso,
            o_w5500_rst_n    = w5500.rst_n,
            i_w5500_int_n    = Constant(1, 1),
            i_tx_valid       = gen.valid,
            o_tx_ready       = gen.ready,
            i_tx_data        = gen.data,
            i_tx_last        = gen.last,
            o_rx_valid       = Signal(),
            i_rx_ready       = Constant(1, 1),
            o_rx_data        = Signal(cfg.data_width),
            o_rx_last        = Signal(),
            o_status_ready   = ready,
            o_status_error   = error,
        )

        # Switches / keys (asynchronous): SW[1:0] mode, SW9 display select, KEY1 clear.
        sw   = Signal(10)
        key1 = Signal()
        self.specials += [
            MultiReg(Cat(*[platform.request("user_sw", i) for i in range(10)]), sw),
            MultiReg(~platform.request("user_btn", 1), key1),
        ]
        self.comb += [
            gen.mode.eq(sw[0:2]),
            gen.enable.eq(ready),
            gen.clear.eq(key1),
        ]

        # LEDs.
        heartbeat = Signal(25)
        self.sync += heartbeat.eq(heartbeat + 1)
        leds = Cat(*[platform.request("user_led", i) for i in range(10)])
        self.comb += leds.eq(Cat(
            ready,                      # LEDR0 core up (status_ready)
            error,                      # LEDR1 sticky core error (status_error)
            gen.overrun,                # LEDR2 a chunk was dropped (periodic modes)
            gen.valid & ~gen.ready,     # LEDR3 source stalled by the core
            heartbeat[-1],              # LEDR4 clock running
            Constant(0, 3),             # LEDR5..7
            gen.active_mode,            # LEDR8..9 mode of the current / last chunk
        ))

        # HEX5..0: chunks sent (24 LSBs), or the overrun count with SW9 up.
        value = Signal(24)
        self.comb += If(sw[9], value.eq(gen.overruns)).Else(value.eq(gen.chunks_sent))
        for i in range(6):
            pads   = platform.request("seven_seg", i)
            nibble = value[4 * i:4 * i + 4]
            segs   = Signal(7)
            self.comb += [
                segs.eq(Array(Constant(s, 7) for s in _HEX_SEGMENTS)[nibble]),
                pads.eq(~Cat(segs, Constant(0, 1))),  # Active low; DP off.
            ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--impl", required=True, choices=sorted(DEFAULT_PRESET),
                        help="Core implementation.")
    parser.add_argument("--preset", choices=sorted(PRESETS),
                        help="CoreConfig preset (default: de10lite for soc_v1, stream for soc_v2).")
    parser.add_argument("--output-dir", help="Default: build/hwtest/<impl>.")
    parser.add_argument("--build", action="store_true", help="Run Quartus (needs quartus_sh).")
    args = parser.parse_args(argv)

    preset = args.preset or DEFAULT_PRESET[args.impl]
    cfg    = PRESETS[preset]
    if reason := unsupported_reason(args.impl, cfg):
        parser.error(f"{args.impl} does not support preset {preset}: {reason}")
    if cfg.clk_freq != 50_000_000:
        parser.error(f"preset {preset}: clk_freq must be 50 MHz (DE10-Lite clk50, no PLL)")
    if cfg.data_width != 32:
        parser.error(f"preset {preset}: data_width must be 32 (one counter word per beat)")
    if cfg.tx_chunk_bytes != 40:
        parser.error(f"preset {preset}: tx_chunk_bytes must be 40 (SOF + 8 counters + EOF)")
    if args.build and shutil.which("quartus_sh") is None:
        parser.error("quartus_sh not found: add <quartus>/bin to PATH")

    out = os.path.abspath(args.output_dir or os.path.join("build", "hwtest", args.impl))
    logging.disable(logging.INFO)  # LiteX SoC banner, bus/CSR maps of the core's SoC.
    core_path = generate(cfg, args.impl, CORE, os.path.join(out, "core"))
    print(f"[hwtest] core: {os.path.relpath(core_path)}  (impl={args.impl}, preset={preset})")

    platform = terasic_de10lite.Platform()
    platform.add_extension(_w5500_io)
    platform.toolchain.additional_sdc_commands += _extra_sdc
    top = HwTestTop(platform, cfg, core_path)
    gateware = os.path.join(out, "gateware")
    platform.build(top, build_dir=gateware, build_name=TOP, run=args.build)

    if args.build:
        sof = os.path.join(gateware, f"{TOP}.sof")
        print(f"[hwtest] bitstream: {os.path.relpath(sof)}")
        print(f'[hwtest] program on the host: quartus_pgm -m jtag -o "p;{TOP}.sof"')
    else:
        print(f"[hwtest] Quartus project: {os.path.relpath(gateware)}/{TOP}.qsf (not built)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
