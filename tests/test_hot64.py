"""tools/hot64.py: the board build's guard against new 64-bit helper calls in
the hot C, pinned on objdump text captured from both chips' objects (CI's host
lane has no board objects and no cross objdump)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import hot64  # noqa: E402

# `riscv32-esp-elf-objdump -r -t` of a P4 object, cut down: a call from a
# function in its own section, one from a clone, one from a function that
# shares an IRAM section with another, and a call to something else.
RISCV = """
moy_x.c.obj:     file format elf32-littleriscv

SYMBOL TABLE:
00000000 l    df *ABS*\t00000000 moy_x.c
00000000 l    d  .text.row_paint\t00000000 .text.row_paint
00000000 l     F .text.row_paint\t00000120 row_paint
00000000 l     F .text.bd_read.part.0\t00000040 bd_read.part.0
00000000 g     F .iram1.3\t00000010 first_isr
00000010 g     F .iram1.3\t00000030 second_isr
00000000         *UND*\t00000000 __divdi3
00000000         *UND*\t00000000 __udivdi3


RELOCATION RECORDS FOR [.text.row_paint]:
OFFSET   TYPE              VALUE
00000014 R_RISCV_CALL_PLT  __divdi3
00000014 R_RISCV_RELAX     *ABS*
00000060 R_RISCV_CALL_PLT  __divdi3
00000080 R_RISCV_CALL_PLT  memcpy


RELOCATION RECORDS FOR [.text.bd_read.part.0]:
OFFSET   TYPE              VALUE
00000008 R_RISCV_CALL_PLT  __udivdi3


RELOCATION RECORDS FOR [.iram1.3]:
OFFSET   TYPE              VALUE
00000020 R_RISCV_CALL_PLT  __udivdi3
"""

# `xtensa-esp32s3-elf-objdump -r -t` of an S3 object, cut down: the literal
# pool's word for a callee and the call itself are one call.
XTENSA = """
moy_y.c.obj:     file format elf32-xtensa-le

SYMBOL TABLE:
00000000 l     F .text.mg_blit$constprop$0\t0000009c mg_blit$constprop$0
00000000         *UND*\t00000000 __moddi3


RELOCATION RECORDS FOR [.literal.mg_blit$constprop$0]:
OFFSET   TYPE              VALUE
00000004 R_XTENSA_32       __moddi3


RELOCATION RECORDS FOR [.text.mg_blit$constprop$0]:
OFFSET   TYPE              VALUE
00000003 R_XTENSA_SLOT0_OP  .literal.mg_blit$constprop$0+0x00000004
00000003 R_XTENSA_ASM_EXPAND  __moddi3
"""

MAP = """
Linker script and memory map

 .text.row_paint
                0x40001000       0x120 CMakeFiles/micropython.elf.dir/src/x/firmware/b/native/.staged/moy_gfx/moy_backdrop.c.obj
 .text.f        0x40002000        0x20 CMakeFiles/micropython.elf.dir/src/x/firmware/b/native/.staged/moy_store/moy_fs.c.obj
 .text.g        0x40003000        0x20 CMakeFiles/micropython.elf.dir/src/x/firmware/b/native/.staged/moy_audio/libmoy/moy_audio.c.obj
 .text.h        0x40004000        0x20 esp-idf/main/libmain.a(main.c.obj)
"""


def test_the_functions_an_object_defines_are_read_by_their_source_names():
    assert hot64.defined(RISCV) == {"row_paint", "bd_read", "first_isr", "second_isr"}
    assert hot64.defined(XTENSA) == {"mg_blit"}


def test_each_call_is_counted_once_in_the_function_it_sits_in():
    assert sorted(hot64.calls(RISCV)) == [
        ("bd_read", "__udivdi3"),
        ("row_paint", "__divdi3"),
        ("row_paint", "__divdi3"),
        ("second_isr", "__udivdi3"),
    ]


def test_a_literal_pool_word_is_not_a_second_call():
    assert hot64.calls(XTENSA) == [("mg_blit", "__moddi3")]


def test_the_linked_objects_are_the_maps_and_only_the_hot_ones_are_scanned(tmp_path):
    p = tmp_path / "micropython.map"
    p.write_text(MAP)
    objs = hot64.linked_objects(str(p))
    assert sorted(objs) == ["moy_audio/libmoy/moy_audio.c", "moy_gfx/moy_backdrop.c",
                            "moy_store/moy_fs.c"]
    assert [r for r in sorted(objs) if hot64.is_hot(r)] == [
        "moy_audio/libmoy/moy_audio.c", "moy_gfx/moy_backdrop.c"]


def test_a_new_call_and_a_stale_allowance_both_fail():
    allow = {("riscv", "moy_gfx/a.c", "f", "__divdi3"): "why",
             ("riscv", "moy_gfx/a.c", "gone", "__divdi3"): "why",
             ("riscv", "moy_gfx/unlinked.c", "h", "__divdi3"): "why",
             ("xtensa", "moy_gfx/a.c", "x", "__divdi3"): "why"}
    found = {("moy_gfx/a.c", "f", "__divdi3"): 2, ("moy_gfx/a.c", "g", "__moddi3"): 1}
    funcs = {("moy_gfx/a.c", "f"), ("moy_gfx/a.c", "g"), ("moy_gfx/a.c", "gone")}
    new, stale = hot64.verdict("riscv", found, funcs, allow)
    assert new == [("moy_gfx/a.c", "g", "__moddi3")]
    assert stale == [("moy_gfx/a.c", "gone", "__divdi3")]
    # a build that leaves `gone` out says nothing about its allowance
    assert hot64.verdict("riscv", {("moy_gfx/a.c", "f", "__divdi3"): 1},
                         {("moy_gfx/a.c", "f")}, allow)[1] == []
    assert hot64.verdict("riscv", {("moy_gfx/a.c", "f", "__divdi3"): 1},
                         {("moy_gfx/a.c", "f")}, {k: v for k, v in allow.items()
                                           if k[2] != "gone"}) == ([], [])


def test_every_allowance_names_a_hot_object_a_helper_and_why():
    for (arch, rel, fn, helper), why in hot64.ALLOW.items():
        assert arch in ("xtensa", "riscv") and hot64.is_hot(rel), (rel, fn)
        assert helper in hot64.HELPERS and fn and len(why) > 20, (rel, fn, why)
