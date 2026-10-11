"""Vendor-agnostic platform that emits the whole core as ONE self-contained Verilog file."""

from __future__ import annotations

import os

from litex.build.generic_platform import GenericPlatform
from migen.fhdl.structure import _Fragment


class VirtualPlatform(GenericPlatform):
    """No toolchain, no pin locations: ``build()`` only writes Verilog.

    Migen logic is flattened into the top module; every hand-written file registered with
    ``add_source()`` (rtl/) is appended after it, so the output has no external dependencies.
    Hand-written sources may be SystemVerilog: read the output in SV mode (Verilator does by
    default; Yosys needs ``read_verilog -sv``).
    """

    def __init__(self, io, name="w5500_udp"):
        GenericPlatform.__init__(self, "virtual", io, name=name)

    def build(self, fragment, build_dir="build", build_name=None, header=(), **kwargs):
        if not isinstance(fragment, _Fragment):
            fragment = fragment.get_fragment()
        self.finalize(fragment)
        name = build_name or self.name

        conv = self.get_verilog(fragment, name=name, regular_comb=True, hierarchical=False)
        if conv.data_files:
            raise RuntimeError(f"generated design needs data files {sorted(conv.data_files)}: "
                               "memory init files break the single-file output")

        parts = ["".join(f"// {line}\n" for line in header), conv.main_source]
        for path, language, _library, *_ in self.sources:
            if language not in ("verilog", "systemverilog"):
                raise ValueError(f"{path}: unsupported source language {language!r}")
            with open(path) as f:
                parts.append(f"\n// {'-' * 96}\n// rtl: {os.path.basename(path)}\n"
                             f"// {'-' * 96}\n\n{f.read()}")

        os.makedirs(build_dir, exist_ok=True)
        out = os.path.join(build_dir, name + ".v")
        with open(out, "w") as f:
            f.write("".join(parts))
        return out
