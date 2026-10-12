import pytest

from w5500_litex_verilog_driver.config import PRESETS, CoreConfig


def test_presets_are_valid():
    assert "default" in PRESETS
    for cfg in PRESETS.values():
        assert isinstance(cfg, CoreConfig)


def test_derived_values():
    cfg = CoreConfig(clk_freq=50_000_000, spi_freq=10_000_000, mac="02:11:22:33:44:55",
                     ip="10.0.0.2")
    assert cfg.spi_div == 3                         # 50 MHz / 6 = 8.33 MHz <= 10 MHz.
    assert cfg.clk_freq / (2 * cfg.spi_div) <= cfg.spi_freq
    assert cfg.mac_int == 0x021122334455
    assert cfg.ip_int == 0x0A000002
    assert cfg.cycles(500) == 25_000
    assert CoreConfig(tx_chunk_bytes=40, tx_chunks_per_datagram=10).tx_datagram_bytes == 400


@pytest.mark.parametrize("changes, message", [
    ({"data_width": 16},               "data_width"),
    ({"spi_freq": 30_000_000},         "clk_freq / 2"),
    ({"ip": "300.1.1.1"},              "ip"),
    ({"mac": "01:00:00:00:00:01"},     "unicast"),
    ({"mac": "02:00:00:00:01"},        "mac"),
    ({"local_port": 0},                "local_port"),
    ({"remote_port": 70000},           "remote_port"),
    ({"socket": 8},                    "socket"),
    ({"tx_buf_kb": 3},                 "tx_buf_kb"),
    ({"tx_buf_kb": 1},                 "max_payload must fit"),
    ({"reset_hold_us": 10},            "reset_hold_us"),
    ({"tx_chunk_bytes": 42},           "tx_chunk_bytes must be a multiple of 4"),
    ({"tx_chunks_per_datagram": 0},    "tx_chunks_per_datagram must be >= 1"),
    ({"tx_chunks_per_datagram": 37},   "must be <= max_payload"),
])
def test_invalid(changes, message):
    with pytest.raises(ValueError, match=message):
        CoreConfig(**changes)


def test_frozen():
    with pytest.raises(AttributeError):
        CoreConfig().ip = "1.2.3.4"
