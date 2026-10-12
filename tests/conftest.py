from pathlib import Path

import pytest

from w5500_litex_verilog_driver.cli import generate
from w5500_litex_verilog_driver.config import PRESETS
from w5500_litex_verilog_driver.impl import IMPLEMENTATIONS, unsupported_reason

ROOT      = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build" / "test"
TOP       = "w5500_udp"

IMPLS = sorted(IMPLEMENTATIONS)

# Verilator -Wall waivers for the generated file (keep in sync with VERI_WAIVE in the Makefile):
# Migen emits unsized/minimum-width constants and does not name files after modules; LiteX cores
# (CSR banks, FSMs, FIFOs) add `<=` in `always @(*)`, assigned-and-initialised regs and Case
# statements without default. Hand-written rtl/ sources are linted with plain -Wall
# (see test_generate.py::test_rtl_lint).
VERILATOR_WAIVERS = ["-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-WIDTHEXPAND",
                     "-Wno-WIDTHTRUNC", "-Wno-COMBDLY", "-Wno-PROCASSINIT", "-Wno-CASEINCOMPLETE"]


@pytest.fixture(scope="session")
def generated():
    """``generated(preset, impl) -> Path`` of the single-file Verilog, cached per session.

    Skips the calling test when the implementation does not support the preset.
    """
    cache = {}

    def get(preset: str, impl: str) -> Path:
        if reason := unsupported_reason(impl, PRESETS[preset]):
            pytest.skip(reason)
        key = (preset, impl)
        if key not in cache:
            out = BUILD_DIR / f"{preset}-{impl}" / "gateware"
            cache[key] = Path(generate(PRESETS[preset], impl, TOP, str(out)))
        return cache[key]

    return get
