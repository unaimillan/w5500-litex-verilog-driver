"""Pure-Migen placeholder: holds the W5500 in reset and every interface idle."""

from litex.gen import LiteXModule


class StubMigen(LiteXModule):
    def __init__(self, platform, cfg, pads):
        self.comb += [
            pads.w5500.spi_sclk.eq(0),
            pads.w5500.spi_cs_n.eq(1),
            pads.w5500.spi_mosi.eq(0),
            pads.w5500.rst_n.eq(0),
            pads.tx.ready.eq(0),
            pads.rx.valid.eq(0),
            pads.rx.data.eq(0),
            pads.rx.last.eq(0),
            pads.status.ready.eq(0),
            pads.status.error.eq(0),
        ]
