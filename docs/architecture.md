# Architecture

Status: **scaffold** — only placeholder implementations exist. Everything below marked *draft*
is open for discussion before the first real implementation.

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

1. **Datagram framing**: keep `last` (one datagram per burst), or a fixed datagram size from
   config (`last` optional), or both?
2. **32-bit mode**: byte order inside a word; partial last word (add `tx_keep`/`rx_keep`, or
   require payload lengths that are multiples of 4?).
3. **TX buffering**: stream straight into the W5500 TX buffer (low area, length known only at
   `last`) vs. a local FIFO; behaviour when the datagram exceeds `max_payload`.
4. **RX**: filter datagrams by remote IP/port? Expose source IP/port/length as sideband? Drop
   policy when the user does not drain `rx` in time.
5. **Engine style**: hard-coded FSMs vs. a tiny microcoded sequencer (ROM of SPI ops generated
   from Python) vs. a small soft CPU. The `impl/` registry lets these coexist for comparison.
6. **Polling vs. `INTn`**, SPI clock rate, MISO sampling point at high SCLK.
7. **Errors / recovery**: link loss, ARP timeout, W5500 not responding — retry, reset, or flag?
8. **Throughput target** (sets SCLK, burst length and buffer sizes).
