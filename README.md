# w5500-litex-verilog-driver

Standalone Verilog IP core that sends and receives UDP datagrams through a WIZnet **W5500**
(tested target: W5500 Lite + Terasic DE10-Lite). The core is generated with LiteX/Migen (plus
optional hand-written Verilog) into **one self-contained `.v` file**. All settings (MAC, IPs,
ports, clock, SPI rate, data width) are baked in at generation time; your design only sees
simple valid/ready streams.

> Status: **scaffold**. The generator, port contract, test bench and W5500 model are in place;
> the implementations are still placeholders. See [docs/architecture.md](docs/architecture.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python ≥ 3.14 is fetched automatically)
- Verilator ≥ 5 and Yosys, e.g. from [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build)

```sh
uv sync                          # LiteX stack + dev tools (pytest, cocotb, ruff)
```

## Usage

```sh
make build                                   # -> dist/w5500_udp.v
make build PRESET=de10lite IMPL=stub_rtl     # pick a config preset / implementation
make test                                    # pytest: config, generation, lint, model, cocotb
make lint                                    # Verilator + Yosys checks of the generated file
make -j synth-estimate                       # Yosys resource estimate (MAX10/Gowin/Xilinx/Lattice)
make help
```

or directly: `uv run w5500-gen --preset default --impl stub_migen --output-dir build/gateware`.

### Configuration

Settings are a frozen dataclass, `CoreConfig` in
[`src/w5500_litex_verilog_driver/config.py`](src/w5500_litex_verilog_driver/config.py); add
your own entry to `PRESETS` (values are validated on construction):

```python
PRESETS["lab"] = CoreConfig(clk_freq=50_000_000, spi_freq=12_500_000, data_width=32,
                            ip="10.0.0.2", remote_ip="10.0.0.1", local_port=7000, remote_port=7000)
```

## Interface

```verilog
w5500_udp u_eth (
    .clk            (clk),            .rst            (rst),          // sync, active high
    // W5500 module
    .w5500_spi_sclk (eth_sclk),       .w5500_spi_cs_n (eth_cs_n),
    .w5500_spi_mosi (eth_mosi),       .w5500_spi_miso (eth_miso),
    .w5500_rst_n    (eth_rst_n),      .w5500_int_n    (eth_int_n),
    // to remote: one datagram per burst, `last` on the final beat
    .tx_valid (tx_valid), .tx_ready (tx_ready), .tx_data (tx_data), .tx_last (tx_last),
    // from remote
    .rx_valid (rx_valid), .rx_ready (rx_ready), .rx_data (rx_data), .rx_last (rx_last),
    .status_ready (eth_ready),        .status_error (eth_error)
);
```

`tx_data`/`rx_data` are 8 or 32 bits wide (`data_width`). The full contract and the open design
questions are in [docs/architecture.md](docs/architecture.md); W5500 protocol notes in
[docs/w5500-notes.md](docs/w5500-notes.md); survey of existing W5500 FPGA drivers (ideas, authors) in
[docs/prior-art.md](docs/prior-art.md).

## Layout

```
src/w5500_litex_verilog_driver/
  config.py      CoreConfig + presets          core.py      port contract, top module
  platform.py    single-file Verilog output    cli.py       `w5500-gen`
  w5500.py       W5500 register map            impl/        interchangeable implementations
rtl/             hand-written (System)Verilog used by impl/ (embedded into the output)
tests/           pytest + cocotb; models/w5500_model.py = behavioral W5500 (SPI slave + UDP)
docs/            architecture, W5500 notes, prior-art survey
```
