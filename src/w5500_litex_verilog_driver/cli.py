"""Generate the W5500 UDP IP core as a single Verilog file.

Usage:  uv run w5500-gen --preset default --impl stub_rtl --output-dir build/gateware
"""

from __future__ import annotations

import argparse
import os

from . import __version__
from .config import PRESETS, CoreConfig
from .core import W5500UdpCore, io_list, ports
from .impl import IMPLEMENTATIONS
from .platform import VirtualPlatform

DEFAULT_IMPL = "stub_migen"


def generate(cfg: CoreConfig, impl: str = DEFAULT_IMPL, name: str = "w5500_udp",
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", default="default", choices=sorted(PRESETS),
                        help="CoreConfig preset (see config.py).")
    parser.add_argument("--impl", default=DEFAULT_IMPL, choices=sorted(IMPLEMENTATIONS),
                        help="Implementation (see impl/).")
    parser.add_argument("--build-name", default="w5500_udp", help="Top module / file name.")
    parser.add_argument("--output-dir", default="build/gateware", help="Output directory.")
    args = parser.parse_args(argv)

    cfg = PRESETS[args.preset]
    path = generate(cfg, args.impl, args.build_name, args.output_dir)

    print(f"[w5500-gen] {os.path.relpath(path)}  (preset={args.preset}, impl={args.impl})")
    for port, direction, width in ports(cfg):
        print(f"[w5500-gen]   {direction:<3} {f'[{width - 1}:0]' if width > 1 else '':<6} {port}")


if __name__ == "__main__":
    main()
