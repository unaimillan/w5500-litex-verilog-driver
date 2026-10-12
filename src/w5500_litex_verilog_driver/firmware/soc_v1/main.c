// soc_v1 firmware: bring up the W5500, open one UDP socket, then forward fixed-size frames from
// TxFramePort into the socket TX buffer and SEND each one. Any failure is sticky (fail()).
//
// generated/csr.h (CSR map), generated/config.h (CoreConfig + W5500 constants) are written by
// impl/soc_v1.py at generation time. SPI: LiteSPIMaster, one `rxtx` write = one transfer of
// `len` bits (MSB first); every transfer returns a word that must be popped from `rxtx`.

#include <stdint.h>

#include <generated/config.h>
#include <generated/csr.h>

#define REG(addr) (*(volatile uint32_t *)(addr))

#define FRAME_WAIT REG(FRAME_BASE + 0)  // Stalls until FRAME_WORDS words are buffered.
#define FRAME_DATA REG(FRAME_BASE + 4)  // Pops one 32-bit word.

#define SPI_TX_READY (1u << CSR_SPI_STATUS_TX_READY_OFFSET)
#define SPI_RX_READY (1u << CSR_SPI_STATUS_RX_READY_OFFSET)

// Timing (rdcycle: FemtoRV32 quark cycle counter, 32 bits) -------------------------------------

static inline uint32_t now(void) {
    uint32_t c;
    __asm__ volatile("rdcycle %0" : "=r"(c));
    return c;
}

static void delay(uint32_t cycles) {
    uint32_t t0 = now();
    while (now() - t0 < cycles)
        ;
}

static void __attribute__((noreturn)) fail(void) {
    REG(CSR_W5500_ERROR_ADDR) = 1;  // Sticky in hardware, also forces status_ready low.
    for (;;)
        ;
}

// SPI ------------------------------------------------------------------------------------------

static uint32_t spi_len;

static void spi_push(uint32_t bits, uint32_t word) {
    if (bits != spi_len) {  // Sampled per transfer, so it may change between pushes.
        REG(CSR_SPI_PHYCONFIG_ADDR) = (bits << CSR_SPI_PHYCONFIG_LEN_OFFSET) |
                                      (1u << CSR_SPI_PHYCONFIG_WIDTH_OFFSET) |
                                      (1u << CSR_SPI_PHYCONFIG_MASK_OFFSET);
        spi_len = bits;
    }
    while (!(REG(CSR_SPI_STATUS_ADDR) & SPI_TX_READY))
        ;
    REG(CSR_SPI_RXTX_ADDR) = word;
}

static uint32_t spi_pop(void) {
    while (!(REG(CSR_SPI_STATUS_ADDR) & SPI_RX_READY))
        ;
    return REG(CSR_SPI_RXTX_ADDR);
}

static uint32_t spi_xfer(uint32_t bits, uint32_t word) {
    spi_push(bits, word);
    return spi_pop();
}

// CS low + 3-byte address/control phase (VDM).
static void spi_begin(uint32_t bsb, uint32_t addr, uint32_t ctrl) {
    REG(CSR_SPI_CS_ADDR) = 1;
    spi_xfer(24, (addr << 8) | (bsb << 3) | ctrl);
}

static void spi_end(void) {
    REG(CSR_SPI_CS_ADDR) = 0;
}

// W5500 register access (VDM bursts, up to 4 bytes per SPI transfer) --------------------------

static void w_write(uint32_t bsb, uint32_t addr, const uint8_t *buf, uint32_t n) {
    spi_begin(bsb, addr, W_CTRL_WRITE);
    while (n) {
        uint32_t k = n > 4 ? 4 : n, word = 0;
        for (uint32_t i = 0; i < k; i++)
            word = (word << 8) | *buf++;
        spi_xfer(k << 3, word);
        n -= k;
    }
    spi_end();
}

static void w_read(uint32_t bsb, uint32_t addr, uint8_t *buf, uint32_t n) {
    spi_begin(bsb, addr, W_CTRL_READ);
    while (n) {
        uint32_t k = n > 4 ? 4 : n;
        uint32_t word = spi_xfer(k << 3, 0);  // MOSI content during reads: see w5500-notes.
        for (uint32_t i = 0; i < k; i++)
            *buf++ = word >> ((k - 1 - i) << 3);
        n -= k;
    }
    spi_end();
}

static void w8(uint32_t bsb, uint32_t addr, uint8_t value) {
    w_write(bsb, addr, &value, 1);
}

static uint8_t r8(uint32_t bsb, uint32_t addr) {
    uint8_t value;
    w_read(bsb, addr, &value, 1);
    return value;
}

static void w16(uint32_t bsb, uint32_t addr, uint32_t value) {
    uint8_t buf[2] = {value >> 8, value};
    w_write(bsb, addr, buf, 2);
}

