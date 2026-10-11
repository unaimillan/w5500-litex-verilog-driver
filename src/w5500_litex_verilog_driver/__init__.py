"""W5500 UDP IP core generator (LiteX/Migen + hand-written RTL)."""

from importlib.metadata import version

from .config import PRESETS, CoreConfig
from .core import PORT_GROUPS, W5500UdpCore, ports

__version__ = version("w5500-litex-verilog-driver")

__all__ = ["PORT_GROUPS", "PRESETS", "CoreConfig", "W5500UdpCore", "__version__", "ports"]
