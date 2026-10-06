"""`tools/link_providers.py`: no Rust object in an image provides a C library
name, read from the link map in both formats every image is linked with.

A Rust staticlib's bundled compiler_builtins (and compiler-rt's C objects on
some targets) silently replaced about forty libm functions on a board image
(#224); the guard runs after every board, browser and desktop link. These pin
its parse on both map formats, what counts as a Rust object, what a Rust object
may define, and that every build runs it.
"""

import os
import re

from tools import link_providers as lp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUST = "/w/.build/moy_index_rust/esp32s3/out/libmoy_index_rs.a"
CB = "compiler_builtins-1a2b3c.compiler_builtins.4d5e6f-cgu.105.rcgu.o"
OWN = "moy_index_rs-9f8e7d.moy_index_rs.6c5b4a-cgu.0.rcgu.o"

# GNU ld's memory map: an input section line names the file, and the global
# symbols it defines follow it; a long section name wraps its address and file
# onto the next line.
GNU = """Archive member included to satisfy reference by file (symbol)

%(rust)s(%(cb)s)
                              x.o (sinf)

Discarded input sections

 .text.cosf     0x00000000       0x20 %(rust)s(%(cb)s)

Linker script and memory map

LOAD %(rust)s
 .flash.text    0x42000020   0x123456
 *(.text .text.*)
 .text.sinf     0x42000020       0x58 %(sinf)s
                0x42000020                sinf
 *fill*         0x42000078        0x8
 .text.moy_index_intern
                0x42000080      0x2f0 %(rust)s(%(own)s)
                0x42000080                moy_index_intern
                0x42000370                _RNvCs1_12moy_index_rs4copy
 .text.__popcountsi2
                0x42000400       0x30 %(popc)s
                0x42000400                __popcountsi2
 .text.mp_init  0x42000430       0x40 esp-idf/main/libmain.a(main.c.obj)
                0x42000430                mp_init
                0x42000470                _etext = ABSOLUTE (.)
                0x42000470                PROVIDE (memcpy = 0x40001234)

Cross Reference Table

sinf                                              %(rust)s(%(cb)s)
"""

# wasm-ld's map: Out rows, In rows (file:(name)) and Symbol rows, by column.
WASM = """    Addr      Off     Size Out     In      Symbol
       -        8      2a1 TYPE
       -     2c9d   123456 CODE
       -     2c9d       ad         build-moybyte/moy_audio/modmoy_audio.o:(mod_snd_counts)
       -     2c9d       ad                 mod_snd_counts
       -     2d4a       3b         %(rust)s(%(cb)s):(strlen)
       -     2d4a       3b                 strlen
       -     2d85      400         %(rust)s(%(own)s):(moy_index_find)
       -     2d85      400                 moy_index_find
       -     3185       40         %(rust)s(%(own)s):(_RNvMs_12moy_index_rs5Index6lookup)
       -     3185       40                 moy_index_rs::Index::lookup
       -     31c5       10         /emsdk/lib/libc.a(sqrtf.o):(sqrtf)
       -     31c5       10                 sqrtf
     400      400       10 DATA
     400      400       10         %(rust)s(%(own)s):(.rodata..Lanon.8b6b.1)
     400      400       10                 .Lanon.8b6b.1
"""


def write(tmp_path, name, text, **kw):
    p = tmp_path / name
    p.write_text(text % dict(rust=RUST, cb=CB, own=OWN, **kw))
    return str(p)


def gnu(tmp_path, sinf, popc, name="a.map"):
    return write(tmp_path, name, GNU, sinf=sinf, popc=popc)


LIBM = "/idf/newlib/libm_nano.a(libm_a-sf_sin.o)"
LIBGCC = "/idf/gcc/libgcc.a(_popcountsi2.o)"


def test_a_clean_image_passes_and_names_each_provider(tmp_path):
    m = gnu(tmp_path, LIBM, LIBGCC)
    prov = lp.providers(m)
    assert prov["sinf"] == LIBM
    assert prov["moy_index_intern"] == "%s(%s)" % (RUST, OWN)
    assert prov["mp_init"] == "esp-idf/main/libmain.a(main.c.obj)"
    assert "_etext" not in prov and "memcpy" not in prov
    assert lp.swaps(prov) == []
    assert lp.check([m]) == 0