static uint32_t r16(uint32_t bsb, uint32_t addr) {
    uint8_t buf[2];
    w_read(bsb, addr, buf, 2);
    return ((uint32_t)buf[0] << 8) | buf[1];
}

// 16-bit counters updated by the chip: read until two consecutive reads agree (datasheet).
static uint32_t r16_stable(uint32_t bsb, uint32_t addr) {
    uint32_t a = r16(bsb, addr), b;
    while ((b = r16(bsb, addr)) != a)
        a = b;
    return a;
}

// Write, read back, fail on mismatch.
static void w_write_check(uint32_t bsb, uint32_t addr, const uint8_t *buf, uint32_t n) {
    uint8_t back[18];
    w_write(bsb, addr, buf, n);
    w_read(bsb, addr, back, n);
    for (uint32_t i = 0; i < n; i++)
        if (back[i] != buf[i])
            fail();
}

// Poll until (reg & mask) == value, fail after `timeout` cycles.
static void wait_reg(uint32_t bsb, uint32_t addr, uint8_t mask, uint8_t value, uint32_t timeout) {
    uint32_t t0 = now();
    while ((r8(bsb, addr) & mask) != value)
        if (now() - t0 > timeout)
            fail();
}

static void socket_command(uint8_t cmd) {
    w8(W_BSB_SREG, W_Sn_CR, cmd);
    wait_reg(W_BSB_SREG, W_Sn_CR, 0xff, 0, CMD_TIMEOUT_CYCLES);  // Accepted (not completed).
}

// Bring-up -------------------------------------------------------------------------------------

static void w5500_init(void) {
    static const uint8_t udp = W_Sn_MR_UDP, rxbuf = RX_BUF_KB, txbuf = TX_BUF_KB;

    REG(CSR_W5500_RST_N_ADDR) = 0;
    delay(RESET_HOLD_CYCLES);
    REG(CSR_W5500_RST_N_ADDR) = 1;
    delay(INIT_WAIT_CYCLES);

    if (r8(W_BSB_COMMON, W_VERSIONR) != W_VERSION)
        fail();

    w_write_check(W_BSB_COMMON, W_GAR, NET_CFG, sizeof NET_CFG);  // GAR, SUBR, SHAR, SIPR.

    w_write_check(W_BSB_SREG, W_Sn_MR, &udp, 1);
    w_write_check(W_BSB_SREG, W_Sn_PORT, SOCK_PORT, sizeof SOCK_PORT);
    w_write_check(W_BSB_SREG, W_Sn_DIPR, SOCK_DEST, sizeof SOCK_DEST);  // + Sn_DPORT.
    w_write_check(W_BSB_SREG, W_Sn_RXBUF_SIZE, &rxbuf, 1);
    w_write_check(W_BSB_SREG, W_Sn_TXBUF_SIZE, &txbuf, 1);

    wait_reg(W_BSB_COMMON, W_PHYCFGR, W_PHYCFGR_LNK, W_PHYCFGR_LNK, LINK_TIMEOUT_CYCLES);

    socket_command(W_Sn_CR_OPEN);
    wait_reg(W_BSB_SREG, W_Sn_SR, 0xff, W_SOCK_UDP, CMD_TIMEOUT_CYCLES);
}

// One frame: FIFO -> socket TX buffer (one VDM burst at Sn_TX_WR) -> SEND -> SENDOK -----------

static void send_frame(void) {
    uint32_t t0 = now();
    while (r16_stable(W_BSB_SREG, W_Sn_TX_FSR) < FRAME_BYTES)
        if (now() - t0 > CMD_TIMEOUT_CYCLES)
            fail();

    uint32_t wr = r16(W_BSB_SREG, W_Sn_TX_WR);

    // Keep one transfer queued ahead (LiteSPIMaster TX/RX FIFOs are 2 deep).
    spi_begin(W_BSB_STX, wr, W_CTRL_WRITE);
    spi_push(32, FRAME_DATA);
    for (uint32_t i = 1; i < FRAME_WORDS; i++) {
        spi_push(32, FRAME_DATA);
        spi_pop();
    }
    spi_pop();
    spi_end();

    w16(W_BSB_SREG, W_Sn_TX_WR, (wr + FRAME_BYTES) & 0xffff);
    socket_command(W_Sn_CR_SEND);

    t0 = now();
    for (;;) {
        uint8_t ir = r8(W_BSB_SREG, W_Sn_IR);
        if (ir & W_Sn_IR_TIMEOUT)  // ARP for the destination failed.
            fail();
        if (ir & W_Sn_IR_SENDOK)
            break;
        if (now() - t0 > SEND_TIMEOUT_CYCLES)
            fail();
    }
    w8(W_BSB_SREG, W_Sn_IR, W_Sn_IR_SENDOK);  // Write 1 to clear.
}

int main(void) {
    w5500_init();
    REG(CSR_W5500_READY_ADDR) = 1;
    for (;;) {
        (void)FRAME_WAIT;  // Bus stall until the user has handed over a whole frame.
        send_frame();
    }
}
