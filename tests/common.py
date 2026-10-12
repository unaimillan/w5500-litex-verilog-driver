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
