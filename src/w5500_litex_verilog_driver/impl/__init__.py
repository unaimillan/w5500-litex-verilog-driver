"""Interchangeable core implementations.

An implementation is a factory ``(platform, cfg, pads) -> Module``. ``pads`` has one Migen Record
per port group of ``core.PORT_GROUPS`` (``pads.w5500``, ``pads.tx``, ``pads.rx``,
``pads.status``); the implementation must drive every ``out`` field. Clock domain: ``sys``.

To add one: create ``impl/<name>.py`` exposing a factory and register it below. Hand-written
(System)Verilog lives in ``rtl/`` and is pulled in with ``platform.add_source(rtl_path(...))``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import stub_migen, stub_rtl

RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"


def rtl_path(filename: str) -> str:
    path = RTL_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    return str(path)


IMPLEMENTATIONS: dict[str, Callable] = {
    "stub_migen": stub_migen.StubMigen,
    "stub_rtl":   stub_rtl.StubRtl,
}
