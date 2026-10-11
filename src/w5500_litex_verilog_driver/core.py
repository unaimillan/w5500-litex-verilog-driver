"""Top level of the W5500 UDP IP core: the external port contract + implementation selection.

The ports are declared here once, from ``PORT_GROUPS``, and are identical for every
implementation in ``impl/``; an implementation only receives the pads and drives/reads them.
That keeps the generated module a drop-in replacement no matter which approach is selected.
"""

from __future__ import annotations

from types import SimpleNamespace

from litex.build.generic_platform import Pins, Subsignal
from litex.gen import LiteXModule
from migen import ClockDomain

from .config import CoreConfig

DW = "DW"  # Placeholder width: replaced by CoreConfig.data_width.

# Port contract (DRAFT, see docs/architecture.md). Each group becomes `<group>_<field>` ports.
#   group: [(field, direction, width), ...]   direction is from the core's point of view.
PORT_GROUPS: dict[str, list[tuple[str, str, int | str]]] = {
    # Clock / synchronous active-high reset (synchronize it in the parent design).
    "": [("clk", "in", 1), ("rst", "in", 1)],
    # W5500 module pins.
    "w5500": [
        ("spi_sclk", "out", 1),
        ("spi_cs_n", "out", 1),
        ("spi_mosi", "out", 1),
        ("spi_miso", "in",  1),
        ("rst_n",    "out", 1),
        ("int_n",    "in",  1),
    ],
    # User -> remote: one datagram = a burst of beats terminated by `last`.
    "tx": [("valid", "in", 1), ("ready", "out", 1), ("data", "in", DW), ("last", "in", 1)],
    # Remote -> user: one datagram = a burst of beats terminated by `last`.
    "rx": [("valid", "out", 1), ("ready", "in", 1), ("data", "out", DW), ("last", "out", 1)],
    # Core state.
    "status": [("ready", "out", 1), ("error", "out", 1)],
}


def _width(width: int | str, cfg: CoreConfig) -> int:
    return cfg.data_width if width == DW else int(width)


def ports(cfg: CoreConfig) -> list[tuple[str, str, int]]:
    """Flat ``(verilog_port_name, direction, width)`` list of the generated top module."""
    return [(f"{group}_{name}" if group else name, direction, _width(width, cfg))
            for group, fields in PORT_GROUPS.items()
            for name, direction, width in fields]


def io_list(cfg: CoreConfig) -> list:
    """LiteX board-style io list for ``VirtualPlatform`` (unplaced pins)."""
    io = []
    for group, fields in PORT_GROUPS.items():
        if group:
            io.append((group, 0, *[Subsignal(n, Pins(_width(w, cfg))) for n, _, w in fields]))
        else:
            io += [(n, 0, Pins(_width(w, cfg))) for n, _, w in fields]
    return io


class W5500UdpCore(LiteXModule):
    """Top-level Migen module: clocking + pads, delegating all logic to ``impl``."""

    def __init__(self, platform, cfg: CoreConfig, impl: str):
        from .impl import IMPLEMENTATIONS  # Late import: impl modules import this module.

        if impl not in IMPLEMENTATIONS:
            raise ValueError(f"unknown impl {impl!r}, choose from {sorted(IMPLEMENTATIONS)}")
        self.cfg = cfg

        # Single `sys` clock domain driven straight from the pins (no PLL, no reset synchronizer:
        # the parent design owns clocking and provides `rst` synchronous to `clk`).
        self.cd_sys = ClockDomain()
        self.comb += [
            self.cd_sys.clk.eq(platform.request("clk")),
            self.cd_sys.rst.eq(platform.request("rst")),
        ]

        pads = SimpleNamespace(**{g: platform.request(g) for g in PORT_GROUPS if g})
        self.impl = IMPLEMENTATIONS[impl](platform, cfg, pads)
