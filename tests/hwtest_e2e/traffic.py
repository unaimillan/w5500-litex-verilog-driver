"""Counter traffic source for the hardware end-to-end test (Migen).

One chunk = 10 x 32-bit words = 40 bytes (``tx_chunk_bytes`` default):

    SOF "<--|", {16'h0, cnt0}, ..., {16'h0, cnt7}, EOF "|-->"

``cnt`` is a 16-bit counter that advances once per cnt word and continues across chunks, so the
receiver (``receiver.py``) can detect lost, duplicated or reordered chunks. Byte order follows
the core: ``tx_data[31:24]`` is the first payload byte.

Modes (``mode``, latched at chunk boundaries):

    0 off     no traffic
    1 little  one chunk every ``periods[1]`` clk (DE10-Lite: 100 chunks/s)
    2 middle  one chunk every ``periods[2]`` clk (DE10-Lite: 4000 chunks/s, soc_v1 with margin)
    3 full    back-to-back: ``valid`` stays high, the source is throttled by ``ready`` only

In the periodic modes the source behaves like a real-time producer: a chunk that becomes due
while the previous one is still not accepted is dropped (``overruns`` += 1, sticky ``overrun``)
and its 8 counter values are skipped, so the receiver sees the loss as a gap.
"""

from migen import Case, If, Module, Signal

SOF = 0x3C2D2D7C  # "<--|"
EOF = 0x7C2D2D3E  # "|-->"
CNT_WORDS   = 8
CHUNK_WORDS = CNT_WORDS + 2
CHUNK_BYTES = 4 * CHUNK_WORDS

MODE_OFF, MODE_LITTLE, MODE_MIDDLE, MODE_FULL = range(4)
MODES = {"off": MODE_OFF, "little": MODE_LITTLE, "middle": MODE_MIDDLE, "full": MODE_FULL}


def default_periods(clk_freq: int) -> dict[int, int]:
    """clk cycles per chunk of the periodic modes: little 100 /s, middle 4000 /s."""
    return {MODE_LITTLE: clk_freq // 100, MODE_MIDDLE: clk_freq // 4000}


class ChunkGenerator(Module):
    def __init__(self, periods: dict[int, int]):
        # Control.
        self.mode   = Signal(2)  # Requested mode (synchronous to sys).
        self.enable = Signal()   # Core is up (status_ready): no traffic and no overruns before.
        self.clear  = Signal()   # Clear chunks_sent / overruns / overrun.

        # Stream to the core's tx port.
        self.valid = Signal()
        self.ready = Signal()
        self.data  = Signal(32)
        self.last  = Signal()

        # Status.
        self.active_mode = Signal(2)   # Mode of the current / last chunk.
        self.chunks_sent = Signal(32)  # Chunks fully accepted by the core.
        self.overruns    = Signal(16)  # Dropped chunks (periodic modes).
        self.overrun     = Signal()    # Sticky: at least one chunk dropped.

        # # #

        busy = Signal()                    # Emitting a chunk (valid high).
        word = Signal(max=CHUNK_WORDS)     # Word index inside the chunk.
        cnt  = Signal(16)                  # Next counter value.
        skip = Signal(16)                  # Dropped chunks whose counter values are still due.

        # Chunk timer of the periodic modes, free-running per mode.
        timer    = Signal(max=max(periods.values()))
        periodic = (self.mode == MODE_LITTLE) | (self.mode == MODE_MIDDLE)
        tick     = Signal()
        self.comb += tick.eq(periodic & (timer == 0))
        self.sync += If(timer == 0,
            Case(self.mode, {m: timer.eq(p - 1) for m, p in periods.items()} | {
                "default": timer.eq(0)}),
        ).Else(
            timer.eq(timer - 1),
        )

        accept = Signal()
        done   = Signal()  # EOF accepted this cycle.
        free   = Signal()  # No chunk pending after this cycle.
        start  = Signal()
        drop   = Signal()
        self.comb += [
            self.valid.eq(busy),
            self.last.eq(word == CHUNK_WORDS - 1),
            Case(word, {
                0:               self.data.eq(SOF),
                CHUNK_WORDS - 1: self.data.eq(EOF),
                "default":       self.data.eq(cnt),
            }),
            accept.eq(self.valid & self.ready),
            done.eq(accept & self.last),
            free.eq(~busy | done),
            start.eq(free & self.enable & ((self.mode == MODE_FULL) | tick)),
            drop.eq(~free & self.enable & tick),
        ]

        self.sync += [
            If(start,
                busy.eq(1),
                word.eq(0),
                self.active_mode.eq(self.mode),
                cnt.eq(cnt + (skip << 3)),
                skip.eq(0),
            ).Elif(done,
                busy.eq(0),
            ).Elif(accept,
                word.eq(word + 1),
                If(word != 0, cnt.eq(cnt + 1)),
            ),
            If(drop, skip.eq(skip + 1)),
            If(self.clear,
                self.chunks_sent.eq(0),
                self.overruns.eq(0),
                self.overrun.eq(0),
            ).Else(
                If(done, self.chunks_sent.eq(self.chunks_sent + 1)),
                If(drop,
                    self.overruns.eq(self.overruns + 1),
                    self.overrun.eq(1),
                ),
            ),
        ]
