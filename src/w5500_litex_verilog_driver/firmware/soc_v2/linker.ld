/* soc_v2 firmware: code + constants in the integrated ROM, data/bss/stack in the SRAM.
 * regions.ld (MEMORY rom/sram) is generated next to the build by build_firmware (impl/soc_v1.py). */
OUTPUT_ARCH(riscv)
ENTRY(_start)
INCLUDE regions.ld

SECTIONS
{
    .text : {
        KEEP(*(.text.start))
        *(.text .text.*)
        *(.rodata .rodata.* .srodata .srodata.*)
        . = ALIGN(4);
    } > rom

    .data : {
        _data_start = .;
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4);
        _data_end = .;
    } > sram AT > rom
    _data_load = LOADADDR(.data);

    .bss (NOLOAD) : {
        _bss_start = .;
        *(.bss .bss.* .sbss .sbss.* COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > sram

    _stack_top = ORIGIN(sram) + LENGTH(sram);

    /DISCARD/ : { *(.comment) *(.riscv.attributes) *(.note*) *(.eh_frame*) }
}