def test_compiler_builtins_providing_libm_fails_the_gnu_image(tmp_path, capsys):
    m = gnu(tmp_path, "%s(%s)" % (RUST, CB), LIBGCC)
    assert lp.swaps(lp.providers(m)) == [("sinf", "%s(%s)" % (RUST, CB))]
    assert lp.check([m]) == 1
    out = capsys.readouterr().out
    assert "sinf" in out and "libmoy_index_rs.a(compiler_builtins-" in out


def test_compiler_rt_c_objects_in_a_rust_archive_count_as_rust(tmp_path):
    # rustc bundles compiler-rt's C under hashed names: not *.rcgu.o, but the
    # archive holds one, so it is a Rust archive.
    popc = "%s(45c91108d938afe8-popcountsi2.o)" % RUST
    m = gnu(tmp_path, LIBM, popc)
    assert lp.swaps(lp.providers(m)) == [("__popcountsi2", popc)]


def test_the_diff_names_the_symbols_that_changed_provider(tmp_path):
    base = lp.providers(gnu(tmp_path, LIBM, LIBGCC, "base.map"))
    new = lp.providers(gnu(tmp_path, "%s(%s)" % (RUST, CB), LIBGCC, "new.map"))
    rows = lp.diff(base, new)
    assert [r[0] for r in rows] == ["sinf"]
    assert rows[0][1] == "libm_nano.a(libm_a-sf_sin.o)"
    assert rows[0][2].startswith("libmoy_index_rs.a(compiler_builtins-")


def test_the_wasm_map_is_read_by_column(tmp_path):
    m = write(tmp_path, "web.map", WASM)
    prov = lp.providers(m)
    assert prov["mod_snd_counts"] == "build-moybyte/moy_audio/modmoy_audio.o"
    assert prov["sqrtf"] == "/emsdk/lib/libc.a(sqrtf.o)"
    assert prov["moy_index_rs::Index::lookup"] == "%s(%s)" % (RUST, OWN)
    assert lp.swaps(prov) == [("strlen", "%s(%s)" % (RUST, CB))]


def test_what_a_rust_object_may_define():
    own = ["moy_index_new", "_ZN4core9panicking5panic17h0123456789abcdefE",
           "_RNvCs1_7___rustc12___rust_alloc", "__rust_alloc", "rust_eh_personality",
           "DW.ref.rust_eh_personality", ".Lanon.8b6b.1",
           "compiler_builtins::int::udiv::__udivti3"]
    c = ["memcpy", "sinf", "__popcountsi2", "__udivdi3", "strlen", "_Reent"]
    prov = {s: "%s(%s)" % (RUST, OWN) for s in own + c}
    assert sorted(s for s, _ in lp.swaps(prov)) == sorted(c)
    assert lp.swaps(prov, allow=("moy_", "mem")) == sorted(
        (s, "%s(%s)" % (RUST, OWN)) for s in c if not s.startswith("mem"))


def test_every_image_build_runs_the_guard():
    """The boards', the browser's and the desktop MicroPython's links."""
    lib = open(os.path.join(ROOT, "tools", "esp32_build_lib.sh")).read()
    assert re.search(r'link_providers\.py" check \\\n\s+"\$\{bout\}/micropython\.map"'
                     r' \|\| exit 1', lib)
    web = open(os.path.join(ROOT, "firmware", "web_runner", "build.sh")).read()
    assert "JSFLAGS += -Wl,--Map=$(BUILD)/micropython.map" in web
    assert 'link_providers.py" check \\\n    "${PORT_DIR}/build-moybyte/micropython.map"' in web
    mk = open(os.path.join(ROOT, "Makefile")).read()
    for b in ("build-moybyte", "build-moybyte-board"):
        assert "link_providers.py check $(UNIX_MP_SRC)/ports/unix/%s/micropython.map" % b in mk
