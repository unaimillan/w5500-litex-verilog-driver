/* soc_v1 firmware start-up: stack, .data copy, .bss clear, main(). No interrupts/traps. */
    .section .text.start, "ax"
    .global _start
_start:
    la   sp, _stack_top
    la   a0, _data_start
    la   a1, _data_end
    la   a2, _data_load
1:  bgeu a0, a1, 2f
    lw   t0, 0(a2)
    sw   t0, 0(a0)
    addi a0, a0, 4
    addi a2, a2, 4
    j    1b
2:  la   a0, _bss_start
    la   a1, _bss_end
3:  bgeu a0, a1, 4f
    sw   zero, 0(a0)
    addi a0, a0, 4
    j    3b
4:  call main
5:  j    5b
