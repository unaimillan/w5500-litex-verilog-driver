"""soc_v2: TX-only LiteX SoC like soc_v1, but the CPU stays out of the data path.

    tx stream --> FIFO (>= 64 words) --> TxChunkStreamer --+
                                                           +--mux--> LiteSPI SDR PHY --> W5500 SPI
    FemtoRV (quark_bicycle) --CSR--> LiteSPIMaster --------+
    FemtoRV --CSR--> W5500Ctrl (W5500 RSTn, status)

Terms: a *chunk* is ``tx_chunk_bytes`` (``tx_chunk_bytes / 4`` words), a *datagram* (one UDP
packet) is ``tx_chunks_per_datagram`` chunks.

The firmware (``firmware/soc_v2/``) brings the W5500 up exactly like soc_v1. Per datagram it checks
Sn_TX_FSR once, then for every chunk writes the socket TX buffer offset to ``TxChunkStreamer``,
which waits for a whole chunk in the FIFO and writes it with one VDM burst (CS, 24-bit header,
chunk words straight from the FIFO, CS release) while the CPU waits for ``busy`` to clear. After
the last chunk the CPU advances Sn_TX_WR by the datagram size and SENDs. SCLK = clk / (2 *
spi_div), i.e. 25 MHz with the ``stream`` preset at 50 MHz. See docs/architecture.md.

Limits: TX only (rx outputs idle), fixed datagram size, ``tx_last`` ignored, data_width 32.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from litespi.common import spi_core2phy_layout, spi_phy2core_layout
from litespi.core.master import LiteSPIMaster
from litespi.phy.generic_sdr import LiteSPISDRPHYCore
from litex.gen import LiteXModule
from litex.soc.integration.soc_core import SoCCore
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStatus, CSRStorage
from migen import FSM, If, Mux, NextState, NextValue, Signal

from .. import w5500 as W
from .soc_v1 import ROM_SIZE, SRAM_SIZE, FemtoRVVendored, W5500Ctrl, build_firmware
from .soc_v1 import supports as supports_v1

FIRMWARE_DIR = Path(__file__).resolve().parents[1] / "firmware" / "soc_v2"
FIFO_MIN_WORDS = 64


def supports(cfg) -> str | None:
    """Reason why ``cfg`` cannot be built with this implementation, or None."""
    return supports_v1(cfg.replace(tx_chunks_per_datagram=1))  # Same needs, but any batching.


def fifo_depth(chunk_words: int) -> int:
    """Input FIFO depth: power of two, >= FIFO_MIN_WORDS and >= two chunks."""
    return 1 << (max(FIFO_MIN_WORDS, 2 * chunk_words) - 1).bit_length()


# Peripherals --------------------------------------------------------------------------------------

class TxChunkStreamer(LiteXModule):
    """Writes one chunk from the input FIFO into the W5500 socket TX buffer, without the CPU.

    Writing ``start`` (the TX buffer offset) arms it; it waits until the FIFO holds a whole chunk
    (so SCLK never stalls inside the burst), then owns the PHY for one VDM write frame: CS low,
    24-bit header (offset, socket TX block, write), ``words`` 32-bit transfers popped from the
    FIFO, CS high once the PHY has returned every transfer. ``busy`` is high from the ``start``
    write until then; the firmware must not use LiteSPIMaster (nor its CS) meanwhile.

    ``source``/``sink`` replace the master's PHY streams while ``active``; ``cs`` is OR-ed with the
    master's.
    """

    def __init__(self, fifo, words, socket):
        self.start = CSRStorage(16, description="Write: stream one chunk to this TX buffer offset.")
        self.busy  = CSRStatus(1,  description="Chunk armed or being written (SPI master busy).")

        self.source = source = stream.Endpoint(spi_core2phy_layout)  # To the PHY.
        self.sink   = sink   = stream.Endpoint(spi_phy2core_layout)  # From the PHY.
        self.cs     = cs     = Signal()
        self.active = active = Signal()

        # # #

        header  = Signal(24)
        to_send = Signal(max=words + 1)  # Data transfers still to hand to the PHY.
        pending = Signal(max=words + 2)  # PHY transfers (header + data) not yet returned.

        # Stream signals are driven outside the FSM, one assignment each: a block that both reads
        # a ready and drives a valid shows up as a combinational loop (Verilator UNOPTFLAT).
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(self.start.re,
                NextValue(header, (self.start.storage << 8) |
                                  W.control_byte(W.bsb_socket_tx(socket), True)),
                NextState("WAIT-CHUNK"),
            ),
        )
        fsm.act("WAIT-CHUNK",
            If(fifo.level >= words,
                NextValue(to_send, words),
                NextValue(pending, words + 1),
                NextState("HEADER"),
            ),
        )
        fsm.act("HEADER",
            If(source.ready, NextState("DATA")),
        )
        fsm.act("DATA",
            If(source.valid & source.ready,
                NextValue(to_send, to_send - 1),
                If(to_send == 1, NextState("DRAIN")),
            ),
        )
        fsm.act("DRAIN",
            If((pending == 0) | ((pending == 1) & sink.valid), NextState("IDLE")),
        )
        self.sync += If(sink.valid, pending.eq(pending - 1))

        in_header, in_data = fsm.ongoing("HEADER"), fsm.ongoing("DATA")
        self.comb += [
            active.eq(~fsm.ongoing("IDLE")),
            cs.eq(in_header | in_data | fsm.ongoing("DRAIN")),
            self.busy.status.eq(active),
            source.valid.eq(in_header | (in_data & fifo.source.valid)),
            source.data.eq(Mux(in_header, header, fifo.source.data)),
            source.len.eq(Mux(in_header, 24, 32)),
            source.width.eq(1),
            source.mask.eq(1),
            source.clk_div.eq(0),  # PHY default divisor.
            fifo.source.ready.eq(in_data & source.ready),
            sink.ready.eq(1),      # Read data (MISO) is dropped.
        ]


# SoC ----------------------------------------------------------------------------------------------

class SocV2(SoCCore):
    supports     = staticmethod(supports)
    capabilities = ("tx",)

    def __init__(self, platform, cfg, pads):
        if (reason := supports(cfg)) is not None:
            raise ValueError(reason)

        SoCCore.__init__(self, platform, cfg.clk_freq,
            cpu_type             = FemtoRVVendored.name,
            cpu_variant          = "quark_bicycle",
            integrated_rom_size  = ROM_SIZE,
            integrated_sram_size = SRAM_SIZE,
            with_uart            = False,
            with_timer           = False,  # Firmware times with rdcycle.
            with_ctrl            = False,
        )

        # W5500 reset / status.
        self.w5500 = W5500Ctrl(pads.w5500, pads.status)

        # Input FIFO (no CPU access; tx_ready only once the socket is open).
        words = cfg.tx_chunk_bytes // 4
        self.txfifo = fifo = stream.SyncFIFO([("data", 32)], fifo_depth(words), buffered=True)
        self.comb += [
            fifo.sink.valid.eq(pads.tx.valid & pads.status.ready),
            fifo.sink.data.eq(pads.tx.data),
            pads.tx.ready.eq(fifo.sink.ready & pads.status.ready),
        ]

        # SPI: LiteSPI generic SDR PHY (SCLK = clk / clk_div, mode 0), shared by the CSR master
        # (control plane) and the chunk streamer (data plane).
        spi_pads = SimpleNamespace(clk=pads.w5500.spi_sclk, cs_n=pads.w5500.spi_cs_n,
                                   mosi=pads.w5500.spi_mosi, miso=pads.w5500.spi_miso)
        self.spi_phy = phy = LiteSPISDRPHYCore(spi_pads, flash=None, device="generic",
                                               clock_domain="sys", default_divisor=2 * cfg.spi_div)
        self.spi = master = LiteSPIMaster(tx_fifo_depth=2, rx_fifo_depth=2)
        self.txstream = streamer = TxChunkStreamer(fifo, words, cfg.socket)
        # PHY mux: per-signal assignments (see TxChunkStreamer about UNOPTFLAT).
        sel = streamer.active
        self.comb += [phy.sink.valid.eq(Mux(sel, streamer.source.valid, master.source.valid))]
        self.comb += [getattr(phy.sink, f).eq(Mux(sel, getattr(streamer.source, f),
                                                   getattr(master.source, f)))
                      for f, _ in spi_core2phy_layout]
        self.comb += [
            streamer.source.ready.eq(sel & phy.sink.ready),
            master.source.ready.eq(~sel & phy.sink.ready),
            streamer.sink.valid.eq(sel & phy.source.valid),
            master.sink.valid.eq(~sel & phy.source.valid),
            streamer.sink.data.eq(phy.source.data),
            master.sink.data.eq(phy.source.data),
            phy.source.ready.eq(Mux(sel, streamer.sink.ready, master.sink.ready)),
            phy.cs.eq(master.cs | streamer.cs),
        ]

        # No receive path.
        self.comb += [pads.rx.valid.eq(0), pads.rx.data.eq(0), pads.rx.last.eq(0)]

        # Compile the firmware against this SoC and bake it into the ROM.
        self.finalize()
        self.init_rom("rom", build_firmware(self, cfg, FIRMWARE_DIR))
