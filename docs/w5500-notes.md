# W5500 notes

Condensed from the WIZnet W5500 datasheet v1.1.0 (`DS p.N` = page in that PDF); the Python
constants live in `src/w5500_litex_verilog_driver/w5500.py`.

Statements are datasheet facts unless marked:

- *(derived)*: our own conclusion from datasheet facts.
- *(convention)*: what existing drivers do; the datasheet does not require it.
- **[verify]**: not covered, or ambiguous, in the datasheet. Clarify with other docs or
  research (WIZnet ioLibrary, application notes), or with an experiment on the real module.
  All of these are collected in [To clarify](#to-clarify).

## SPI

- Mode 0 or 3, MSB first. The chip latches MOSI on the rising SCLK edge and drives MISO on the
  falling edge (DS p.12). SCLK is 80 MHz in theory and 33.3 MHz guaranteed; module wiring and
  signal integrity decide the real limit (DS p.61).
- Timing (DS p.61), in ns:

  | parameter | value |
  |---|---|
  | SCLK high/low (TWH/TWL) | ≥ 6 |
  | SCSn high between frames (TCS) | ≥ 30 |
  | SCSn setup/hold (TCSS/TCSH) | ≥ 5 |
  | MOSI setup/hold (TDS/TDH) | ≥ 3 |
  | MISO valid after falling SCLK (TOV) | ≤ 5 |
  | MISO hold (TOH) | ≥ 0 |

  MISO is high-Z while SCSn is high (DS p.10).
- SCSn low → frame → SCSn high. Variable-length data mode (VDM, `OM=00`) streams any number of
  data bytes (N ≥ 1), and the offset address auto-increments by 1 per byte (DS p.14, 17).
  Fixed-length mode (FDM) is only for SCSn tied low and is "not recommended" (DS p.24).
- Frame (DS p.14–17):

  | byte | content |
  |------|---------|
  | 0    | address [15:8] |
  | 1    | address [7:0]  |
  | 2    | control = `BSB[4:0] << 3 \| RWB << 2 \| OM[1:0]` (RWB: 1 = write) |
  | 3…   | data |

- Block select (BSB): `00000` selects the common registers. For socket *n*, `n<<2 | 01` selects
  its registers, `n<<2 | 10` its TX buffer and `n<<2 | 11` its RX buffer. `n<<2 | 00` (n ≥ 1)
  is reserved and "can cause malfunction" (DS p.15–16).
- Each frame costs 3 header bytes, so batch payload writes into long frames *(derived)*.
- **[verify]** What MOSI carries during the data phase of a read. The datasheet only shows
  MISO there. mackerel-68k reports that it must be `0xFF` (see prior-art.md); driving `0xFF`
  is free, so do it, and test `0x00` on hardware.

## Reset / bring-up

1. RSTn low ≥ 500 µs (TRC); the internal PLL locks ≤ 1 ms after RSTn rises (TPL). Wait that
   long before accessing registers (DS p.10, 60). Alternative: soft reset by writing `MR.RST`
   (0x80), which clears itself when done (DS p.32).
2. Optional sanity check: `VERSIONR` (0x0039) always reads `0x04` (DS p.43).
3. Optional: wait for `PHYCFGR.LNK` (bit 0) = 1 (DS p.42). The PHY mode comes from the PMODE
   pins after reset (`PHYCFGR` reset value `0b10111XXX`). The chip has no auto-MDIX (DS p.62).
4. Write `SHAR` (MAC, 0x0009, 6 B), `GAR` (gateway, 0x0001, 4 B), `SUBR` (subnet, 0x0005, 4 B)
   and `SIPR` (IP, 0x000F, 4 B). Each value is big-endian, in dotted-notation order (DS
   p.33). These four registers are contiguous (0x0001–0x0012), so one VDM burst can write
   them all *(derived)*.
5. Socket buffer sizes `Sn_TXBUF_SIZE` (0x001F) and `Sn_RXBUF_SIZE` (0x001E) take a value in
   KB: 0, 1, 2, 4, 8 or 16. Any other value breaks TX/RX. The reset default is 2 KB per
   socket. The sum over all 8 sockets must be ≤ 16 KB in each direction, otherwise data
   errors occur. Buffers are allocated in socket order 0…7 "once all Sn_*BUF_SIZE registers
   have been configured" (DS p.31, 52–53).
   - Consequence: `tx_buf_kb`/`rx_buf_kb` > 2 needs the unused sockets shrunk (e.g. set to 0)
     *(derived)*.
   - **[verify]** Whether the new allocation takes effect without writing all 8 registers,
     and whether it must happen before `OPEN`. ioLibrary sets the sizes once at init, before
     any socket is opened *(convention)*.
6. Retry timing: `RTR` (0x0019, unit 100 µs, default 2000 = 200 ms) and `RCR` (0x001B,
   default 8). In UDP they only govern ARP: ARP timeout = RTR × 0.1 ms × (RCR + 1), which is
   **1.8 s** by default (DS p.38–39). Lower them if a send to a missing peer must fail faster.

## UDP socket

**Open** (DS p.44–46, 51):

- Set `Sn_MR = 0x02` (UDP) and `Sn_PORT` = local port. `Sn_PORT` must be set before `OPEN`.
- Issue `Sn_CR = OPEN (0x01)`. `Sn_CR` clears itself once the chip has accepted the command,
  but the command may still be running; completion shows in `Sn_SR`/`Sn_IR`. Poll until
  `Sn_SR == SOCK_UDP (0x22)`.
- Set `Sn_DIPR`/`Sn_DPORT` (destination); every `SEND` uses them. For multicast they must be
  set before `OPEN`. For unicast either order works *(convention)*.
- Other `Sn_MR` options: `BCASTB` (bit 6) blocks broadcast reception in UDP mode; `MULTI`
  (bit 7) enables multicast.

**Send** (DS p.47, 53–55):

1. Read `Sn_TX_FSR` (free space, reset value 0x0800). It is a 16-bit value that can change
   between the byte reads, so read it until two consecutive reads agree. Never write more
   than `Sn_TX_FSR` bytes: the excess overwrites unsent data.
2. Read `Sn_TX_WR` and write the payload into the TX buffer block, starting at offset
   `Sn_TX_WR`. The 16-bit offset (0x0000–0xFFFF) is mapped onto the socket's physical buffer
   by the chip (DS p.27, 31; Fig. 20 shows the 2 KB window repeating over the 64 KB offset
   space).
   - **[verify]** That one VDM burst crossing the end of the buffer wraps to its start
     (for example, a write at offset 0x07FE of 4 bytes into a 2 KB buffer). Fig. 20 implies
     it and mackerel-68k relies on it, but the text never says so. The fallback is to split
     the burst at the buffer boundary.
3. `Sn_TX_WR += len`, keeping the low 16 bits; write it back.
4. Issue `Sn_CR = SEND (0x20)`; poll `Sn_CR` until 0 (accepted). `SEND` transmits everything
   from `Sn_TX_RD` to `Sn_TX_WR` and then advances `Sn_TX_RD`.
5. Wait for `Sn_IR.SENDOK` (0x10), or `Sn_IR.TIMEOUT` (0x08: ARP timeout, see bring-up
   step 6). Clear the bit by writing 1 to it.
   - `SEND` resolves the destination MAC by ARP first, so the first send can take up to the
     ARP timeout.
   - **[verify]** Whether the resolved MAC is cached across `SEND`s. `MR.FARP` ("ARP on every
     send") implies it is.
   - `SEND_MAC (0x21)` skips ARP and uses `Sn_DHAR` instead. This is an option if the peer
     MAC is known at generation time.
   - **[verify]** Whether the next `SEND` may be issued before `SENDOK`. Drivers always wait
     *(convention)*.

**Receive** (DS p.48, 55–56):

1. Wait until `Sn_RX_RSR` > 0, or `Sn_IR.RECV` (0x04) is set. `Sn_RX_RSR` is 16 bits; read it
   until two consecutive reads agree. Its maximum is `Sn_RXBUF_SIZE`.
2. Read `Sn_RX_RD`, then read from the RX buffer block starting at offset `Sn_RX_RD`. The
   mapping is the same as for TX, and so is the wrap question.
   - **[verify]** Each UDP datagram in the buffer starts with an 8-byte header: source IP
     (4), source port (2), payload length (2), big-endian. The payload follows. **This format
     is not in the datasheet.** It comes from WIZnet's ioLibrary `recvfrom()` and prior art
     (SCIS passes the header through as data), and `W5500Model` implements it the same way.
     Confirm with a capture on hardware.
3. `Sn_RX_RD += 8 + len` (low 16 bits), write it back, then issue `Sn_CR = RECV (0x40)`;
   poll `Sn_CR` until 0.
   - **[verify]** Whether `RECV` may cover several datagrams at once, i.e. advance `RX_RD`
     past more than one header.

## Interrupts (DS p.34–37, 48, 56)

- `Sn_IR` (0x0002) bits are RCW1: read them, and write 1 to a bit to clear it; writing 0 has
  no effect.
- `SIR` bit *n* stays set while `Sn_IR` ≠ 0. INTn is low while any bit in `IR & IMR` or
  `SIR & SIMR` is set.
- `Sn_IMR` (0x002C, reset 0xFF): the datasheet says a `Sn_IR` bit is only *set* when its
  `Sn_IMR` bit is 1 (DS p.48, 56).
  - **[verify]** Is it a mask on the flag itself, or only on INTn? Until this is checked,
    leave `Sn_IMR` at 0xFF and mask INTn with `SIMR` only, so that polling `Sn_IR` keeps
    working.
- `INTLEVEL` (0x0013) sets a minimum time between INTn assertions.

## Gotchas

- 16-bit size registers (`Sn_TX_FSR`, `Sn_RX_RSR`) can change between their two byte reads:
  read until two consecutive values agree (DS p.54, 55).
- Pointer registers wrap at 16 bits; buffer offsets wrap at the buffer size *(derived)*.
- Wait for `Sn_CR` to read 0 before writing the next command. The datasheet only says it
  clears on acceptance; every driver waits *(convention)*.
- Max UDP payload is 1472 bytes: the W5500 does not support IP fragmentation (DS p.2), and
  1472 = 1500 MTU − 20 IP − 8 UDP *(derived)*. `Sn_FRAG` resets to 0x4000 (Don't Fragment).
- **[verify]** RX buffer overflow behaviour. The datasheet does not say what happens when a
  datagram arrives that does not fit. SCIS's README reports that the socket closes under
  load (prior-art.md); test this on hardware by flooding the socket.

## Lessons from prior art

Mistakes and practices seen in existing drivers (details and authors in `prior-art.md`):

- Generate SCLK as a clock *enable* in the system clock domain. Do not drive logic from a
  divided fabric clock (riocore's `mclk`).
- Use VDM bursts: one frame per register group, and one frame per payload. Byte-per-frame
  costs 4× the SPI time (harout, Reference-FPGA-System).
- Check `Sn_TX_FSR` against the datagram length before writing (required by DS p.53).
  Skipping it overwrites unsent data (harout, riocore).
- Start payload writes at `Sn_TX_WR`, as the datasheet procedure says (DS p.54), not at
  `Sn_TX_RD`. Reference-FPGA-System gets away with it only while the buffer is empty.
- `Sn_IR` lives at `0x0002` (DS p.30); SCIS writes the clear to `0x0003` (`Sn_SR`, read-only).
  Check `TIMEOUT` too, or an ARP failure hangs the send path.
- Read the full 16 bits of `Sn_RX_RSR`. riocore reads only the low byte.
- Several datagrams can sit in the RX buffer. Parse per datagram using the header's length;
  don't forward `RSR` bytes as one stream (SCIS).
- MOSI during read data phases: drive `0xFF` (mackerel-68k field note; see **[verify]** under
  SPI).

## To clarify

Open points, each with a suggested way to settle it. "Docs" means WIZnet ioLibrary sources,
application notes or forum answers; "HW" means an experiment on the W5500 Lite + DE10-Lite.

| # | question | docs | HW |
|---|---|---|---|
| 1 | MOSI value during read data phase: does `0x00` really slip bits? | – | read `VERSIONR` with `0x00` vs `0xFF` fill at several SCLK rates |
| 2 | Does a VDM burst wrap at the end of a socket buffer? | ioLibrary `wiz_send_data` (does it split?) | write 16 B at offset size−8, read back from 0 |
| 3 | Exact UDP RX header format (8 B: IP, port, len) | ioLibrary `recvfrom` | capture the RX buffer after a known datagram |
| 4 | `RECV` over several datagrams at once | ioLibrary | inject 2 datagrams, advance `RX_RD` past both, check `RSR` |
| 5 | Buffer-size reallocation: all 8 registers needed? before `OPEN` only? | ioLibrary `wizchip_init` | set socket *n* to 8 KB, check `TX_FSR` |
| 6 | `Sn_IMR` = 0: does it stop `Sn_IR` from being set? | WIZnet forum / errata | clear `Sn_IMR`, send, poll `Sn_IR` |
| 7 | ARP cache across `SEND`s; time of the first vs. later sends | – | time `SEND`→`SENDOK` |
| 8 | `SEND` before the previous `SENDOK` | ioLibrary | issue two `SEND`s back-to-back |
| 9 | RX overflow: drop the datagram, or close the socket? | – | flood the socket without reading, check `Sn_SR` |
| 10 | Max reliable SCLK and MISO sample point on the module | WIZnet SPI app note | sweep `spi_freq` with a read/compare loop |
