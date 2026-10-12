"""pytest entry point for the cocotb (Verilator) testbenches (cocotb_*.py here)."""

import shutil

import pytest

from w5500_litex_verilog_driver.impl import IMPLEMENTATIONS

from ..common import BUILD_DIR, IMPLS, run_cocotb

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None,
                                reason="verilator not installed")


# Implementations with a working TX path get the behaviour tests (stubs only the smoke tests).
TX_IMPLS = [i for i in IMPLS if "tx" in getattr(IMPLEMENTATIONS[i], "capabilities", ())]
# (impl, preset) that sustain a whole chunk per 1/44100 s (cocotb_tx realtime_44k1_packet_rate);
# everywhere else that test is an expected failure.
PACKET_RATE_OK = {("soc_v2", "stream")}


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("preset", ["default", "default32"])
def test_smoke(generated, preset, impl):
    run_cocotb(generated(preset, impl), "tests.sim.cocotb_smoke",
               BUILD_DIR / f"{preset}-{impl}" / "sim", preset)


@pytest.mark.parametrize("impl", TX_IMPLS)
@pytest.mark.parametrize("preset", ["default32", "stream"])
def test_tx(generated, preset, impl):
    ok = (impl, preset) in PACKET_RATE_OK
    run_cocotb(generated(preset, impl), "tests.sim.cocotb_tx",
               BUILD_DIR / f"{preset}-{impl}" / "sim", preset,
               extra_env={"W5500_PACKET_RATE_OK": "1" if ok else ""})
