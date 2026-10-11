// Placeholder hand-written implementation of the W5500 UDP core (impl "stub_rtl").
//
// Same port contract as the generated top module (see docs/architecture.md); the build-time
// configuration arrives as parameters from the Migen wrapper (impl/stub_rtl.py). It holds the
// W5500 in reset and keeps every interface idle.

`default_nettype none

// The stub ignores the configuration; real implementations must not need this waiver.
/* verilator lint_off UNUSEDPARAM */
module w5500_udp_stub #(
    parameter integer CLK_FREQ          = 50000000,
    parameter integer SPI_DIV           = 3,         // clk cycles per SCLK half-period.
    parameter integer DATA_W            = 8,
    parameter integer MAX_PAYLOAD       = 1472,
    parameter [47:0]  MAC               = 48'h020000000001,
    parameter [31:0]  IP                = 32'hC0A8010A,
    parameter [31:0]  SUBNET            = 32'hFFFFFF00,
    parameter [31:0]  GATEWAY           = 32'hC0A80101,
    parameter [15:0]  LOCAL_PORT        = 16'd5000,
    parameter [31:0]  REMOTE_IP         = 32'hC0A80164,
    parameter [15:0]  REMOTE_PORT       = 16'd5000,
    parameter integer SOCKET            = 0,
    parameter integer TX_BUF_KB         = 2,
    parameter integer RX_BUF_KB         = 2,
    parameter integer RESET_HOLD_CYCLES = 25000,
    parameter integer INIT_WAIT_CYCLES  = 50000
) (
/* verilator lint_on UNUSEDPARAM */
    input  wire              clk,
    input  wire              rst,

    output wire              w5500_spi_sclk,
    output wire              w5500_spi_cs_n,
    output wire              w5500_spi_mosi,
    input  wire              w5500_spi_miso,
    output wire              w5500_rst_n,
    input  wire              w5500_int_n,

    input  wire              tx_valid,
    output wire              tx_ready,
    input  wire [DATA_W-1:0] tx_data,
    input  wire              tx_last,

    output wire              rx_valid,
    input  wire              rx_ready,
    output wire [DATA_W-1:0] rx_data,
    output wire              rx_last,

    output wire              status_ready,
    output wire              status_error
);

    assign w5500_spi_sclk = 1'b0;
    assign w5500_spi_cs_n = 1'b1;
    assign w5500_spi_mosi = 1'b0;
    assign w5500_rst_n    = 1'b0;

    assign tx_ready       = 1'b0;
    assign rx_valid       = 1'b0;
    assign rx_data        = {DATA_W{1'b0}};
    assign rx_last        = 1'b0;

    assign status_ready   = 1'b0;
    assign status_error   = 1'b0;

    // Inputs intentionally unused by the stub.
    wire unused = &{1'b0, clk, rst, w5500_spi_miso, w5500_int_n,
                    tx_valid, tx_data, tx_last, rx_ready};

endmodule

`default_nettype wire
