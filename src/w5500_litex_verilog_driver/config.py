"""Build-time configuration of the W5500 UDP IP core.

Everything here is baked into the generated Verilog: there are no runtime configuration ports.
Add a new preset to ``PRESETS`` (or build a ``CoreConfig`` in your own script) to target another
board / network.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from dataclasses import dataclass, field

# W5500 has 16 KB TX + 16 KB RX of socket buffer memory shared by 8 sockets; per-socket sizes
# (Sn_TXBUF_SIZE / Sn_RXBUF_SIZE) are restricted to these values (KB).
SOCKET_BUF_SIZES_KB = (0, 1, 2, 4, 8, 16)
DATA_WIDTHS         = (8, 32)
SPI_FREQ_MAX        = 80_000_000  # W5500 datasheet: SCLK up to 80 MHz (33.3 MHz guaranteed).


def _ip(value: str) -> int:
    return int(ipaddress.IPv4Address(value))


def _mac(value: str) -> int:
    parts = value.replace("-", ":").split(":")
    if len(parts) != 6 or not all(len(p) == 2 for p in parts):
        raise ValueError(f"invalid MAC address {value!r} (expected xx:xx:xx:xx:xx:xx)")
    return int("".join(parts), 16)


@dataclass(frozen=True)
class CoreConfig:
    # Clocking.
    clk_freq: int = 50_000_000  # Hz, the core's `clk`.
    spi_freq: int = 12_500_000  # Hz, target SCLK (actual = clk_freq / even divider).

    # User data interface.
    data_width:  int = 32      # tx_data / rx_data width in bits (8 or 32).
    max_payload: int = 1472   # Max UDP payload bytes per datagram (1500 MTU - 20 IP - 8 UDP).
    # Fixed-size TX framing (soc_*): a chunk = tx_chunk_bytes / 4 handshakes of 32 bits, written
    # into the W5500 as one SPI burst; a datagram (UDP packet) = tx_chunks_per_datagram chunks.
    tx_chunk_bytes:         int = 40
    tx_chunks_per_datagram: int = 1

    # Local network identity.
    mac:     str = "02:00:00:00:00:01"  # Locally administered unicast.
    ip:      str = "192.168.1.10"
    subnet:  str = "255.255.255.0"
    gateway: str = "192.168.1.1"

    # UDP endpoint.
    local_port:  int = 5000
    remote_ip:   str = "192.168.1.100"
    remote_port: int = 5000

    # W5500 resources.
    socket:    int = 0   # Hardware socket number (0..7).
    tx_buf_kb: int = 2   # Socket TX buffer (W5500 reset default: 2 KB).
    rx_buf_kb: int = 2   # Socket RX buffer.

    # Timing (W5500 datasheet: RSTn low >= 500 us; PLL lock <= 1 ms after reset release).
    reset_hold_us: int = 500
    init_wait_us:  int = 1000

    # Timeouts that end in a sticky status_error (soc_v1). The UDP ARP timeout of the W5500 with
    # reset RTR/RCR is 1.8 s, so send_timeout_ms must be larger to see Sn_IR.TIMEOUT first.
    link_timeout_ms: int = 3000  # PHYCFGR.LNK after reset (auto-negotiation takes ~1-3 s).
    cmd_timeout_us:  int = 1000  # Sn_CR accepted / Sn_SR state change / Sn_TX_FSR free space.
    send_timeout_ms: int = 3000  # SEND -> Sn_IR.SENDOK or Sn_IR.TIMEOUT.

    # Free-form notes (shown in the generated file header), not used by the hardware.
    notes: tuple[str, ...] = field(default=())

    def __post_init__(self):
        errors = []

        def check(cond, msg):
            if not cond:
                errors.append(msg)

        check(self.clk_freq > 0, "clk_freq must be > 0")
        check(0 < self.spi_freq <= SPI_FREQ_MAX, f"spi_freq must be in (0, {SPI_FREQ_MAX}]")
        check(self.spi_freq * 2 <= self.clk_freq, "spi_freq must be <= clk_freq / 2")
        check(self.data_width in DATA_WIDTHS, f"data_width must be one of {DATA_WIDTHS}")
        check(0 < self.max_payload <= 1472, "max_payload must be in 1..1472")
        check(self.socket in range(8), "socket must be in 0..7")
        for name in ("tx_buf_kb", "rx_buf_kb"):
            check(getattr(self, name) in SOCKET_BUF_SIZES_KB[1:],
                  f"{name} must be one of {SOCKET_BUF_SIZES_KB[1:]}")
        check(self.max_payload <= self.tx_buf_kb * 1024, "max_payload must fit in tx_buf_kb")
        for name in ("local_port", "remote_port"):
            check(0 < getattr(self, name) < 65536, f"{name} must be in 1..65535")
        for name in ("ip", "subnet", "gateway", "remote_ip"):
            try:
                _ip(getattr(self, name))
            except ValueError as e:
                errors.append(f"{name}: {e}")
        try:
            mac = _mac(self.mac)
            check(not (mac >> 40) & 1, "mac must be unicast (bit 0 of first octet clear)")
        except ValueError as e:
            errors.append(f"mac: {e}")
        check(self.reset_hold_us >= 500, "reset_hold_us must be >= 500 (W5500 RSTn min)")
        check(self.init_wait_us >= 0, "init_wait_us must be >= 0")
        check(0 < self.tx_chunk_bytes <= self.max_payload and self.tx_chunk_bytes % 4 == 0,
              "tx_chunk_bytes must be a multiple of 4 in 4..max_payload")
        check(self.tx_chunks_per_datagram >= 1, "tx_chunks_per_datagram must be >= 1")
        check(self.tx_datagram_bytes <= self.max_payload,
              "tx_chunk_bytes * tx_chunks_per_datagram must be <= max_payload")
        for name in ("link_timeout_ms", "cmd_timeout_us", "send_timeout_ms"):
            check(getattr(self, name) > 0, f"{name} must be > 0")
        # Firmware busy-waits on a 32-bit cycle counter.
        longest_us = max(self.reset_hold_us, self.init_wait_us, self.cmd_timeout_us,
                         1000 * self.link_timeout_ms, 1000 * self.send_timeout_ms)
        check(self.clk_freq * longest_us // 1_000_000 < 2**31,
              "waits/timeouts must be < 2**31 clk cycles")

        if errors:
            raise ValueError("invalid CoreConfig:\n  " + "\n  ".join(errors))

    # Derived values ------------------------------------------------------------------------------

    @property
    def mac_int(self) -> int:
        return _mac(self.mac)

    @property
    def ip_int(self) -> int:
        return _ip(self.ip)

    @property
    def subnet_int(self) -> int:
        return _ip(self.subnet)

    @property
    def gateway_int(self) -> int:
        return _ip(self.gateway)

    @property
    def remote_ip_int(self) -> int:
        return _ip(self.remote_ip)

    @property
    def tx_datagram_bytes(self) -> int:
        return self.tx_chunk_bytes * self.tx_chunks_per_datagram

    @property
    def spi_div(self) -> int:
        """clk cycles per SCLK half-period (>= 1); actual SCLK = clk_freq / (2 * spi_div)."""
        return max(1, -(-self.clk_freq // (2 * self.spi_freq)))  # ceil: never exceed spi_freq.

    def cycles(self, us: int) -> int:
        """Number of clk cycles covering ``us`` microseconds (rounded up)."""
        return -(-self.clk_freq * us // 1_000_000)

    def replace(self, **changes) -> CoreConfig:
        return dataclasses.replace(self, **changes)

    def summary(self) -> list[str]:
        """Human-readable ``key = value`` lines (used in the generated file header)."""
        return [f"{f.name:<22} = {getattr(self, f.name)}" for f in dataclasses.fields(self)
                if f.name != "notes"] + [f"note: {n}" for n in self.notes]


PRESETS: dict[str, CoreConfig] = {
    "default": CoreConfig(),
    "default32": CoreConfig(data_width=32),
    # DE10-Lite (50 MHz MAX10 oscillator) + W5500 Lite.
    "de10lite": CoreConfig(clk_freq=50_000_000, spi_freq=12_500_000,
                           notes=("Terasic DE10-Lite + WIZnet W5500 Lite",)),
    # Streaming target of soc_v2: 25 MHz SCLK, 400-byte datagrams of 10 x 40-byte chunks.
    "stream": CoreConfig(spi_freq=25_000_000, tx_chunks_per_datagram=10,
                         notes=("25 MHz SCLK, 10 chunks of 40 B per datagram",)),
}
