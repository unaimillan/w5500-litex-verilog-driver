"""Generate the W5500 UDP IP core as a single Verilog file.

Usage:  uv run w5500-gen --preset default --impl stub_rtl --output-dir build/gateware
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import __version__
from .config import PRESETS, CoreConfig
from .core import W5500UdpCore, io_list, ports
from .impl import IMPLEMENTATIONS, unsupported_reason
from .platform import VirtualPlatform


def generate(cfg: CoreConfig, impl: str, name: str = "w5500_udp",
             output_dir: str = "build/gateware") -> str:
    """Elaborate the core and write ``<output_dir>/<name>.v``; returns the file path."""
    platform = VirtualPlatform(io_list(cfg), name=name)
    core = W5500UdpCore(platform, cfg, impl)
    header = [
        f"{name}: W5500 UDP IP core (w5500-litex-verilog-driver {__version__})",
        f"impl          = {impl}",
        *cfg.summary(),
        "Generated file - do not edit; regenerate with `w5500-gen` instead.",
    ]
    return platform.build(core, build_dir=output_dir, build_name=name, header=header)


def list_options():
    """Print the implementations and presets (used by ``make help`` / usage)."""
    print("implementations (IMPL=):")
    for name, factory in sorted(IMPLEMENTATIONS.items()):
        doc   = (sys.modules[factory.__module__].__doc__ or "").strip().splitlines()
        title = doc[0].removeprefix(f"{name}: ") if doc else ""
        ok    = [p for p in sorted(PRESETS) if unsupported_reason(name, PRESETS[p]) is None]
        caps  = ", ".join(getattr(factory, "capabilities", ()))
        extra = ([f"capabilities: {caps}"] if caps else []) + \
                ([f"presets: {' '.join(ok)}"] if len(ok) < len(PRESETS) else [])
        print(f"  {name:<12} {title.replace('``', '')}")
        if extra:
            print(f"  {'':<12} ({'; '.join(extra)})")
    print("presets (PRESET=):")
    for name, cfg in sorted(PRESETS.items()):
        info = [f"data_width={cfg.data_width}", f"clk={cfg.clk_freq / 1e6:g} MHz",
                f"spi={cfg.spi_freq / 1e6:g} MHz", *cfg.notes]
        print(f"  {name:<12} {', '.join(info)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", default="default", choices=sorted(PRESETS),
                        help="CoreConfig preset (see config.py).")
    parser.add_argument("--impl", choices=sorted(IMPLEMENTATIONS),
                        help="Implementation (see impl/); required.")
    parser.add_argument("--build-name", default="w5500_udp", help="Top module / file name.")
    parser.add_argument("--output-dir", default="build/gateware", help="Output directory.")
    parser.add_argument("--verbose", action="store_true", help="Show LiteX SoC build logs.")
    parser.add_argument("--list", action="store_true", help="List implementations and presets.")
    args = parser.parse_args(argv)
    if args.list:
        list_options()
        return
    if args.impl is None:
        parser.error("--impl is required (see --list)")
    if not args.verbose:
        logging.disable(logging.INFO)  # LiteX SoC banner, bus/CSR maps, hierarchy.

    cfg = PRESETS[args.preset]
    path = generate(cfg, args.impl, args.build_name, args.output_dir)

    print(f"[w5500-gen] {os.path.relpath(path)}  (preset={args.preset}, impl={args.impl})")
    for port, direction, width in ports(cfg):
        print(f"[w5500-gen]   {direction:<3} {f'[{width - 1}:0]' if width > 1 else '':<6} {port}")


if __name__ == "__main__":
    main()
