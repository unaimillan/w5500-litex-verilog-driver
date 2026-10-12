"""Behavioral model of a WIZnet W5500 for cocotb testbenches.

Two layers:
  * ``W5500Model``   - pure Python, byte level: register/buffer memory, SPI frame decoding and a
                       minimal UDP socket behaviour (OPEN/CLOSE/SEND/RECV, TX/RX ring buffers).
                       Unit-testable without a simulator.
  * ``W5500SpiSlave`` - cocotb adapter driving/sampling the SPI pins (mode 0) of the DUT.

Not modelled (yet): ARP/timeouts (only an all-fail switch), interrupts pin (INTn), PHY link
changes, fixed-length (FDM) frames, TCP/MACRAW, MR software reset side effects beyond register
defaults.

Assumed where the datasheet is silent (see "To clarify" in docs/w5500-notes.md): VDM bursts wrap
inside a socket buffer, the 8-byte UDP RX header format, and MOSI content is ignored on reads.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from w5500_litex_verilog_driver import w5500 as W


@dataclass
class Frame:
    bsb: int
    addr: int
    write: bool
    data: bytearray = field(default_factory=bytearray)


@dataclass
class Datagram:
    ip: str
    port: int
    payload: bytes


def _ip_str(b: bytes) -> str:
    return ".".join(str(x) for x in b)


class W5500Model:
    BLOCK_SIZE = 0x10000

    def __init__(self, version: int = W.VERSION, link_up: bool = True, arp_fail: bool = False):
        self.frames: list[Frame] = []      # Completed SPI frames (for assertions).
        self.sent: list[Datagram] = []     # Datagrams transmitted by SEND.
        # Fault injection: VERSIONR value, PHYCFGR.LNK, SEND ends in Sn_IR.TIMEOUT (ARP failed).
        self.version, self.link_up, self.arp_fail = version, link_up, arp_fail
        self.reset()

    # Memory ------------------------------------------------------------------------------------

    def reset(self):
        self.mem: dict[int, bytearray] = defaultdict(lambda: bytearray(self.BLOCK_SIZE))
        common = self.mem[W.BSB_COMMON]
        common[W.RTR:W.RTR + 2] = (2000).to_bytes(2, "big")
        common[W.RCR] = 8
        common[W.PHYCFGR] = 0xBF if self.link_up else 0xBE  # Auto-neg., 100M full duplex.
        common[W.VERSIONR] = self.version
        for n in range(8):
            regs = self.mem[W.bsb_socket_reg(n)]
            regs[W.Sn_TXBUF_SIZE] = 2
            regs[W.Sn_RXBUF_SIZE] = 2
            regs[W.Sn_TTL] = 0x80
            self._set16(n, W.Sn_TX_FSR, 2048)
        self._ir: dict[int, int] = defaultdict(int)  # Sn_IR state (memory holds last write).
        self._frame: Frame | None = None
        self._hdr = bytearray()

    def read(self, bsb: int, addr: int, n: int = 1) -> bytes:
        return bytes(self.mem[bsb][(addr + i) & 0xFFFF] for i in range(n))

    def write(self, bsb: int, addr: int, data: bytes):
        for i, b in enumerate(data):
            self.mem[bsb][(addr + i) & 0xFFFF] = b

    def _get16(self, n: int, reg: int) -> int:
        return int.from_bytes(self.read(W.bsb_socket_reg(n), reg, 2), "big")

    def _set16(self, n: int, reg: int, value: int):
        self.write(W.bsb_socket_reg(n), reg, (value & 0xFFFF).to_bytes(2, "big"))

    def _buf_size(self, n: int, reg: int) -> int:
        return self.mem[W.bsb_socket_reg(n)][reg] * 1024

    # SPI frame layer (byte level) --------------------------------------------------------------

    def cs_fall(self):
        self._frame, self._hdr = None, bytearray()

    def next_miso(self) -> int:
        """Byte to shift out during the upcoming byte slot."""
        f = self._frame
        if f is None or f.write:
            return 0x00
        return self._buf_read(f.bsb, f.addr + len(f.data))

    def push_mosi(self, byte: int, miso: int = 0):
        """Consume one byte from the master (``miso`` = byte that was shifted out meanwhile)."""
        if self._frame is None:
            self._hdr.append(byte)
            if len(self._hdr) == 3:
                ctrl = self._hdr[2]
                assert ctrl & 0b11 == W.OM_VDM, "only VDM frames are modelled"
                self._frame = Frame(bsb=ctrl >> 3, addr=(self._hdr[0] << 8) | self._hdr[1],
                                    write=bool(ctrl & 0b100))
            return
        f = self._frame
        if f.write:
            self._buf_write(f.bsb, f.addr + len(f.data), byte)
            f.data.append(byte)
        else:
            f.data.append(miso)

    def cs_rise(self):
        if self._frame is not None:
            self.frames.append(self._frame)
            if self._frame.write:
                self._on_write(self._frame)
        self._frame, self._hdr = None, bytearray()

    def transfer(self, mosi: bytes) -> bytes:
        """Whole frame at once (CS low, bytes, CS high); returns MISO bytes."""
        self.cs_fall()
        out = bytearray()
        for b in mosi:
            m = self.next_miso()
            out.append(m)
            self.push_mosi(b, m)
        self.cs_rise()
        return bytes(out)

    def _buf_read(self, bsb: int, addr: int) -> int:
        return self.mem[bsb][self._wrap(bsb, addr)]

    def _buf_write(self, bsb: int, addr: int, byte: int):
        self.mem[bsb][self._wrap(bsb, addr)] = byte

    def _wrap(self, bsb: int, addr: int) -> int:
        n, kind = bsb >> 2, bsb & 0b11
        if bsb != W.BSB_COMMON and kind in (0b10, 0b11):  # TX / RX buffer: ring of buf size.
            size = self._buf_size(n, W.Sn_TXBUF_SIZE if kind == 0b10 else W.Sn_RXBUF_SIZE)
            return addr % size if size else 0
        return addr & 0xFFFF

    # Register side effects ---------------------------------------------------------------------

    def _on_write(self, f: Frame):
        if f.bsb == W.BSB_COMMON:
            if f.addr <= W.MR < f.addr + len(f.data) and self.mem[W.BSB_COMMON][W.MR] & W.MR_RST:
                self.reset()
            return
        n, kind = f.bsb >> 2, f.bsb & 0b11
        if kind != 0b01:
            return
        regs = self.mem[f.bsb]
        if f.addr <= W.Sn_CR < f.addr + len(f.data):
            self._command(n, regs[W.Sn_CR])
            regs[W.Sn_CR] = 0  # Commands complete immediately in the model.
        if f.addr <= W.Sn_IR < f.addr + len(f.data):
            self._ir[n] &= ~f.data[W.Sn_IR - f.addr] & 0xFF  # Write 1 to clear.
            regs[W.Sn_IR] = self._ir[n]

    def _set_ir(self, n: int, bits: int):
        self._ir[n] |= bits
        self.mem[W.bsb_socket_reg(n)][W.Sn_IR] = self._ir[n]

    def _command(self, n: int, cmd: int):
        regs = self.mem[W.bsb_socket_reg(n)]
        if cmd == W.Sn_CR_OPEN:
            if regs[W.Sn_MR] & 0x0F == W.Sn_MR_UDP:
                regs[W.Sn_SR] = W.SOCK_UDP
            for reg in (W.Sn_TX_RD, W.Sn_TX_WR, W.Sn_RX_RD, W.Sn_RX_WR, W.Sn_RX_RSR):
                self._set16(n, reg, 0)
            self._set16(n, W.Sn_TX_FSR, self._buf_size(n, W.Sn_TXBUF_SIZE))
        elif cmd == W.Sn_CR_CLOSE:
            regs[W.Sn_SR] = W.SOCK_CLOSED
        elif cmd == W.Sn_CR_SEND and regs[W.Sn_SR] == W.SOCK_UDP and self.arp_fail:
            self._set_ir(n, W.Sn_IR_TIMEOUT)
        elif cmd == W.Sn_CR_SEND and regs[W.Sn_SR] == W.SOCK_UDP:
            rd, wr = self._get16(n, W.Sn_TX_RD), self._get16(n, W.Sn_TX_WR)
            length = (wr - rd) & 0xFFFF
            payload = bytes(self._buf_read(W.bsb_socket_tx(n), rd + i) for i in range(length))
            self.sent.append(Datagram(_ip_str(self.read(W.bsb_socket_reg(n), W.Sn_DIPR, 4)),
                                      self._get16(n, W.Sn_DPORT), payload))
            self._set16(n, W.Sn_TX_RD, wr)
            self._set16(n, W.Sn_TX_FSR, self._buf_size(n, W.Sn_TXBUF_SIZE))
            self._set_ir(n, W.Sn_IR_SENDOK)
        elif cmd == W.Sn_CR_RECV:
            rd, wr = self._get16(n, W.Sn_RX_RD), self._get16(n, W.Sn_RX_WR)
            self._set16(n, W.Sn_RX_RSR, (wr - rd) & 0xFFFF)
        # TODO: remaining commands (TCP, MACRAW, SEND_MAC/KEEP) are not needed for UDP.

    # Test-side stimulus ------------------------------------------------------------------------

    def inject(self, n: int, payload: bytes, ip: str = "192.168.1.100", port: int = 5000):
        """Deliver a datagram from the network into socket ``n``'s RX buffer."""
        header = bytes(int(x) for x in ip.split(".")) + port.to_bytes(2, "big") \
            + len(payload).to_bytes(2, "big")
        wr = self._get16(n, W.Sn_RX_WR)
        for i, b in enumerate(header + payload):
            self._buf_write(W.bsb_socket_rx(n), wr + i, b)
        wr = (wr + len(header) + len(payload)) & 0xFFFF
        self._set16(n, W.Sn_RX_WR, wr)
        self._set16(n, W.Sn_RX_RSR, (wr - self._get16(n, W.Sn_RX_RD)) & 0xFFFF)
        self._set_ir(n, W.Sn_IR_RECV)


