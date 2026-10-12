# Prior art: FPGA drivers for the W5500

A survey of existing open-source projects that drive a WIZnet W5500 from an FPGA, or from a
CPU next to one. Its purpose is to collect **ideas** (what works, what breaks) before we
choose an implementation approach. Each idea we adopt is credited to its author and
repository here. Commits are the ones inspected in October 2026.

Our requirements, for comparison: UDP to **one** remote endpoint; network settings fixed at
generation time; `valid/ready` streams of 8 or 32 bits with `last`; a single self-contained
Verilog file; a single clock domain; portable across vendors.

## Lineage

```
harout/concurrent-data-capture (2016, TX-only UDP streamer)
 ├── fengwyn/PacketProcessor           (verbatim copy + FIFO + checksum gate)
 └── multigcs/LinuxCNC-RIO             (adds RX, fixed-size frames)
      └── multigcs/riocore             (same core, parameterised from Python plugin)

independent:  daanders03/SCIS_DataConcentrator (VHDL, AXI-Stream, full UDP flow)
              YangYunLong1988/w5500             (TCP, layered FSMs)
              joshuawins99/Reference-FPGA-System, crmaykish/mackerel-68k (CPU + C driver)
              alexforencich/verilog-ethernet    (no W5500: own MAC/IP/UDP stack)
              unaimillan/verilog-mic-array      (project author's earlier experiments)
```

## Summary

