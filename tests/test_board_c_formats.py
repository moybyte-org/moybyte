"""A board's C library is newlib nano (CONFIG_NEWLIB_NANO_FORMAT on every
board): its printf and scanf families take no `long long` length (%lld,
%llu, %jd, PRId64). Such a conversion does not fail, it reads the varargs
out of step, and the next %s dereferences a stray word: the sign-in's key
file panicked a P4 that way on its first pairing. So no format string in the
C a board compiles carries one; a 64-bit value is written by hand (moy_seed.c,
moy_sh_store.c, moy_webauth.c's dec64) and read with strtoll.

Vendored trees, the host harnesses, the wasm-only sources (glibc, musl) and
the shell's sources under its POSIX shim (native/moy_sh/micropython.cmake's
MOY_SH_DASH_SRCS, whose vsnprintf is moy_sh_fmt.c's, every length) are
outside it."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native"

# Vendored: their own tracing is compiled out, and an edit there is a re-vendor.
VENDORED = ("moy_net/mbedtls/", "moy_ssh/dropbear/", "moy_wasm/", "moycore/lua/",
            "moy_sh/dash/")
# Compiled only off a board: the host's glibc, the browser's musl, the fuzzers.
OFF_BOARD = re.compile(r"(^|/)host/|_host\.c$|_web\.c$|(^|/)fuzz_[^/]*\.c$")



def shimmed():
    """The shell's sources compiled under moy_sh_posix.h, as its cmake lists them."""
    text = (NATIVE / "moy_sh" / "micropython.cmake").read_text()
    block = re.search(r"list\(APPEND MOY_SH_DASH_SRCS(.*?)\)", text, re.S).group(1)
    assert "file(GLOB MOY_SH_DASH_SRCS ${MOY_SH}/dash/*.c)" in text
    return {"moy_sh/" + m for m in re.findall(r"\$\{MOY_SH\}/(\S+\.c)", block)}


STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
WIDE = re.compile(r"%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:ll|j|L|q)[diouxXn]")
PRI64 = re.compile(r"\b(?:PRI|SCN)[diouxX](?:64|MAX|LEAST64|FAST64)\b")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def board_sources():
    shim = shimmed()
    for path in sorted(NATIVE.rglob("*.c")):
        rel = path.relative_to(NATIVE).as_posix()
        if rel.startswith(VENDORED) or OFF_BOARD.search(rel) or rel in shim:
            continue
        yield rel, path


def test_no_board_c_formats_a_long_long():
    found = []
    for rel, path in board_sources():
        text = COMMENT.sub("", path.read_text(errors="replace"))
        for n, line in enumerate(text.splitlines(), 1):
            if PRI64.search(line) or any(WIDE.search(s) for s in STRING.findall(line)):
                found.append("native/%s:%d: %s" % (rel, n, line.strip()))
    assert not found, "a long long conversion in a board's C (newlib nano):\n" + "\n".join(found)


def test_the_guard_sees_the_board_tree():
    names = {rel for rel, _ in board_sources()}
    assert "moy_net/moy_webauth.c" in names and "moy_net/modmoy_net.c" in names
    assert "moy_net/moy_seal_web.c" not in names
    assert "moy_sh/moy_sh_cmds.c" not in names and "moy_sh/moy_sh_store.c" in names
