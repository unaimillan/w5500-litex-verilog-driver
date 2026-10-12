"""pytest entry point for the cocotb (Verilator) testbenches (cocotb_*.py here)."""

import shutil

import pytest

from w5500_litex_verilog_driver.impl import IMPLEMENTATIONS

from ..common import BUILD_DIR, IMPLS, ROOT, TOP

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None,
                                reason="verilator not installed")


# Implementations with a working TX path get the behaviour tests (stubs only the smoke tests).
TX_IMPLS = [i for i in IMPLS if "tx" in getattr(IMPLEMENTATIONS[i], "capabilities", ())]
# (impl, preset) that sustain a whole chunk per 1/44100 s (cocotb_tx realtime_44k1_packet_rate);
# everywhere else that test is an expected failure.
PACKET_RATE_OK = {("soc_v2", "stream")}


def run_cocotb(verilog, test_module, build_dir, preset, waves=False, extra_env=None):
    from cocotb_tools.runner import get_runner

    runner = get_runner("verilator")
    runner.build(
        sources      = [verilog],
        hdl_toplevel = TOP,
        build_dir    = build_dir,
        build_args   = ["-Wno-fatal", "-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC"],
        timescale    = ("1ns", "1ps"),
        waves        = waves,
        always       = True,
    )
    runner.test(
        hdl_toplevel = TOP,
        test_module  = test_module,
        test_dir     = ROOT,
        build_dir    = build_dir,
        results_xml  = str(build_dir / f"results-{test_module.rsplit('.', 1)[-1]}.xml"),
        waves        = waves,
        extra_env    = {"W5500_PRESET": preset, **(extra_env or {})},
    )


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
