# W5500 notes

Condensed from the WIZnet W5500 datasheet; the Python constants live in
`src/w5500_litex_verilog_driver/w5500.py`.

## SPI

- Mode 0 or 3, MSB first. SCLK up to 80 MHz in theory, 33.3 MHz guaranteed — module wiring and
  level of signal integrity decide the real limit.
- SCSn low → frame → SCSn high. Variable-length data mode (VDM, `OM=00`) streams any number of
  data bytes; the address auto-increments (wrapping inside a socket buffer).
- Frame:

  | byte | content |
  |------|---------|
  | 0    | address [15:8] |
  | 1    | address [7:0]  |
  | 2    | control = `BSB[4:0] << 3 \| RWB << 2 \| OM[1:0]` (RWB: 1 = write) |
  | 3…   | data |

- Block select (BSB): `00000` common registers; socket *n*: `n<<2 | 01` registers,
  `n<<2 | 10` TX buffer, `n<<2 | 11` RX buffer.
- Overhead: 3 header bytes per frame — batch payload writes into long frames.

## Reset / bring-up

1. RSTn low ≥ 500 µs, then wait for the PLL (≤ 1 ms) before accessing registers.
2. Optional sanity check: `VERSIONR` (0x0039) reads `0x04`.
3. Optional: wait for `PHYCFGR.LNK` (bit 0) = 1.
4. Write `SHAR` (MAC, 0x0009, 6 B), `GAR` (gateway, 0x0001), `SUBR` (subnet, 0x0005),
   `SIPR` (IP, 0x000F).
5. Socket buffer sizes `Sn_TXBUF_SIZE`/`Sn_RXBUF_SIZE` (KB; all sockets together ≤ 16 KB each
   direction; reset default 2 KB per socket).

## UDP socket

**Open**: `Sn_MR = 0x02` (UDP), `Sn_PORT` = local port, `Sn_CR = OPEN (0x01)`; poll `Sn_CR`
until 0, check `Sn_SR == SOCK_UDP (0x22)`. Set `Sn_DIPR`/`Sn_DPORT` (destination) — they are
used by every `SEND`.

**Send**:
1. Read `Sn_TX_FSR` (free space; read until two consecutive reads agree).
2. Read `Sn_TX_WR`; write payload into the TX buffer block starting at `Sn_TX_WR`
   (the address is taken modulo the buffer size by the chip).
3. `Sn_TX_WR += len` (16-bit wrap), write it back.
4. `Sn_CR = SEND (0x20)`; poll `Sn_CR` until 0.
5. Wait `Sn_IR.SENDOK` (0x10) or `Sn_IR.TIMEOUT` (0x08: ARP for the destination failed);
   clear by writing 1. The first send to a new destination triggers ARP and is slow.

**Receive**:
1. `Sn_RX_RSR` > 0 (read until stable) or `Sn_IR.RECV` (0x04).
2. Read `Sn_RX_RD`; read from the RX buffer at `Sn_RX_RD`. Each datagram starts with an 8-byte
   header: source IP (4), source port (2), payload length (2), big-endian; then the payload.
3. `Sn_RX_RD += 8 + len`, write it back, `Sn_CR = RECV (0x40)`; poll `Sn_CR` until 0.

## Gotchas

- 16-bit size/pointer registers can change between the two byte reads — read twice.
- Commands in `Sn_CR` are accepted only after the previous one cleared.
- `Sn_IR` bits are cleared by writing 1; `SIR`/`INTn` only assert for unmasked sources (`SIMR`,
  `Sn_IMR`).
- Max UDP payload without IP fragmentation: 1472 bytes (W5500 does not fragment).

## Lessons from prior art

Mistakes and practices seen in existing drivers (details and authors in `prior-art.md`):

- Generate SCLK as a clock *enable* in the system clock domain. Do not drive logic from a
  divided fabric clock (riocore's `mclk`).
- Use VDM bursts: one frame per register group, and one frame per payload. Byte-per-frame
  costs 4× the SPI time (harout, Reference-FPGA-System).
- Check `Sn_TX_FSR` against the datagram length before writing. Skipping it overwrites unsent
  data (harout, riocore).
- Start payload writes at `Sn_TX_WR`, not `Sn_TX_RD` (Reference-FPGA-System gets away with it
  only while the buffer is empty).
- `Sn_IR` lives at `0x0002`; SCIS writes the clear to `0x0003` (`Sn_SR`). Check `TIMEOUT` too, or
  an ARP failure hangs the send path.
- Read the full 16 bits of `Sn_RX_RSR`. riocore reads only the low byte.
- Several datagrams can sit in the RX buffer. Parse per datagram using the 8-byte header's
  length; don't forward `RSR` bytes as one stream (SCIS).
- Field note, unverified: drive MOSI = `0xFF` during read data phases (mackerel-68k).
