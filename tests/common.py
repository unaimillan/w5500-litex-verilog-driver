"""Shared test constants (paths, top module, implementations, Verilator waivers)."""

from pathlib import Path

from w5500_litex_verilog_driver.impl import IMPLEMENTATIONS

ROOT      = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build" / "test"
TOP       = "w5500_udp"

IMPLS = sorted(IMPLEMENTATIONS)

# Verilator -Wall waivers for the generated file (keep in sync with VERI_WAIVE in the Makefile):
# Migen emits unsized/minimum-width constants and does not name files after modules; LiteX cores
# (CSR banks, FSMs, FIFOs) add `<=` in `always @(*)`, assigned-and-initialised regs and Case
# statements without default. Hand-written rtl/ sources are linted with plain -Wall
# (see unit/test_generate.py::test_rtl_lint).
VERILATOR_WAIVERS = ["-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-WIDTHEXPAND",
                     "-Wno-WIDTHTRUNC", "-Wno-COMBDLY", "-Wno-PROCASSINIT", "-Wno-CASEINCOMPLETE"]


def run_cocotb(verilog, test_module, build_dir, preset, waves=False, extra_env=None, top=TOP):
    """Build ``verilog`` with Verilator and run the cocotb ``test_module`` (preset in env)."""
    from cocotb_tools.runner import get_runner

    runner = get_runner("verilator")
    runner.build(
        sources      = [verilog],
        hdl_toplevel = top,
        build_dir    = build_dir,
        build_args   = ["-Wno-fatal", "-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC"],
        timescale    = ("1ns", "1ps"),
        waves        = waves,
        always       = True,
    )
    runner.test(
        hdl_toplevel = top,
        test_module  = test_module,
        test_dir     = ROOT,
        build_dir    = build_dir,
        results_xml  = str(build_dir / f"results-{test_module.rsplit('.', 1)[-1]}.xml"),
        waves        = waves,
        extra_env    = {"W5500_PRESET": preset, **(extra_env or {})},
    )
