# CLAUDE.md

Guidance for working on this repository.

## What this is

Generator for a standalone W5500 UDP IP core: `CoreConfig` (Python dataclass) + an
implementation from `impl/` → one self-contained Verilog file (`build/gateware/<name>.v`,
top module `<name>`, default `w5500_udp`). Python 3.14 via `uv`; Verilator/Yosys from
oss-cad-suite. Design notes and open questions: `docs/architecture.md`; W5500 protocol:
`docs/w5500-notes.md`.

This project is independent: `include/` (git-ignored) holds unrelated reference repos for
inspiration only — never import, copy from, or link to them.

## Commands

```sh
uv sync
make generate [PRESET=default] [IMPL=stub_migen] [BUILD_NAME=w5500_udp]
make build                 # + copy to dist/
make test                  # uv run pytest (~30 s; cocotb sims under build/test/)
make lint                  # Verilator -Wall (+ waivers) on generated file, strict on rtl/, yosys check
make -j synth-estimate     # yosys MAX10/Gowin/Xilinx/Lattice -> build/synth/*.stat
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
- Python style: LiteX-style aligned assignments are allowed (ruff ignores E221/E241/E251/E272);
  no auto-formatter.
