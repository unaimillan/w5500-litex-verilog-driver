"""Generated Verilog: single self-contained file honouring the port contract."""

import re
import shutil
import subprocess

import pytest

from w5500_litex_verilog_driver.config import PRESETS
from w5500_litex_verilog_driver.core import ports

from .conftest import IMPLS, ROOT, TOP, VERILATOR_WAIVERS

RTL_SOURCES = sorted([*(ROOT / "rtl").glob("*.v"), *(ROOT / "rtl").glob("*.sv")])

PORT_RE = re.compile(r"^\s*(input|output|inout)\s+wire\s+(?:\[(\d+):0\])?\s*(\w+)", re.M)


def top_ports(path):
    src = path.read_text()
    header = src[src.index(f"module {TOP} ("):]
    header = header[:header.index(");")]
    return {name: (direction, int(msb) + 1 if msb else 1)
            for direction, msb, name in PORT_RE.findall(header)}


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("preset", ["default", "default32"])
def test_port_contract(generated, preset, impl):
    expected = {name: ({"in": "input", "out": "output"}[d], w)
                for name, d, w in ports(PRESETS[preset])}
    assert top_ports(generated(preset, impl)) == expected


@pytest.mark.parametrize("impl", IMPLS)
def test_self_contained(generated, impl):
    src = generated("default", impl).read_text()
    assert "$readmem" not in src
    assert "`include" not in src
    assert src.startswith(f"// {TOP}: W5500 UDP IP core")


@pytest.mark.skipif(shutil.which("yosys") is None, reason="yosys not installed")
@pytest.mark.parametrize("impl", IMPLS)
def test_yosys_check(generated, impl):
    path = generated("default", impl)
    subprocess.run(["yosys", "-q", "-p",
                    f"read_verilog -sv {path}; hierarchy -check -top {TOP}; proc; check -assert"],
                   check=True)


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not installed")
@pytest.mark.parametrize("impl", IMPLS)
def test_verilator_lint(generated, impl):
    path = generated("default", impl)
    subprocess.run(["verilator", "--lint-only", "-Wall", *VERILATOR_WAIVERS,
                    "--top-module", TOP, str(path)], check=True)


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not installed")
@pytest.mark.parametrize("path", RTL_SOURCES, ids=lambda p: p.name)
def test_rtl_lint(path):
    """Hand-written sources are held to plain -Wall."""
    subprocess.run(["verilator", "--lint-only", "-Wall", str(path)], check=True)