class W5500SpiSlave:
    """cocotb adapter: SPI mode 0 slave on the DUT pins, backed by a ``W5500Model``."""

    def __init__(self, sclk, cs_n, mosi, miso, model: W5500Model | None = None):
        self.sclk, self.cs_n, self.mosi, self.miso = sclk, cs_n, mosi, miso
        self.model = model or W5500Model()

    def start(self):
        import cocotb

        return cocotb.start_soon(self._run())

    async def _run(self):
        from cocotb.triggers import FallingEdge, First, RisingEdge

        self.miso.value = 0
        while True:
            await FallingEdge(self.cs_n)
            self.model.cs_fall()
            out, bit, shift_in = self.model.next_miso(), 0, 0
            self.miso.value = (out >> 7) & 1
            while True:
                await First(RisingEdge(self.sclk), RisingEdge(self.cs_n))
                if self.cs_n.value == 1:
                    break
                shift_in = ((shift_in << 1) | int(self.mosi.value)) & 0xFF
                bit += 1
                if bit == 8:
                    self.model.push_mosi(shift_in, out)
                    out, bit = self.model.next_miso(), 0
                await First(FallingEdge(self.sclk), RisingEdge(self.cs_n))
                if self.cs_n.value == 1:
                    break
                self.miso.value = (out >> (7 - bit)) & 1
            self.model.cs_rise()
            self.miso.value = 0
