from pathlib import Path

import pytest

from w5500_litex_verilog_driver.cli import generate
from w5500_litex_verilog_driver.config import PRESETS
from w5500_litex_verilog_driver.impl import IMPLEMENTATIONS

ROOT      = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build" / "test"
TOP       = "w5500_udp"

IMPLS = sorted(IMPLEMENTATIONS)

# Verilator -Wall waivers for the generated file (keep in sync with VERI_WAIVE in the Makefile):
# Migen emits unsized/minimum-width constants and does not name files after modules.
# Hand-written rtl/ sources are linted with plain -Wall (see test_generate.py::test_rtl_lint).
VERILATOR_WAIVERS = ["-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-WIDTHEXPAND",
                     "-Wno-WIDTHTRUNC"]


@pytest.fixture(scope="session")
def generated():
    """``generated(preset, impl) -> Path`` of the single-file Verilog, cached per session."""
    cache = {}

    def get(preset: str, impl: str) -> Path:
        key = (preset, impl)
        if key not in cache:
            out = BUILD_DIR / f"{preset}-{impl}" / "gateware"
            cache[key] = Path(generate(PRESETS[preset], impl, TOP, str(out)))
        return cache[key]

    return get
