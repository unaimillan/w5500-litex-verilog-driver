"""Vendor-agnostic platform that emits the whole core as ONE self-contained Verilog file."""

from __future__ import annotations

import os
import re

from litex.build.generic_platform import GenericPlatform
from migen.fhdl.structure import _Fragment

_READMEMH = re.compile(r'^\s*\$readmemh\("([^"]+)", (\w+)\);\n', re.M)


def inline_memory_init(source: str, data_files: dict[str, str]) -> str:
    """Replace Migen's ``$readmemh("<file>", mem);`` by one ``mem[i] = 'h..;`` per word.

    Keeps the output self-contained. Initial blocks with constant assignments are accepted for
    RAM/ROM inference by Yosys, Vivado and Quartus (Quartus: verify on the real toolchain).
    """
    used = set()

    def repl(m):
        filename, mem = m.groups()
        used.add(filename)
        words = data_files[filename].split()
        return "".join(f"\t{mem}[{i}] = 'h{w};\n" for i, w in enumerate(words))

    source = _READMEMH.sub(repl, source)
    if missing := sorted(set(data_files) - used):
        raise RuntimeError(f"generated design needs data files {missing}: "
                           "cannot inline them into the single-file output")
    return source


class VirtualPlatform(GenericPlatform):
    """No toolchain, no pin locations: ``build()`` only writes Verilog.

    Migen logic is flattened into the top module (memory init inlined, no ``$readmemh``); every
    Verilog file registered with ``add_source()`` (rtl/, vendor/) is appended after it, so the
    output has no external dependencies.
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
        main = inline_memory_init(conv.main_source, conv.data_files)

        parts = ["".join(f"// {line}\n" for line in header), main]
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
