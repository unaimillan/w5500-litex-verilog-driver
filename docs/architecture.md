# Architecture

Status: first implementation **`soc_v1`** (TX only, see [below](#impl-soc_v1)); the stubs remain
as templates. Everything marked *draft* is still open for discussion.

## Goal

A standalone IP core for a larger FPGA design that talks UDP to one remote endpoint through a
WIZnet W5500 (W5500 Lite module) over SPI. All network/clock settings are fixed at generation
time; the user logic only sees valid/ready byte or word streams.

```
             +---------------------------- w5500_udp (generated .v) ---------------------------+
 tx stream ->|                                                                                 |
 rx stream <-|   impl (selected at generation time: Migen and/or hand-written rtl/)           |--SPI--> W5500
 status    <-|                                                                                 |--RSTn->
             +---------------------------------------------------------------------------------+
```

## Generation flow

```
CoreConfig (config.py preset) ──► W5500UdpCore (core.py: ports + clock domain)
                                       └─► IMPLEMENTATIONS[impl](platform, cfg, pads)
                                              ├─ Migen logic          ─┐
                                              └─ Instance + add_source ─┤
                                                                        ▼
                    VirtualPlatform.build(): Migen → Verilog, append rtl/ sources → <name>.v
```

- **One output file**, no `$readmem`/init files, no vendor primitives — portable to Quartus,
  Vivado, Gowin, Yosys flows.
- The config is printed into the header comment of the generated file.
- RTL implementations receive the config as Verilog parameters (`impl/stub_rtl.py:
  verilog_params`).

## Port contract (*draft*)

Declared in `core.py: PORT_GROUPS`; verified for every implementation by
`tests/test_generate.py`. `DW` = `CoreConfig.data_width` (8 or 32).

| port | dir | width | notes |
|------|-----|-------|-------|
| `clk` | in | 1 | single clock domain |
| `rst` | in | 1 | synchronous, active high |
| `w5500_spi_sclk`, `_cs_n`, `_mosi` | out | 1 | SPI master, mode 0 |
| `w5500_spi_miso` | in | 1 | synchronize inside the core |
| `w5500_rst_n` | out | 1 | hardware reset of the W5500 |
| `w5500_int_n` | in | 1 | optional; may be left unused by an impl |
| `tx_valid` / `tx_ready` | in / out | 1 | AXI-Stream-like handshake |
| `tx_data` | in | DW | first byte = most significant lane (*draft*) |
| `tx_last` | in | 1 | last beat of a datagram |
| `rx_valid` / `rx_ready` | out / in | 1 | |
| `rx_data` | out | DW | |
| `rx_last` | out | 1 | last beat of a datagram |
| `status_ready` | out | 1 | W5500 initialised and socket open |
| `status_error` | out | 1 | sticky error (version mismatch, send timeout, …) |

Stream rules (both directions): a beat transfers when `valid & ready` on a rising `clk` edge;
once asserted, `valid` and the payload stay stable until accepted.

## Open questions (for the implementation sessions)

Examples from existing drivers are in [prior-art.md](prior-art.md).

1. **Datagram framing**: keep `last` (one datagram per burst), or a fixed datagram size from
   config (`last` optional), or both? (riocore: fixed size; SCIS: `tlast`.)
2. **32-bit mode**: byte order inside a word; partial last word (add `tx_keep`/`rx_keep`, or
   require payload lengths that are multiples of 4?).
3. **TX buffering**: stream straight into the W5500 TX buffer (low area, length known only at
   `last`) vs. a local FIFO; behaviour when the datagram exceeds `max_payload`.
4. **RX**: filter datagrams by remote IP/port? Expose source IP/port/length as sideband? Drop
   policy when the user does not drain `rx` in time. (riocore: magic-ID filter + reply to last
   sender; SCIS: passes the raw header; verilog-ethernet: separate header handshake.)
5. **Engine style**: hard-coded FSMs vs. a tiny microcoded sequencer (ROM of SPI ops generated
   from Python) vs. a small soft CPU. The `impl/` registry lets these coexist for comparison.
   (init ROM + FSM: harout/riocore; big FSM over streaming SPI: SCIS; µ-program:
   verilog-mic-array; soft CPU: Reference-FPGA-System. See prior-art.md "Engine styles".)
6. **Polling vs. `INTn`**, SPI clock rate, MISO sampling point at high SCLK.
   Datasheet gaps that need docs research or hardware experiments are listed in
   [w5500-notes.md "To clarify"](w5500-notes.md#to-clarify).
7. **Errors / recovery**: link loss, ARP timeout, W5500 not responding — retry, reset, or flag?
8. **Throughput target** (sets SCLK, burst length and buffer sizes).

## Terminology (TX framing)

| term | meaning | config |
|---|---|---|
| word | one 32-bit `tx_data` handshake (e.g. one mic sample) | `data_width = 32` |
| chunk | the unit the producer hands over and the core writes into the W5500 as one SPI (VDM) burst | `tx_chunk_bytes` (default 40 = 10 words) |
| datagram | one UDP packet: `tx_chunks_per_datagram` chunks, SENT once all are in the socket TX buffer | `tx_chunks_per_datagram` (default 1; preset `stream`: 10 → 400 B) |

"Frame" is avoided: it already means an SPI frame (W5500), an Ethernet frame and an audio frame.

<a id="impl-soc_v1"></a>
## Implementation `soc_v1`: LiteX SoC (FemtoRV + LiteSPI) + firmware

Implementation attempt v1, `impl/soc_v1.py` + `firmware/soc_v1/`. Only LiteX/Migen gateware
and no hand-written RTL; the CPU Verilog is the vendored FemtoRV32 quark
(`vendor/femtorv/`, Bruno Levy & Matthias Koch,
<https://github.com/BrunoLevy/learn-fpga>).

Scope:
- TX only: the `rx_*` outputs are held idle and `int_n` is unused.
- `data_width = 32` is required (the default). Configs with `data_width = 8` are rejected via `SocV1.supports()`, and tests skip them.
- One datagram per chunk (`tx_chunk_bytes`, default 40, i.e. 10 handshakes). Configs with `tx_chunks_per_datagram > 1` are rejected.
- `tx_last` is ignored.
- Byte order: `tx_data[31:24]` is the first payload byte.

```
tx stream ─► TxFramePort (SyncFIFO, 20 × 32 bit) ──wishbone──┐
                                                               ├─ FemtoRV32 quark ── ROM 4 KiB (firmware)
W5500 SPI ◄─ LiteSPI SDR PHY ◄─ LiteSPIMaster ◄──── CSR ──────┤                    └ SRAM 1 KiB (stack)
RSTn / status_ready / status_error ◄─ W5500Ctrl ◄── CSR ──────┘
```

Memory map:

| region | origin | contents |
|---|---|---|
| rom | `0x0000_0000` | firmware (compiled at generation time, inlined into the `.v`) |
| sram | `0x0100_0000` | .data/.bss/stack |
| csr | `0x8200_0000` | `spi` (LiteSPIMaster), `spi_phy` (divider, mode), `w5500` (rst_n, ready, error, int_n) |
| txframe | `0x9000_0000` | `+0` WAIT: read stalls until ≥ one whole frame is buffered; `+4` DATA: read pops one word |

Hardware details:
- **`tx_ready`** is `fifo not full & status_ready`, so no input is accepted before bring-up or after an error.
- **Input FIFO** holds two frames (2 × `tx_chunk_bytes / 4` words). Before it pops the first word of a frame, the firmware spends ≈30 µs on `Sn_TX_FSR` / `Sn_TX_WR` reads. With only one frame of buffer, `tx_ready` stayed low that long, and a 44.1 kHz word source overran on the 11th word. The second frame absorbs this.
- **`status_error`** is a set-only flip-flop: firmware can raise it, but only `rst` clears it. It also forces `status_ready` low.
- **SCLK** is `clk / (2·spi_div)`, made by LiteSPI's clock-enable generator; there is no derived clock. LiteSPI only divides by even numbers, so the default `spi_freq` is now 12.5 MHz (50 MHz / 4).
- **Pin registers:** SCLK, MOSI, CS# and MISO go through registers (`SDROutput`/`SDRInput`). Because `rst` is synchronous, these pins are defined only after the first `clk` edge in reset.
- **No bus timeout:** the Wishbone bus has none, since the WAIT stall is intentional.

Build flow (`SocV1.__init__`):
1. Elaborate the SoC, then `finalize()`.
2. Write `generated/csr.h` (LiteX export) and `generated/config.h`. `config.h` holds the CoreConfig values as bytes and clk-cycle counts, plus the W5500 constants from `w5500.py`.
3. `riscv64-unknown-elf-gcc -march=rv32i -Os` builds the firmware, which goes into the ROM via `init_rom`.
4. `VirtualPlatform` replaces Migen's `$readmemh` with inline `mem[i] = …` assignments, so the output stays a single file.

Firmware (`main.c`), with timing from `rdcycle`:
1. **Reset:** RSTn low for `reset_hold_us`, then wait `init_wait_us`.
2. **Version:** `VERSIONR == 0x04`.
3. **Network registers:** write GAR/SUBR/SHAR/SIPR in one burst, then read back and compare.
4. **Socket registers:** `Sn_MR=UDP`, `Sn_PORT`, `Sn_DIPR`+`Sn_DPORT`, `Sn_RX/TXBUF_SIZE`, each written, read back and compared.
5. **Link:** wait for `PHYCFGR.LNK`, up to `link_timeout_ms`.
6. **Open:** OPEN; `Sn_CR` cleared and `Sn_SR == SOCK_UDP`, each within `cmd_timeout_us`. Then raise `ready`.
7. **Per frame:**
   1. Read WAIT; the CPU stalls on the bus here.
   2. Read `Sn_TX_FSR` until two reads agree; it must be ≥ frame size within `cmd_timeout_us`.
   3. Read `Sn_TX_WR`.
   4. Under one CS, send the 24-bit header and 10 × 32-bit DATA words. One LiteSPI transfer is kept queued ahead.
   5. Write `Sn_TX_WR + 40`, then SEND, then wait for `Sn_CR` to clear.
   6. Wait for `Sn_IR.SENDOK` or `Sn_IR.TIMEOUT`, up to `send_timeout_ms`, then clear SENDOK.

Every failed check calls `fail()`, which sets the sticky error and makes the CPU spin forever. That covers:
- wrong version
- register readback mismatch
- no link
- command not accepted, or socket state wrong
- no TX space
- ARP timeout (`Sn_IR.TIMEOUT`)
- no SENDOK

Measured and estimated results:
- **Simulation** (cocotb, W5500 model, 50 MHz, SCLK 12.5 MHz): bring-up takes ≈1.8 ms (1.5 ms of it is the reset wait), and each 40-byte frame takes ≈135 µs from handshake to SENDOK. That is ≈0.3 MB/s, limited by the CPU's per-word CSR handling and the register round trips per frame.
  - **Back-to-back** source (`tx_valid` always high, 10 / 100 frames): ≈144–148 µs per frame (≈0.27 MB/s). All frames are sent.
  - **44.1 kHz, one 32-bit word per tick** (a frame every ≈227 µs, 10 / 100 frames): every word is accepted immediately (min slack 22.6 µs). All frames are sent.
  - **44.1 kHz, one 40-byte frame per tick** (≈1.76 MB/s): not sustainable by v1. The test is marked `expect_fail` and overruns on the second frame.
- **Firmware size:** ≈1.7 KiB.
- **Yosys resource estimate** (`make -k -j synth-estimate PRESET=default32 IMPL=soc_v1`):
  - Xilinx 7: ≈1220 LUT, 600 FF, 2 RAMB18, 33 RAM32M.
  - Lattice XO2: ≈1470 LUT4, 600 FF, 4 DP8KC.
  - Gowin: ≈2000 LUT4, 570 FF; the ROM partly ends up in logic.
  - MAX10: Yosys `synth_intel` fails to map an initialized M9K (`Can't find object for defparam INIT`), so there is no estimate. **[verify]** with Quartus, including that it honours the inline `initial` ROM init.

Findings while building it:
- **FemtoRV `sel` bug:** LiteX's FemtoRV wrapper drives Wishbone `sel` from the write mask, which is 0 on reads. `Wishbone2CSR` only strobes CSR reads when `sel != 0`, so read-to-pop CSRs (LiteSPIMaster `rxtx`) never pop. `FemtoRVVendored` forces `sel = 0b1111` on reads.
- **FemtoRV `wget`:** LiteX's FemtoRV wrapper `wget`s the CPU Verilog into the current directory. It is vendored instead.
- **LiteSPI pacing:** LiteSPIMaster is paced per transfer, ≤ 32 bits each. SCLK stops between transfers, but CS stays low, which is fine for W5500 VDM bursts. Every transfer returns an RX word that must be popped, even for writes.

Possible next steps:
- Track `Sn_TX_WR` in software instead of reading it per frame.
- Skip the TX_FSR check while frames ≤ the buffer and SENDOK is awaited.
- Merge the 1-byte register ops into a single 32-bit transfer.
- Use a faster FemtoRV variant, or let hardware stream DATA → SPI.

All four are done in [`soc_v2`](#impl-soc_v2).

<a id="impl-soc_v2"></a>
## Implementation `soc_v2`: CPU on the control plane, chunks streamed FIFO → SPI in hardware

Implementation attempt v2, `impl/soc_v2.py` + `firmware/soc_v2/`. Its goal: 8 mics × 44.1 kHz ×
32 bit ≈ 1.41 MB/s in real time. Same scope, bring-up, error handling and byte order as soc_v1.
Differences:

- **No CPU in the data path.** `TxChunkStreamer` writes each chunk from the input FIFO into the socket TX buffer with one VDM burst. The CPU only gives it the TX buffer offset.
- **Batching:** one SEND per `tx_chunks_per_datagram` chunks. The register work per datagram is amortised over 10 chunks with the `stream` preset.
- **CPU:** FemtoRV32 **quark_bicycle**, the same RV32I core with overlapped fetch (≈20 % fewer cycles per instruction). It is vendored next to quark.
- **SCLK = clk / 2:** 25 MHz with the `stream` preset at 50 MHz, using LiteSPI divider 2.

```
tx stream ─► SyncFIFO (64 × 32 bit, block RAM) ─► TxChunkStreamer ──┐
                                                                      ├─mux─► LiteSPI SDR PHY ─► W5500 SPI
FemtoRV32 quark_bicycle ──CSR──► LiteSPIMaster ──────────────────────┘
        │  └──CSR──► txstream (start = TX buffer offset, busy)
        └─────CSR──► W5500Ctrl (RSTn, status_ready, status_error)
```

Hardware details:
- **`TxChunkStreamer`:**
  - A write to `txstream_start` (16-bit TX buffer offset) arms it.
  - It waits until the FIFO holds a whole chunk, so SCLK never stalls inside a burst.
  - It then owns the PHY for one VDM frame: CS low, the 24-bit header (offset, socket-n TX block, write), and chunk-words 32-bit transfers straight from the FIFO.
  - CS goes high once the PHY has returned every transfer; the MISO data is dropped.
  - `txstream_busy` is high from the `start` write until then.
- **PHY mux:** the PHY's sink and source streams go to the streamer while it is active and to LiteSPIMaster otherwise. CS = master CS | streamer CS.
  - Contract: the firmware leaves the master idle, with CS low, while `busy`.
  - The mux is written as one assignment per signal. A combinational block that both reads a `ready` and drives a `valid` shows up as a loop in Verilator (UNOPTFLAT).
- **Input FIFO:** the next power of two ≥ max(64, 2 chunks) words. 64 words ≈ 145 µs of input at one chunk per 1/44100 s. That covers the per-datagram control phase, during which nothing drains.
- **No bus stall:** nothing stalls the bus any more, so the default Wishbone timeout is back.

Firmware per datagram (bring-up as in soc_v1; `Sn_TX_WR` is read once after OPEN and then tracked):
1. Read `Sn_TX_FSR` until two reads agree; it must be ≥ datagram size within `cmd_timeout_us`. This happens once per datagram.
2. For each chunk:
   1. `txstream_start = Sn_TX_WR + c × tx_chunk_bytes`.
   2. Poll `txstream_busy`. This waits for the producer as well as for the burst.
3. Write `Sn_TX_WR + datagram size`, then SEND, then wait for `Sn_CR` to clear. Then wait for `Sn_IR.SENDOK` or `.TIMEOUT` and clear SENDOK.
4. 1-byte register accesses (`Sn_CR`, `Sn_IR`, `Sn_SR`) are a single 32-bit transfer (header + data). 2-byte ones are 32 + 8 bits.

Measured and estimated results:
- **Simulation** (cocotb, W5500 model, 50 MHz):

  | preset | SCLK | chunks / datagram | steady state, back-to-back | per datagram | rate |
  |---|---|---|---|---|---|
  | `stream` | 25 MHz | 10 | 18.3 µs / chunk | ≈183 µs (400 B) | ≈2.2 MB/s |
  | `default32` | 12.5 MHz | 1 | 70 µs / chunk | 70 µs (40 B) | ≈0.57 MB/s |
  | soc_v1, `default32` | 12.5 MHz | 1 | 148 µs / chunk | 148 µs (40 B) | ≈0.27 MB/s |

  - **44.1 kHz, one 40-byte chunk per tick** (≈1.76 MB/s, `stream`, 10 / 100 chunks): passes. The source is never stalled (min slack 22.5 µs of 22.7 µs), and every datagram is sent. On `default32` it remains an expected failure.
  - **Streaming one chunk** takes ≈15 µs: CS setup, 24-bit header, then 10 × (64 + ≈4) clk.
  - **Control phase** per datagram: ≈30 µs (FSR, TX_WR, SEND, Sn_CR, Sn_IR, IR clear).
- **Firmware size:** ≈1.7 KiB.
- **Yosys resource estimate** (`make -k -j synth-estimate PRESET=stream IMPL=soc_v2`). It costs ≈250–430 LUT more than soc_v1, for the bicycle core, the streamer and the larger FIFO.
  - Xilinx 7: ≈1475 LUT, 650 FF, 2 RAMB18, 27 RAM32M + 11 RAM64M.
  - Lattice XO2: ≈1900 LUT4, 690 FF, 6 DP8KC.
  - Gowin: ≈2050 LUT, 650 FF.
  - MAX10: same `synth_intel` ROM-init limitation as soc_v1.

**[verify]** on hardware (the model completes SEND instantly):
- **SEND → SENDOK time:** with headers, a 400-byte datagram takes ≈38 µs on the wire at 100 Mbit/s. That adds to the ≈183 µs per datagram unless SEND is overlapped (below). At 10 Mbit/s it takes ≈380 µs, longer than the 227 µs datagram period, so the real-time target needs a 100 Mbit link.
- **25 MHz SCLK** signal integrity and the MISO sample point on the module (w5500-notes item 10).
- **Overlapping SEND** of datagram k with the chunk writes of k+1, to hide the W5500's send time. Should be safe, since SEND uses the `Sn_TX_RD..Sn_TX_WR` range latched at SEND, but it is not done yet.

Open: a producer that stops in the middle of a datagram leaves the written chunks unsent; there is no flush or `tx_last` yet. See the open questions above.
- RX path; `tx_last` / variable length.

