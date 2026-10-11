"""pytest entry point for the cocotb (Verilator) testbenches in tests/hdl/."""

import shutil

import pytest

from .conftest import BUILD_DIR, IMPLS, ROOT, TOP

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None,
                                reason="verilator not installed")


def run_cocotb(verilog, test_module, build_dir, waves=False):
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
        results_xml  = str(build_dir / "results.xml"),
        waves        = waves,
    )


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("preset", ["default", "default32"])
def test_smoke(generated, preset, impl):
    run_cocotb(generated(preset, impl), "tests.hdl.cocotb_smoke",
               BUILD_DIR / f"{preset}-{impl}" / "sim")
