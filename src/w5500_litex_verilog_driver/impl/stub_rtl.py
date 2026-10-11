"""Hand-written RTL placeholder (rtl/w5500_udp_stub.v) wrapped with a Migen ``Instance``.

Template for RTL-based implementations: the build-time config reaches the Verilog module as
parameters, and the file is embedded into the generated output by ``VirtualPlatform``.
"""

from litex.gen import LiteXModule
from migen import ClockSignal, Constant, Instance, ResetSignal


def verilog_params(cfg) -> dict:
    """CoreConfig -> ``p_*`` Instance parameters.

    Values are passed as sized constants (Migen otherwise sizes them to the minimum width):
    32 bits for ``parameter integer`` ones, the exact width for addresses/ports/MAC.
    """
    def i32(v):
        return Constant(v, 32)

    return {
        "p_CLK_FREQ":          i32(cfg.clk_freq),
        "p_SPI_DIV":           i32(cfg.spi_div),
        "p_DATA_W":            i32(cfg.data_width),
        "p_MAX_PAYLOAD":       i32(cfg.max_payload),
        "p_MAC":               Constant(cfg.mac_int, 48),
        "p_IP":                Constant(cfg.ip_int, 32),
        "p_SUBNET":            Constant(cfg.subnet_int, 32),
        "p_GATEWAY":           Constant(cfg.gateway_int, 32),
        "p_LOCAL_PORT":        Constant(cfg.local_port, 16),
        "p_REMOTE_IP":         Constant(cfg.remote_ip_int, 32),
        "p_REMOTE_PORT":       Constant(cfg.remote_port, 16),
        "p_SOCKET":            i32(cfg.socket),
        "p_TX_BUF_KB":         i32(cfg.tx_buf_kb),
        "p_RX_BUF_KB":         i32(cfg.rx_buf_kb),
        "p_RESET_HOLD_CYCLES": i32(cfg.cycles(cfg.reset_hold_us)),
        "p_INIT_WAIT_CYCLES":  i32(cfg.cycles(cfg.init_wait_us)),
    }


class StubRtl(LiteXModule):
    def __init__(self, platform, cfg, pads):
        from . import rtl_path

        platform.add_source(rtl_path("w5500_udp_stub.v"))
        self.specials += Instance("w5500_udp_stub",
            **verilog_params(cfg),
            i_clk            = ClockSignal("sys"),
            i_rst            = ResetSignal("sys"),
            o_w5500_spi_sclk = pads.w5500.spi_sclk,
            o_w5500_spi_cs_n = pads.w5500.spi_cs_n,
            o_w5500_spi_mosi = pads.w5500.spi_mosi,
            i_w5500_spi_miso = pads.w5500.spi_miso,
            o_w5500_rst_n    = pads.w5500.rst_n,
            i_w5500_int_n    = pads.w5500.int_n,
            i_tx_valid       = pads.tx.valid,
            o_tx_ready       = pads.tx.ready,
            i_tx_data        = pads.tx.data,
            i_tx_last        = pads.tx.last,
            o_rx_valid       = pads.rx.valid,
            i_rx_ready       = pads.rx.ready,
            o_rx_data        = pads.rx.data,
            o_rx_last        = pads.rx.last,
            o_status_ready   = pads.status.ready,
            o_status_error   = pads.status.error,
        )
