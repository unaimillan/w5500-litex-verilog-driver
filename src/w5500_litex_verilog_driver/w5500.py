"""WIZnet W5500 register map and protocol constants (from the W5500 datasheet v1.x).

Single source of truth for the Python side (Migen implementations, test models). See
docs/w5500-notes.md for the SPI frame format and the UDP command sequences.
"""

# SPI frame: [ADDR 15:8][ADDR 7:0][CONTROL][DATA ...], MSB first, SPI mode 0 (or 3).
# CONTROL = BSB[4:0] << 3 | RWB << 2 | OM[1:0]

RWB_READ  = 0
RWB_WRITE = 1

OM_VDM  = 0b00  # Variable data length: frame length set by SCSn.
OM_FDM1 = 0b01  # Fixed 1 byte (SCSn tied low).
OM_FDM2 = 0b10  # Fixed 2 bytes.
OM_FDM4 = 0b11  # Fixed 4 bytes.

BSB_COMMON = 0b00000


def bsb_socket_reg(n: int) -> int:
    return (n << 2) | 0b01


def bsb_socket_tx(n: int) -> int:
    return (n << 2) | 0b10


def bsb_socket_rx(n: int) -> int:
    return (n << 2) | 0b11


def control_byte(bsb: int, write: bool, om: int = OM_VDM) -> int:
    return ((bsb & 0x1F) << 3) | (int(write) << 2) | (om & 0b11)


def spi_header(bsb: int, addr: int, write: bool, om: int = OM_VDM) -> bytes:
    """The 3-byte address + control phase of an SPI frame."""
    return bytes([(addr >> 8) & 0xFF, addr & 0xFF, control_byte(bsb, write, om)])


# Common registers (BSB_COMMON) ----------------------------------------------------------------
MR       = 0x0000  # Mode.
GAR      = 0x0001  # Gateway IP (4).
SUBR     = 0x0005  # Subnet mask (4).
SHAR     = 0x0009  # Source MAC (6).
SIPR     = 0x000F  # Source IP (4).
INTLEVEL = 0x0013  # Interrupt low level timer (2).
IR       = 0x0015  # Interrupt.
IMR      = 0x0016  # Interrupt mask.
SIR      = 0x0017  # Socket interrupt.
SIMR     = 0x0018  # Socket interrupt mask.
RTR      = 0x0019  # Retry time, 100 us units (2).
RCR      = 0x001B  # Retry count.
UIPR     = 0x0028  # Unreachable IP (4).
UPORTR   = 0x002C  # Unreachable port (2).
PHYCFGR  = 0x002E  # PHY configuration.
VERSIONR = 0x0039  # Chip version, reads VERSION.

VERSION = 0x04

MR_RST  = 0x80  # Software reset (self-clearing).
MR_WOL  = 0x20
MR_PB   = 0x10  # Ping block.
MR_PPPOE = 0x08
MR_FARP = 0x02  # Force ARP.

PHYCFGR_RST = 0x80
PHYCFGR_LNK = 0x01  # Link up.
PHYCFGR_SPD = 0x02  # 1 = 100 Mbps.
PHYCFGR_DPX = 0x04  # 1 = full duplex.

# Socket registers (bsb_socket_reg(n)) ---------------------------------------------------------
Sn_MR         = 0x0000  # Mode.
Sn_CR         = 0x0001  # Command (self-clearing; poll until 0).
Sn_IR         = 0x0002  # Interrupt (write 1 to clear).
Sn_SR         = 0x0003  # Status.
Sn_PORT       = 0x0004  # Source port (2).
Sn_DHAR       = 0x0006  # Destination MAC (6).
Sn_DIPR       = 0x000C  # Destination IP (4).
Sn_DPORT      = 0x0010  # Destination port (2).
Sn_MSSR       = 0x0012  # Max segment size (2).
Sn_TOS        = 0x0015
Sn_TTL        = 0x0016
Sn_RXBUF_SIZE = 0x001E  # KB: 0, 1, 2, 4, 8, 16.
Sn_TXBUF_SIZE = 0x001F
Sn_TX_FSR     = 0x0020  # TX free size (2), read twice until stable.
Sn_TX_RD      = 0x0022  # (2)
Sn_TX_WR      = 0x0024  # (2)
Sn_RX_RSR     = 0x0026  # RX received size (2), read twice until stable.
Sn_RX_RD      = 0x0028  # (2)
Sn_RX_WR      = 0x002A  # (2)
Sn_IMR        = 0x002C
Sn_FRAG       = 0x002D  # (2)
Sn_KPALVTR    = 0x002F

Sn_MR_CLOSE  = 0x00
Sn_MR_TCP    = 0x01
Sn_MR_UDP    = 0x02
Sn_MR_MACRAW = 0x04

Sn_CR_OPEN     = 0x01
Sn_CR_LISTEN   = 0x02
Sn_CR_CONNECT  = 0x04
Sn_CR_DISCON   = 0x08
Sn_CR_CLOSE    = 0x10
Sn_CR_SEND     = 0x20
Sn_CR_SEND_MAC = 0x21
Sn_CR_SEND_KEEP = 0x22
Sn_CR_RECV     = 0x40

Sn_IR_CON     = 0x01
Sn_IR_DISCON  = 0x02
Sn_IR_RECV    = 0x04
Sn_IR_TIMEOUT = 0x08  # UDP: ARP for the destination timed out.
Sn_IR_SENDOK  = 0x10

SOCK_CLOSED      = 0x00
SOCK_INIT        = 0x13
SOCK_LISTEN      = 0x14
SOCK_ESTABLISHED = 0x17
SOCK_CLOSE_WAIT  = 0x1C
SOCK_UDP         = 0x22
SOCK_MACRAW      = 0x42

# In UDP mode every datagram in the RX buffer is prefixed with this header:
# [src IP (4)][src port (2)][payload length (2)], big-endian.
UDP_RX_HEADER_LEN = 8
