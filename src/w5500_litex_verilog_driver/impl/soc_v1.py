"""soc_v1: TX-only core as a small LiteX SoC (FemtoRV + LiteSPI) running baked-in firmware.

    tx stream --> TxFramePort (FIFO, tx_chunk_bytes/4 words) --wishbone--+
                                                                          FemtoRV (quark) + ROM/SRAM
    W5500 SPI <-- LiteSPI PHY <-- LiteSPIMaster (CSRs) <-------------CSR--+
    W5500 RSTn, status <------------------- W5500Ctrl (CSRs) <--------CSR--+

The firmware (``firmware/soc_v1/``) is compiled at generation time with the CoreConfig values and
the CSR map of this SoC, then baked into the ROM. It resets and configures the W5500, opens the
UDP socket and raises ``status_ready``; then, per chunk, it stalls on ``TxFramePort.WAIT`` until
the FIFO holds a whole chunk, copies it with one VDM burst into the socket TX buffer and SENDs it
as one datagram.
Any failure sets the sticky ``status_error`` (cleared by ``rst`` only). See docs/architecture.md.

Limits: TX only (rx outputs idle), one chunk per datagram, ``tx_last`` ignored, data_width 32.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

from litespi.core.master import LiteSPIMaster
from litespi.phy.generic_sdr import LiteSPISDRPHYCore
from litex.gen import LiteXModule
from litex.soc.cores import cpu
from litex.soc.cores.cpu.femtorv.core import FemtoRV
from litex.soc.integration import export
from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.soc_core import SoCCore
from litex.soc.interconnect import stream, wishbone
from litex.soc.interconnect.csr import CSR, CSRStatus, CSRStorage
from migen import If, Mux, Signal

from .. import w5500 as W

FIRMWARE_DIR = Path(__file__).resolve().parents[1] / "firmware" / "soc_v1"
VENDOR_DIR   = Path(__file__).resolve().parents[3] / "vendor"
GCC_PREFIX   = "riscv64-unknown-elf-"

ROM_SIZE  = 0x1000
SRAM_SIZE = 0x400
FRAME_BASE = 0x9000_0000  # TxFramePort (FemtoRV io region starts at 0x8000_0000).


def supports(cfg) -> str | None:
    """Reason why ``cfg`` cannot be built with this implementation, or None."""
    if cfg.data_width != 32:
        return "soc_v1 needs data_width = 32"
    if cfg.tx_chunks_per_datagram != 1:
        return "soc_v1 sends one chunk per datagram (tx_chunks_per_datagram = 1)"
    if shutil.which(GCC_PREFIX + "gcc") is None:
        return f"{GCC_PREFIX}gcc not found (apt install gcc-riscv64-unknown-elf)"
    return None


# CPU: LiteX FemtoRV wrapper, but with the vendored Verilog instead of a wget at build time --------

class FemtoRVVendored(FemtoRV):
    """LiteX ``FemtoRV`` with two fixes, limited to the vendored variants (all RV32I).

    - Verilog comes from ``vendor/`` (upstream ``add_sources`` wgets into the cwd).
    - Reads get ``sel = 0b1111``. The upstream wrapper drives ``sel`` from the write mask, which
      is 0 on reads, and ``Wishbone2CSR`` only raises the CSR read strobe (``we``) when
      ``sel != 0``, so read-to-pop CSRs such as LiteSPIMaster ``rxtx`` would never pop.
    """

    name     = "femtorv_vendored"
    sources:  ClassVar[dict[str, str]] = {"quark":         "femtorv32_quark",
                                          "quark_bicycle": "femtorv32_quark_bicycle"}
    variants: ClassVar[list[str]]      = list(sources)

    def __init__(self, platform, variant="standard"):
        FemtoRV.__init__(self, platform, variant)
        cpu_bus = self.idbus
        self.idbus = bus = wishbone.Interface(data_width=32, address_width=32,
                                              addressing="byte")
        self.comb += [
            cpu_bus.connect(bus, omit={"sel"}),
            bus.sel.eq(Mux(cpu_bus.we, cpu_bus.sel, 0b1111)),
        ]
        self.periph_buses = [bus]

    @property
    def gcc_flags(self):
        return "-march=rv32i2p0 -mabi=ilp32 -D__femtorv__"

    @classmethod
    def add_sources(cls, platform, variant):
        platform.add_source(str(VENDOR_DIR / "femtorv" / f"{cls.sources[variant]}.v"))


cpu.CPUS.setdefault(FemtoRVVendored.name, FemtoRVVendored)


# Peripherals --------------------------------------------------------------------------------------

class TxFramePort(LiteXModule):
    """User tx stream -> FIFO of two frames (2 x ``words`` 32-bit words), read by the CPU over
    Wishbone. The second frame keeps tx_ready high while the CPU is busy before it pops the first
    one (Sn_TX_FSR / Sn_TX_WR reads, ~30 us), so a real-time source is not stalled.

    Word 0 (WAIT): read stalls (ack withheld) until the FIFO holds >= ``words`` words; returns 0.
    Word 1 (DATA): read pops one word; stalls while the FIFO is empty. Writes are ignored.
    """

    def __init__(self, tx, words, enable):
        self.bus  = bus  = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.fifo = fifo = stream.SyncFIFO([("data", 32)], 2 * words, buffered=False)

        self.comb += [
            fifo.sink.valid.eq(tx.valid & enable),
            fifo.sink.data.eq(tx.data),
            tx.ready.eq(fifo.sink.ready & enable),
        ]

        access   = bus.cyc & bus.stb & ~bus.ack
        is_data  = bus.adr[0]
        pop      = access & ~bus.we & is_data & fifo.source.valid
        complete = fifo.level >= words
        self.comb += fifo.source.ready.eq(pop)
        self.sync += [
            bus.ack.eq(0),
            If(access,
                If(bus.we | pop | (~is_data & complete), bus.ack.eq(1)),
                bus.dat_r.eq(Mux(is_data, fifo.source.data, 0)),
            ),
        ]


class W5500Ctrl(LiteXModule):
    """W5500 reset pin and the core status outputs; ``error`` is set-only (sticky until rst)."""

    def __init__(self, w5500_pads, status_pads):
        self.rst_n = CSRStorage(1, reset=0, description="W5500 RSTn (held low after rst).")
        self.ready = CSRStorage(1, reset=0, description="Drives status_ready (masked by error).")
        self.error = CSR(1, name="error")  # Write 1: set the sticky error flag.
        self.int_n = CSRStatus(1, description="W5500 INTn pin (not used by the v1 firmware).")

        self.failed = failed = Signal()
        self.sync += If(self.error.re & self.error.r, failed.eq(1))
        self.comb += [
            w5500_pads.rst_n.eq(self.rst_n.storage),
            self.int_n.status.eq(w5500_pads.int_n),
            status_pads.ready.eq(self.ready.storage & ~failed),
            status_pads.error.eq(failed),
        ]


# SoC ----------------------------------------------------------------------------------------------

class SocV1(SoCCore):
    supports     = staticmethod(supports)
    capabilities = ("tx",)

    def __init__(self, platform, cfg, pads):
        if (reason := supports(cfg)) is not None:
            raise ValueError(reason)

        SoCCore.__init__(self, platform, cfg.clk_freq,
            cpu_type             = FemtoRVVendored.name,
            cpu_variant          = "quark",
            bus_timeout          = None,  # TxFramePort stalls the bus on purpose.
            integrated_rom_size  = ROM_SIZE,
            integrated_sram_size = SRAM_SIZE,
            with_uart            = False,
            with_timer           = False,  # Firmware times with rdcycle.
            with_ctrl            = False,
        )

        # W5500 reset / status.
        self.w5500 = W5500Ctrl(pads.w5500, pads.status)

        # SPI: LiteSPI generic SDR PHY (SCLK = clk / clk_div, mode 0) + CSR master.
        spi_pads = SimpleNamespace(clk=pads.w5500.spi_sclk, cs_n=pads.w5500.spi_cs_n,
                                   mosi=pads.w5500.spi_mosi, miso=pads.w5500.spi_miso)
        self.spi_phy = LiteSPISDRPHYCore(spi_pads, flash=None, device="generic",
                                         clock_domain="sys", default_divisor=2 * cfg.spi_div)
        self.spi = LiteSPIMaster(tx_fifo_depth=2, rx_fifo_depth=2)
        self.comb += [
            self.spi.source.connect(self.spi_phy.sink),
            self.spi_phy.source.connect(self.spi.sink),
            self.spi_phy.cs.eq(self.spi.cs),
        ]

        # Frame input.
        self.txframe = TxFramePort(pads.tx, cfg.tx_chunk_bytes // 4, pads.status.ready)
        self.bus.add_slave("txframe", self.txframe.bus,
                           SoCRegion(origin=FRAME_BASE, size=0x10, cached=False))

        # No receive path in v1.
        self.comb += [pads.rx.valid.eq(0), pads.rx.data.eq(0), pads.rx.last.eq(0)]

        # Compile the firmware against this SoC and bake it into the ROM.
        self.finalize()
        self.init_rom("rom", build_firmware(self, cfg))


# Firmware -----------------------------------------------------------------------------------------

def config_header(cfg, extra=None) -> str:
    """C header with the CoreConfig values (as bytes / clk cycles), the W5500 constants and the
    implementation-specific ``extra`` defines."""
    n = cfg.socket

    def be(value, nbytes):
        return ", ".join(f"0x{b:02x}" for b in value.to_bytes(nbytes, "big"))

    defines = {
        "FRAME_BASE":          f"0x{FRAME_BASE:08x}u",
        "CHUNK_WORDS":         cfg.tx_chunk_bytes // 4,
        "CHUNK_BYTES":         cfg.tx_chunk_bytes,
        "CHUNKS":              cfg.tx_chunks_per_datagram,
        "DGRAM_BYTES":         cfg.tx_datagram_bytes,
        "RESET_HOLD_CYCLES":   cfg.cycles(cfg.reset_hold_us),
        "INIT_WAIT_CYCLES":    cfg.cycles(cfg.init_wait_us),
        "LINK_TIMEOUT_CYCLES": cfg.cycles(1000 * cfg.link_timeout_ms),
        "CMD_TIMEOUT_CYCLES":  cfg.cycles(cfg.cmd_timeout_us),
        "SEND_TIMEOUT_CYCLES": cfg.cycles(1000 * cfg.send_timeout_ms),
        "W_BSB_COMMON":        W.BSB_COMMON,
        "W_BSB_SREG":          W.bsb_socket_reg(n),
        "W_BSB_STX":           W.bsb_socket_tx(n),
        "W_CTRL_WRITE":        W.control_byte(0, True),   # RWB | OM (BSB is OR-ed in by C).
        "W_CTRL_READ":         W.control_byte(0, False),
        "TX_BUF_KB":           cfg.tx_buf_kb,
        "RX_BUF_KB":           cfg.rx_buf_kb,
        **(extra or {}),
    }
    regs = ("GAR", "SUBR", "SHAR", "SIPR", "VERSIONR", "VERSION", "PHYCFGR", "PHYCFGR_LNK",
            "Sn_MR", "Sn_CR", "Sn_IR", "Sn_SR", "Sn_PORT", "Sn_DIPR", "Sn_DPORT",
            "Sn_RXBUF_SIZE", "Sn_TXBUF_SIZE", "Sn_TX_FSR", "Sn_TX_WR",
            "Sn_MR_UDP", "Sn_CR_OPEN", "Sn_CR_SEND", "Sn_IR_TIMEOUT", "Sn_IR_SENDOK", "SOCK_UDP")
    for r in regs:
        defines[f"W_{r}"] = f"0x{getattr(W, r):02x}"

    lines = ["// Generated by impl/soc_*.py from CoreConfig - do not edit.", "#pragma once", ""]
    lines += [f"#define {k:<20} {v}" for k, v in defines.items()]
    lines += [
        "",
        "// GAR, SUBR, SHAR, SIPR: contiguous common registers 0x0001..0x0012.",
        f"static const unsigned char NET_CFG[18] = {{{be(cfg.gateway_int, 4)}, "
        f"{be(cfg.subnet_int, 4)}, {be(cfg.mac_int, 6)}, {be(cfg.ip_int, 4)}}};",
        "// Sn_PORT (2), then Sn_DIPR (4) + Sn_DPORT (2).",
        f"static const unsigned char SOCK_PORT[2] = {{{be(cfg.local_port, 2)}}};",
        f"static const unsigned char SOCK_DEST[6] = {{{be(cfg.remote_ip_int, 4)}, "
        f"{be(cfg.remote_port, 2)}}};",
        "",
    ]
    return "\n".join(lines)


def build_firmware(soc, cfg, fw_dir=FIRMWARE_DIR, extra=None) -> list[int]:
    """Compile ``fw_dir`` (crt0.S, main.c, linker.ld) for ``soc``; returns the ROM contents as
    32-bit words. ``extra``: additional config.h defines."""
    gcc = GCC_PREFIX + "gcc"
    with tempfile.TemporaryDirectory(prefix="w5500_fw_") as tmp:
        gen = Path(tmp) / "generated"
        gen.mkdir()
        (gen / "csr.h").write_text(export.get_csr_header(soc.csr_regions, soc.constants,
                                                         with_access_functions=False))
        (gen / "config.h").write_text(config_header(cfg, extra))
        rom, sram = soc.mem_regions["rom"], soc.mem_regions["sram"]
        (Path(tmp) / "regions.ld").write_text(
            "MEMORY {\n"
            f"    rom  (rx)  : ORIGIN = 0x{rom.origin:08x}, LENGTH = 0x{rom.size:x}\n"
            f"    sram (rwx) : ORIGIN = 0x{sram.origin:08x}, LENGTH = 0x{sram.size:x}\n"
            "}\n")
        elf, binf = Path(tmp) / "fw.elf", Path(tmp) / "fw.bin"
        cmd = [gcc, *soc.cpu.gcc_flags.split(), "-Os", "-g0", "-ffreestanding", "-nostdlib",
               "-fno-builtin", "-Wall", "-Wextra", "-Werror", "-I", tmp,
               "-L", tmp, "-T", str(fw_dir / "linker.ld"),
               "-o", str(elf), str(fw_dir / "crt0.S"), str(fw_dir / "main.c"),
               "-lgcc"]
        subprocess.run(cmd, check=True)
        subprocess.run([GCC_PREFIX + "objcopy", "-O", "binary", str(elf), str(binf)], check=True)
        data = binf.read_bytes()
    data += bytes(-len(data) % 4)
    return [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data), 4)]
