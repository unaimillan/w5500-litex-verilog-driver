from pathlib import Path

import pytest

from w5500_litex_verilog_driver.cli import generate
from w5500_litex_verilog_driver.config import PRESETS
from w5500_litex_verilog_driver.impl import unsupported_reason

from .common import BUILD_DIR, TOP


@pytest.fixture(scope="session")
def generated():
    """``generated(preset, impl) -> Path`` of the single-file Verilog, cached per session.

    Skips the calling test when the implementation does not support the preset.
    """
    cache = {}

    def get(preset: str, impl: str) -> Path:
        if reason := unsupported_reason(impl, PRESETS[preset]):
            pytest.skip(reason)
        key = (preset, impl)
        if key not in cache:
            out = BUILD_DIR / f"{preset}-{impl}" / "gateware"
            cache[key] = Path(generate(PRESETS[preset], impl, TOP, str(out)))
        return cache[key]

    return get
