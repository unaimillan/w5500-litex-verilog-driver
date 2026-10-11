"""Unit tests of the behavioral W5500 model itself (no simulator)."""

from w5500_litex_verilog_driver import w5500 as W

from .models.w5500_model import W5500Model


def wr(m, bsb, addr, data):
    m.transfer(W.spi_header(bsb, addr, write=True) + bytes(data))


def rd(m, bsb, addr, n):
    return m.transfer(W.spi_header(bsb, addr, write=False) + bytes(n))[3:]


def test_version_and_common_regs():
    m = W5500Model()
    assert rd(m, W.BSB_COMMON, W.VERSIONR, 1) == bytes([W.VERSION])
    wr(m, W.BSB_COMMON, W.SIPR, [192, 168, 1, 10])
    assert rd(m, W.BSB_COMMON, W.SIPR, 4) == bytes([192, 168, 1, 10])
    assert m.frames[-1].write is False and m.frames[-1].addr == W.SIPR


def test_udp_send():
    m, s = W5500Model(), 1
    reg = W.bsb_socket_reg(s)
    wr(m, reg, W.Sn_MR, [W.Sn_MR_UDP])
    wr(m, reg, W.Sn_CR, [W.Sn_CR_OPEN])
    assert rd(m, reg, W.Sn_SR, 1)[0] == W.SOCK_UDP
    wr(m, reg, W.Sn_DIPR, [10, 0, 0, 1])
    wr(m, reg, W.Sn_DPORT, (1234).to_bytes(2, "big"))

    wr(m, W.bsb_socket_tx(s), 0x0000, b"hello")
    wr(m, reg, W.Sn_TX_WR, (5).to_bytes(2, "big"))
    wr(m, reg, W.Sn_CR, [W.Sn_CR_SEND])

    assert m.sent[-1].payload == b"hello"
    assert (m.sent[-1].ip, m.sent[-1].port) == ("10.0.0.1", 1234)
    assert rd(m, reg, W.Sn_IR, 1)[0] & W.Sn_IR_SENDOK
    wr(m, reg, W.Sn_IR, [W.Sn_IR_SENDOK])
    assert rd(m, reg, W.Sn_IR, 1)[0] == 0


def test_udp_receive_with_header():
    m, s = W5500Model(), 0
    reg = W.bsb_socket_reg(s)
    wr(m, reg, W.Sn_MR, [W.Sn_MR_UDP])
    wr(m, reg, W.Sn_CR, [W.Sn_CR_OPEN])
    m.inject(s, b"abc", ip="192.168.1.100", port=4000)

    size = int.from_bytes(rd(m, reg, W.Sn_RX_RSR, 2), "big")
    assert size == W.UDP_RX_HEADER_LEN + 3
    data = rd(m, W.bsb_socket_rx(s), 0, size)
    assert data == bytes([192, 168, 1, 100]) + (4000).to_bytes(2, "big") + b"\x00\x03abc"

    wr(m, reg, W.Sn_RX_RD, size.to_bytes(2, "big"))
    wr(m, reg, W.Sn_CR, [W.Sn_CR_RECV])
    assert rd(m, reg, W.Sn_RX_RSR, 2) == b"\x00\x00"


def test_tx_ring_wraps():
    m, s = W5500Model(), 0
    wr(m, W.bsb_socket_tx(s), 2048 - 2, b"wrap")  # 2 KB buffer: last 2 bytes + first 2.
    assert rd(m, W.bsb_socket_tx(s), 0, 2) == b"ap"
