# CLAUDE.md

Guidance for working on this repository.

## What this is

Generator for a standalone W5500 UDP IP core: `CoreConfig` (Python dataclass) + an
implementation from `impl/` → one self-contained Verilog file (`build/gateware/<name>.v`,
top module `<name>`, default `w5500_udp`). Python 3.14 via `uv`; Verilator/Yosys from
oss-cad-suite. Design notes and open questions: `docs/architecture.md`; W5500 protocol:
`docs/w5500-notes.md`.

`include/` (git-ignored) holds reference repos and the datasheet. Never depend on `include/`
paths from the build or tests. Prior art is surveyed in `docs/prior-art.md`; when an idea is
taken from it, credit the author + repo there (and next to the code that uses it).

## Commands

```sh
uv sync
make                       # help (targets, variables, `w5500-gen --list` options)
make generate IMPL=<impl> [PRESET=default] [BUILD_NAME=w5500_udp]   # no default IMPL
make build IMPL=<impl>     # + copy to dist/; without IMPL: usage + options
make test                  # uv run pytest (~250 s; cocotb sims under build/test/)
make lint IMPL=<impl>      # Verilator -Wall (+ waivers) on generated file, strict on rtl/, yosys check
make -j synth-estimate IMPL=<impl>  # yosys MAX10/Gowin/Xilinx/Lattice -> build/synth/*.stat
                           # (soc_v1: add -k, synth_intel can't map initialized M9K)
make check                 # ruff
uv run pytest tests/test_cocotb.py -k "default-stub_rtl"   # single sim
```

## Architecture

- `config.py` — `CoreConfig` (frozen, validated in `__post_init__`), derived values
  (`spi_div`, `cycles(us)`, `*_int`), `PRESETS`. No other config mechanism (by design).
- `core.py` — `PORT_GROUPS` is **the** port contract (`<group>_<field>` ports, `DW` =
  data_width). `W5500UdpCore` creates the `sys` clock domain from `clk`/`rst` and hands
  `pads` (one Record per group) to the selected impl.
- `impl/` — registry `IMPLEMENTATIONS`; factory `(platform, cfg, pads) -> Module`, must drive
  every `out` field (an undriven output silently becomes an input — `test_port_contract`
  catches it). Hybrid: pure Migen (`stub_migen.py`) or `Instance` of `rtl/*.v` with
  `platform.add_source(rtl_path(...))` and config passed as Verilog parameters
  (`stub_rtl.py: verilog_params`).
- `platform.py` — `VirtualPlatform.build()` writes Migen Verilog then appends every
  `add_source` file → single output. Fails if Migen needs data files (memory init).
- `w5500.py` — register map/constants; reuse from impls and tests, don't duplicate.
- TX terms: *word* = one 32-bit beat, *chunk* = `tx_chunk_bytes` (one SPI burst into the W5500),
  *datagram* = `tx_chunks_per_datagram` chunks (one UDP packet). Avoid "frame".
- `impl/soc_v1.py` + `firmware/soc_v1/` — LiteX SoC (vendored FemtoRV quark, LiteSPI master,
  `TxFramePort`, `W5500Ctrl`). The firmware is compiled at generation time with
  `riscv64-unknown-elf-gcc` against the generated `csr.h` and `config.h` (CoreConfig plus
  `w5500.py` constants), then inlined into the ROM. `supports(cfg)` and `capabilities` on a
  factory drive test skipping and selection (`cocotb_tx.py` runs for impls with `"tx"`).
- `impl/soc_v2.py` + `firmware/soc_v2/` — like soc_v1 (reuses its CPU wrapper, `W5500Ctrl`,
  `build_firmware`) but FemtoRV quark_bicycle, and `TxChunkStreamer` writes chunks FIFO → SPI
  in hardware; the CPU only does the control plane. Target preset `stream` (25 MHz SCLK, 10
  chunks per datagram); `PACKET_RATE_OK` in `tests/test_cocotb.py` lists (impl, preset) pairs
  that must sustain one chunk per 1/44100 s.
- `tests/models/w5500_model.py` — `W5500Model` (byte-level, pure Python, UDP OPEN/SEND/RECV,
  ring buffers, `inject()`/`sent`) + `W5500SpiSlave` cocotb pin adapter (mode 0).
- `tests/hdl/cocotb_*.py` — cocotb modules (not collected by pytest directly); run via
  `tests/test_cocotb.py` over every (preset, impl). New impls are picked up automatically.

## Conventions / gotchas

- `rtl/` sources must pass `verilator --lint-only -Wall` with no waivers (except documented
  stub-only ones). Generated file uses `VERI_WAIVE` (Makefile) = `VERILATOR_WAIVERS`
  (tests/conftest.py) — keep both in sync.
- `rtl/` may be SystemVerilog; the output stays `.v`, so tools must read it in SV mode
  (`read_verilog -sv`, Verilator default).
- Instance parameters: pass sized `Constant`s (Migen otherwise emits minimum widths).
- Keep everything in the single `sys` clock domain unless a design discussion decides otherwise.
- `vendor/` holds pinned third-party Verilog, unmodified apart from a provenance header. It is
  not linted strictly (unlike `rtl/`), and its authors are credited in `docs/prior-art.md`.
- `VirtualPlatform` inlines memory init (`$readmemh` → `initial` assignments). The Verilator
  waivers include LiteX's code style (COMBDLY, PROCASSINIT, CASEINCOMPLETE).
- Python style: LiteX-style aligned assignments are allowed (ruff ignores E221/E241/E251/E272);
  no auto-formatter.
