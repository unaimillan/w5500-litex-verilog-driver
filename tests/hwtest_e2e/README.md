# Hardware end-to-end test: DE10-Lite + W5500 Lite

This test runs the generated core on a real board. It is integrated the way a user design would
use it: `w5500_udp.v` is added as a source and instantiated as a black box. A counter source
feeds it chunks, and a receiver on the host checks every chunk and reports the rates.

```
DE10-Lite (MAX10, clk50)                                         host PC
┌──────────────────────────────────────────────┐                 ┌──────────────────────┐
│ ChunkGenerator ──tx──► w5500_udp (generated) │──SPI──► W5500 ──►│ receiver.py :5000    │
│  SW[1:0] mode           soc_v1 / soc_v2      │  Arduino   Lite  │ checks SOF/EOF/cnt,  │
│  LEDs, HEX counters                          │  shield          │ chunks/s, lost       │
└──────────────────────────────────────────────┘                 └──────────────────────┘
```

| file | what |
|---|---|
| `traffic.py` | `ChunkGenerator`: the counter chunk source and its traffic modes |
| `de10lite.py` | Builds the board design: generates the core, then LiteX top + Quartus project / build |
| `receiver.py` | Host UDP receiver (stdlib only, Python ≥ 3.10) |
| `sim_top.py`, `cocotb_hwtest.py`, `test_hwtest_e2e.py` | The same design in simulation against the W5500 model, run by `make test` |

## Chunk format

One chunk is 40 bytes (`tx_chunk_bytes`), made of 10 big-endian 32-bit words:

```
"<--|"  {16'h0, cnt0}  {16'h0, cnt1}  ...  {16'h0, cnt7}  "|-->"
```

- `cnt` is a 16-bit counter. It goes up by one per word and carries on into the next chunk, so
  each chunk starts at the previous start + 8, mod 65536.
- soc_v1 sends one chunk per datagram.
- soc_v2 with the `stream` preset sends 10 chunks per datagram (400 B).

## Wiring

The W5500 Lite sits on an Arduino shield on the DE10-Lite Arduino header. The pin mapping is the
same as in verilog-mic-array.

| core port | Arduino | FPGA pin |
|---|---|---|
| `w5500_spi_cs_n` | D10 | AB19 |
| `w5500_spi_mosi` | D11 | AA19 |
| `w5500_spi_miso` | D12 | Y19 |
| `w5500_spi_sclk` | D13 | AB20 |
| `w5500_rst_n` | RESET | F16 (`ARDUINO_RESET_N`) |
| `w5500_int_n` | — | tied high (not used by soc_v1/v2) |

## Network

The addresses are the `CoreConfig` defaults (`config.py`):

| | IP | UDP port |
|---|---|---|
| board (W5500) | 192.168.1.10 | 5000 |
| host (receiver) | **192.168.1.100**/24 | 5000 |

- Give the host NIC 192.168.1.100/24, for example on a direct cable.
- The W5500 resolves the host's MAC with ARP before the first datagram, so a firewall must let
  ARP and UDP 5000 through.
- soc_v2 in full mode needs a 100 Mbit/s link: about 1.8 MB/s of payload.

## Build (container) and program (host)

```sh
make hw-build IMPL=soc_v1       # -> build/hwtest/soc_v1/gateware/hwtest_de10lite.sof (preset de10lite)
make hw-build IMPL=soc_v2       # -> build/hwtest/soc_v2/gateware/hwtest_de10lite.sof (preset stream)
make hw-gen   IMPL=soc_v2       # same without running Quartus (Verilog + .qsf/.sdc only)
```

To use another core preset, pass `PRESET=<name>`. It must keep 50 MHz, 32-bit data and 40-byte
chunks; the script checks this.

Program the board on the host, where the USB-Blaster is. The workspace is bind-mounted, so the
`.sof` is already there:

```sh
quartus_pgm -m jtag -o "p;build/hwtest/soc_v2/gateware/hwtest_de10lite.sof"
```

## Board controls

| control | function |
|---|---|
| SW[1:0] | traffic mode: `00` off, `01` little, `10` middle, `11` full. A new mode takes effect at the next chunk boundary. |
| SW9 | HEX shows `0` = chunks sent, `1` = overruns |
| KEY0 | reset: core and W5500 bring-up restart |
| KEY1 | clear the chunk/overrun counters and the overrun LED |

| LED | meaning |
|---|---|
| LEDR0 | `status_ready`: W5500 set up, link up, socket open |
| LEDR1 | `status_error`: sticky core error (version, link timeout, ARP timeout, …); press KEY0 |
| LEDR2 | overrun: a periodic chunk was dropped because the core did not take the previous one in time |
| LEDR3 | the source is stalled by the core (`tx_valid & ~tx_ready`) |
| LEDR4 | heartbeat (~1.5 Hz) |
| LEDR9..8 | mode of the current / last chunk |
| HEX5..0 | chunks sent (hex, 24 LSBs), or overruns with SW9 up |

## Traffic modes

| mode | source | rate | purpose |
|---|---|---|---|
| little | one chunk every 10 ms | 100 chunks/s, 4 kB/s | basic function, both impls |
| middle | one chunk every 250 µs | 4000 chunks/s, 160 kB/s | what soc_v1 can carry with margin (≈148 µs per chunk in simulation) |
| full | back-to-back, throttled only by `tx_ready` | as fast as the core takes it | pressure test: can soc_v2 carry 44 100 chunks/s (8 mics × 44.1 kHz)? |

In little and middle mode, a chunk that becomes due while the previous one is still waiting
counts as dropped. The overrun count goes up, LEDR2 lights, and the 8 counter values of that
chunk are skipped, so the receiver reports them as lost, exactly like a real-time source losing
data. Full mode never drops; only the rate tells you anything there.

## Procedure

1. Program the `.sof`. Within about 3 s (W5500 reset + auto-negotiation), LEDR0 should light and
   LEDR1 should stay off.
2. Start the receiver on the host. Use `make hw-receive` from a checkout, or
   `python3 tests/hwtest_e2e/receiver.py [--expect-rate 44100]`. It prints one line per second
   (datagrams/s, chunks/s, kB/s, lost, malformed) and a summary on Ctrl-C. The exit code is
   non-zero if anything was lost or malformed.
3. Step SW[1:0] through little → middle → full, about 30 s each, and watch LEDR1/LEDR2 and the
   receiver output.
4. Repeat with the other implementation.

### Pass criteria

| mode | soc_v1 (`de10lite`) | soc_v2 (`stream`) |
|---|---|---|
| little | 100 chunks/s, 0 lost, LEDR2 off | 100 chunks/s (10 datagrams/s), 0 lost, LEDR2 off |
| middle | 4000 chunks/s, 0 lost, LEDR2 off | 4000 chunks/s, 0 lost, LEDR2 off |
| full | 0 lost; record the rate (simulation: ≈6.9 k chunks/s) | 0 lost; ≥ 44 100 chunks/s (simulation: ≈54 k steady) |

Notes:
- **Lost chunks in full mode** cannot come from the source, because it never drops. They mean
  datagrams were lost on the network or in the host's socket buffer. Check with
  `netstat -su` (receive buffer errors).
- **Datagrams that never show up:** a partial datagram (soc_v2: fewer than 10 chunks after the
  mode is switched off) stays in the W5500 until more chunks follow. This is a known open point
  (no flush / `tx_last` yet).

Record the results in `docs/architecture.md`: rates, any errors, Quartus resources and timing.