| project | author | lang | proto | TX | RX | engine style | fit |
|---|---|---|---|---|---|---|---|
| [concurrent-data-capture](https://github.com/harout/concurrent-data-capture) `da4dd1e` | harout | Verilog | UDP | ✔ | – | init ROM + FSM | low |
| [riocore](https://github.com/multigcs/riocore) `1ea218d` / [LinuxCNC-RIO](https://github.com/multigcs/LinuxCNC-RIO) `c70aa03` | Oliver Dippel (multigcs) | Verilog | UDP | ✔ | ✔ | init ROM + FSM | medium |
| [PacketProcessor](https://github.com/fengwyn/PacketProcessor) `08a4b12` | fengwyn | Verilog | UDP | ✔ | – | (harout core) | low |
| [w5500](https://github.com/YangYunLong1988/w5500) `3852fe8` | YangYunLong1988 | Verilog | TCP | – | ✔ | nested FSMs | low |
| [SCIS_DataConcentrator](https://github.com/daanders03/SCIS_DataConcentrator/) `afb0b80` | Daniel Anders | VHDL | UDP | ✔ | ✔ | big FSM over streaming SPI | **high** |
| [Reference-FPGA-System](https://github.com/joshuawins99/Reference-FPGA-System) `b0d551a` | Joshua Winslow | SV + C | UDP | ✔ | ✔ | soft CPU (6502/RV32) | low |
| [mackerel-68k](https://github.com/crmaykish/mackerel-68k) `50786ed` | Colin Maykish | C | TCP | ✔ | ✔ | 68k CPU firmware | reference |
| [verilog-ethernet](https://github.com/alexforencich/verilog-ethernet) `77320a9` | Alex Forencich | Verilog | UDP/IP/ARP | ✔ | ✔ | own stack, no W5500 | interface conventions |
| [verilog-mic-array](https://github.com/unaimillan/verilog-mic-array) `19d0f2f` | Mike Kuskov (unaimillan) | SV | UDP | WIP | WIP | µ-program ROM + AXI-like SPI layer | ideas |

None of these projects can be dropped in. The interfaces don't match ours: they use wide
parallel registers, fixed packet sizes, or flush pulses. Some hard-code the configuration in
include files or literals, and several have functional gaps (listed below). The ideas are
what's worth taking.

---

## harout/concurrent-data-capture

Author **harout**, 2016 (Mojo board, Spartan-6, 50 MHz). Streams ADC samples out over UDP.
`src/wiznet5500.v` plus `src/wiznet5500_parameters.v`.

**How it works**
- Init is a list of 31 pre-assembled 32-bit SPI frames `{addr[15:0], control, data byte}`
  stepped through by `initialization_progress`. It covers PHY mode, MAC, IP, GW, mask,
  `Sn_MR`, `Sn_TXBUF_SIZE`, `Sn_PORT`, `OPEN`, `Sn_DIPR` and `Sn_DPORT`. It then polls `Sn_SR`
  until it reads `SOCK_UDP`.
- Data path: each `data_input_valid` writes a fixed 6 bytes (one 72-bit frame) at a
  **locally tracked** `tx_buffer_write_pointer`. `flush_requested` writes `Sn_TX_WR` (two
  frames) and then `SEND`.
- SPI: SCLK = clk/2 from the FSM. MOSI changes while SCLK is high, and MISO is sampled on the
  cycle before the rising edge.

**Ideas worth taking**
- The whole init sequence is **data**, not states. A table of `(addr, bsb, bytes)` entries
  maps directly to a generator: Python can build the table from `CoreConfig`.
- Tracking `Sn_TX_WR` locally saves one register read per datagram. This is only safe if
  nothing else writes the socket.

**Weaknesses**: TX only. No hardware reset or wait for PLL lock. No `Sn_TX_FSR` check, so it
can overwrite unsent data. No wait for `SENDOK`. Every register byte costs its own 4-byte frame.
SCLK is fixed at clk/2. Constants are hand-encoded binary in an include file.

## multigcs/riocore and multigcs/LinuxCNC-RIO

Author **Oliver Dippel** (multigcs). An FPGA I/O framework for LinuxCNC. The W5500 plugin
(`riocore/plugins/w5500/w5500.v`, `plugin.py`) is credited inline as "based on"
harout/concurrent-data-capture. Of all the designs here it has the most field use: many
boards, and a MAX10 demo at about 0.2 ms round trip.

**How it works**
- `plugin.py` turns user options (MAC/IP/mask/GW/port/SPI speed) into **Verilog
  parameters** of the instance. This is the same idea as our `verilog_params`.
- Power-up holds `rst` (W5500 RSTn) for a fixed number of cycles before init. The constants
  are hard-coded and only correct at one clock frequency.
- TX is the harout path with a parameterised frame width (`BUFFER_SIZE_TX` bits).
- RX: the idle loop polls `Sn_RX_RSR` every 10 cycles. It waits until exactly `8 + payload`
  bytes are present, retries 10 times, and then drains partial or garbage data. It reads the
  whole frame into a wide shift register and checks a 32-bit `MSGID` magic at the start of
  the payload. On a match it **copies the sender IP/port into `Sn_DIPR`/`Sn_DPORT`**, so
  replies go to whoever spoke last.
- The divider creates a fabric clock `mclk` that drives the whole core.

**Ideas worth taking**: config passed in as parameters, which we already do. Reading the
remote endpoint from the RX header (see open question 4). A magic/ID filter as an optional
RX filter. A bounded retry counter on RX.

**Weaknesses**: a derived clock from fabric logic, which causes timing and CDC trouble. It
reads only the **low byte** of `Sn_RX_RSR` (`rx_size <= data_read << 3`), so its size checks
break for larger datagrams. Datagrams have a fixed size. The author's own README reports a
0.02 % error rate.

## fengwyn/PacketProcessor

Author **fengwyn**, 2025 (Basys-3). `W5500Driver.v` is harout's `wiznet5500.v`, credited
inline and unchanged apart from the IP constants. `PacketProcessor.v` adds a bit-serial
input, a FIFO and a UDP checksum gate in front of it.

**Idea**: put validation and filtering *before* the driver, as an upstream stream stage, not
inside the W5500 FSM.
**Weaknesses**: unfinished. The driver's SPI outputs are not connected, and the checksum
logic reads FIFO output that has not been read yet.

## YangYunLong1988/w5500

Author **YangYunLong1988**, 2018. `W5500_PRO.v` (TCP client) and `SPI.v`. The comments are
in Chinese, GB18030-encoded.

**How it works**: nested FSMs (hardware reset → common init → socket init → connect → status
loop → read memory). A generic SPI block sends frames of 1, 2, 4 or 6 bytes, or a burst read
of `rx_size` bytes. SCLK is 100 kHz (divider 500). Received bytes land in `mem[0:99]` and are
decoded into fixed application registers.

**Idea**: separate the phases (reset, common init, socket init, run loop) into small FSMs
that hand off to each other. This makes each phase readable and testable on its own.
**Weaknesses**: TCP. Very slow SCLK. The write path is empty. Specific to one application.

## daanders03/SCIS_DataConcentrator — closest to our goal

Author **Daniel Anders**, 2026. A slow-control interlock system on a CologneChip GateMate
(40 MHz) with two W5500 modules (RX and TX). It has GHDL testbenches. Files:
`src/w5500/{w5500_state_machine, w5500_stream_manager, spi_streamer, spi_master,
ext_data_handler, top_w5500}.vhd`.

**How it works**
- **Layering:**
  1. `spi_master`: AXI-Stream byte in/out. One `tlast`-terminated stream is one CS frame,
     with FIFOs on both sides.
  2. `spi_streamer`: prepends the 24-bit header (`addr`, `control`) to a payload stream.
  3. `stream_manager`: switches the payload source between "controller phase" (register
     values from the FSM, up to 4 bytes) and "pass-through" (user TX stream into the W5500
     TX buffer, or the W5500 RX buffer out to the user RX stream).
  4. `w5500_state_machine`: the protocol.
- **User interface**: `ext_pl_t{data,valid,ready,last,user}` and
  `ext_pl_r{data,valid,ready,last,user}`, 8-bit. `tuser`/`ruser` carry the socket number.
- **TX flow**: read `Sn_TX_FSR` and loop until there are more than 256 bytes free. Write
  `Sn_DIPR` and `Sn_DPORT`. Read `Sn_TX_WR`. Stream the payload in one VDM frame while
  counting bytes. Write `Sn_TX_WR + n`. Issue `SEND`. Poll `Sn_IR` for `SENDOK` or `TIMEOUT`,
  then clear it.
- **RX flow**: read `Sn_RX_RSR` and skip if 0. Read `Sn_RX_RD`. Read `RSR` bytes in one VDM
  frame straight into the user stream. Write `Sn_RX_RD + RSR`. Issue `RECV`.
- **Policy**: opens 1 to 8 sockets with port = base + n, polls them round-robin, and offers
  a `send_first` or `receive_first` priority generic.

**Ideas worth taking**
- **Streaming pass-through.** The payload goes directly between the user stream and the
  W5500 buffer inside one SPI frame, so the core needs no local datagram buffer. This
  matches our open question 3 (low-area option).
- An SPI layer whose unit is "one CS frame = one stream with last". It cleanly separates the
  bit engine from the protocol.
- A configurable TX/RX priority policy.
- Checking `Sn_IR.TIMEOUT` as well as `SENDOK`. Without it, an ARP failure leaves the core
  waiting forever.

**Weaknesses and bugs**
- `Sn_IR` is "cleared" by writing to address `0x0003` (`Sn_SR`, read-only), not `0x0002`.
- The whole RX buffer content goes to the user, **including the 8-byte UDP header**, and
  several datagrams can merge into one stream. The README lists stripping the header as
  backlog.
- `Sn_RX_RSR`/`Sn_TX_FSR` are read only once, with no double-read for consistency.
- The README reports that the socket closes when the 2 KB RX buffer overflows under load.
  The datasheet does not describe overflow (item 9 in "To clarify" in `w5500-notes.md`).
- It needs a 2047-deep FIFO per direction in the SPI master.

## joshuawins99/Reference-FPGA-System

Author **Joshua Winslow**, 2025. A register system with a 6502 or RV32 soft CPU. The W5500 is
driven by C code (`C_Code/ethernet.c`) through a simple SPI peripheral
(`rtl/modules/spi_master*.sv`).

**Idea**: the soft-CPU approach. Everything is software, so it is flexible and easy to
debug. A useful baseline to compare against.
**Weaknesses**: one register byte per SPI transaction, so it is slow. A CPU, RAM and toolchain
are heavy for a fixed-function UDP core. `EthSendUDP` starts writing at `Sn_TX_RD`, not
`Sn_TX_WR`. That works only while the TX buffer is empty.

## crmaykish/mackerel-68k

Author **Colin Maykish**, 2026. A 68k computer. `firmware/w5500.c` is a compact TCP driver
over an SPI master (`tiny_spi` in the CPLD/FPGA).

**Ideas**:
- Clean burst helpers: `w5_read`/`w5_write` take an address, BSB and length.
- Soft reset via `MR.RST`, then poll until it clears.
- A `VERSIONR == 0x04` presence check.
- It relies on the chip auto-wrapping inside the socket buffer, so it never splits a burst
  at the buffer end.

**Field note** (not verified by us): "Dummy bytes MUST be 0xFF: a 0-bit on MOSI during a
W5500 read slips bits". The datasheet says nothing about MOSI during reads, so this is item 1
in the "To clarify" list of `w5500-notes.md`. Driving MOSI high during read data phases
costs nothing.

## alexforencich/verilog-ethernet

Author **Alex Forencich**. Deprecated in favour of
[fpganinja/taxi](https://github.com/fpganinja/taxi). A complete MAC/IP/UDP/ARP stack. There
is no W5500 involved: the FPGA talks to the PHY directly.

**Relevance**:
- It is the alternative architecture (an own stack instead of a hardwired TCP/IP chip). That
  is out of scope here.
- It is the reference for stream conventions: `tdata/tkeep/tvalid/tready/tlast/tuser`, with
  the UDP header carried as a separate header handshake (`udp_hdr_valid/ready` plus fields)
  next to the payload stream. Relevant to open questions 2 and 4.
- Its cocotb testbenches use `cocotbext-eth`.

## unaimillan/verilog-mic-array (the project author's earlier work)

Author **Mike Kuskov** (unaimillan), 2026. An I2S microphone array that streams over W5500.
Used here for ideas only.
- `rtl/w5500_axi_over_spi.sv`: an AXI-like transaction layer. Requests carry `aw/ar`
  (BSB, offset, length), followed by `w` beats, a `b` response or `r` beats with `rlast`, all
  mapped onto one VDM frame. This is the "frame = one burst transaction" layer, as an
  addressable bus instead of SCIS's raw stream.
- `rtl/w5500_ucpu_driver.sv`: a µ-program ROM (`SPI_WRITE_REQ`, `SPI_WRITE_I`, `SPI_READ_R`,
  `WAIT_FOR_TIMER`, `JUMP`, …) for init and periodic send. It is WIP and does not compile;
  the GAR/SUBR values are written to `SIPR`.
- `research/data/raw/*`: logic-analyzer and serial captures of the Arduino `Ethernet` library
  talking to a W5500 (UDP send). This is a real-world reference sequence: soft reset, MR
  checks, `VERSIONR`, SHAR/SIPR/GAR/SUBR, `Sn_MR`/`Sn_PORT`/`OPEN`, `Sn_DIPR`/`Sn_DPORT`,
  `Sn_TX_FSR` (read twice), `Sn_TX_WR`, burst, `Sn_TX_WR`, `SEND`.

---

## Engine styles observed

| style | seen in | pros | cons |
|---|---|---|---|
| Init ROM of SPI frames + small hand FSM for the data path | harout, riocore | tiny; init is data | one byte per frame; inflexible; data path still a hand FSM |
| Large explicit protocol FSM over a streaming SPI layer | SCIS | full UDP flow, streaming, no local buffer | many states; easy to make register typos (see the `Sn_IR` bug) |
| µ-program ROM (op + immediate), tiny sequencer | verilog-mic-array | sequences become data and can be generated from Python; small | needs an ISA, an assembler and a debug story |
| Soft CPU + C | Reference-FPGA-System, mackerel | most flexible | area, toolchain, firmware in the build, slow |
| Own Ethernet stack, no W5500 | verilog-ethernet | max throughput | different hardware and scope |

## What this suggests for us (input for the implementation discussion)

- **Layering**, combined from SCIS and verilog-mic-array:
  1. An SPI bit engine with a clock *enable* (no derived clock) and a configurable divider.
  2. A frame layer, "header + N bytes in one CS frame", streaming in both directions.
  3. A protocol controller.

  Layers 1 and 2 are shared by every implementation candidate.
- **Protocol controller candidates** to prototype side by side in `impl/`:
  - (a) a hand-written RTL FSM following the SCIS flow, with its bugs fixed;
  - (b) a Python-generated sequence (harout's "init is data" idea pushed to the whole flow),
    emitted as a Migen `Case`/`Array` ROM so it needs no init files.
- **Payload path**: stream it straight between the user stream and the W5500 buffer (SCIS
  pass-through), so the core holds no datagram copy. Strip the 8-byte RX header and
  optionally expose it as sideband (verilog-ethernet style). Check `Sn_TX_FSR` against the
  datagram length before starting.
- **Robustness items** that most projects miss: hardware reset plus PLL wait (riocore has a
  hard-coded version); `VERSIONR` check (mackerel); `TIMEOUT` handling (SCIS); consistent
  16-bit reads; RX overflow.
- **Tests**: replay the Arduino-library sequence (verilog-mic-array captures) as a reference
  scenario in the `W5500Model`.

## Third-party code used

- **FemtoRV32 "quark"** and **"quark_bicycle"** (Bruno Levy, Matthias Koch), the CPUs of `soc_v1` and `soc_v2`.
  - Source: <https://github.com/BrunoLevy/learn-fpga>, files `FemtoRV/RTL/PROCESSOR/femtorv32_quark.v` and `femtorv32_quark_bicycle.v` at commit `5c08c870315c09ccd9ec64ccde20ab3375b3f273`.
  - quark_bicycle is the same RV32I core with an overlapped fetch, about 20 % fewer cycles per instruction.
  - Vendored unmodified in `vendor/femtorv/`, used through LiteX's FemtoRV wrapper.

