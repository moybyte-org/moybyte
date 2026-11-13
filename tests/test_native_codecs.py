"""The shared codecs' guard (docs/refactor_2026-11.md, phase 3).

Our C writes a growing string, base64, hex and UTF-8 through one owner each:

  native/moy_spine/moy_sb.h     the byte builder, grown from a module's
                                allocator or into a caller's buffer
  native/moy_spine/moy_codec.h  base64, base64url, hex and UTF-8, each with
                                one rule

A private helper anywhere else is a second copy of a rule, and copies drift
(three base64 decoders read padding three ways before this). The allowlist
names each exception and why it stays; an entry whose file no longer has the
helper fails too.
"""

import fnmatch
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "native")

# Trees we carry but do not write: their own makers' decisions.
VENDORED = (
    "moy_audio/libmoy/", "moy_gfx/libmoy/", "moy_wasm/wamr/", "moy_lua/lua/",
    "moy_ssh/dropbear/", "moy_elf/elf_loader/", "moy_sh/dash/", "moy_net/mbedtls/",
    "moy_net/monocypher/", "moy_media/minimp3", "p4/moy_dsi/vendor", "moycore/libmoy/",
)

OWNER = "moy_spine/"

# A function defined at file scope whose name says it is a codec.
CODEC_FN = re.compile(
    r"^[A-Za-z_][\w \t\*]*?\b(\w*(?:b64|base64|utf8|unhex|hexval|hex_val|hex_to)\w*)"
    r"\s*\([^;{]*\)\s*\{", re.M)
# A byte builder: a struct whose bytes are a pointer followed by their
# length and capacity, and nothing more of their own.
STRUCT = re.compile(r"typedef\s+struct\s*\w*\s*\{([^{}]*)\}\s*(\w+)\s*;")
BUILDER = re.compile(r"\b(?:char|uint8_t)\s*\*\s*\w+\s*;[ \t]*(?://[^\n]*)?\s*"
                     r"size_t\s+(?:n\s*,\s*cap|cap\s*,\s*n)\s*;")

# (file, name): why it stays.
ALLOWED = {
    ("moy_store/moy_img.c", "b64_ok"):
        "the picture's base64 checked whole inside an escaped JSON string, "
        "over moy_b64_value, before the inflater pulls a byte",
    ("moy_store/moy_img.c", "b64_byte"):
        "the inflater's byte source, decoding as it reads the JSON string",
    ("moy_store/moy_img.c", "b64_fill"):
        "the stream's refill, decoding a run of the value into the inflater's window",
    ("moy_net/moy_webhost.c", "out_t"):
        "the answer's stream, not a builder: a socket or a buffer, chunked and sealed",
    ("moy_app/moy_app_lua.c", "buf_t"):
        "its bytes are a Lua userdata at a fixed stack slot, so a Lua error's "
        "unwind leaves them to the collector instead of leaking a heap block",
    ("moy_media/moy_media.c", "to_utf8"):
        "a tag's text as UTF-8: kept when moy_utf8_valid says it is, else read as "
        "Latin-1 and re-encoded, cut on a character",
    ("moy_net/moy_ota.c", "hex_to"):
        "a NUMBER's hex digits right-aligned into big-endian bytes (an RSA value as "
        "Python's '%x' writes it: no leading zeros, any parity), not a byte string",
}


def _sources():
    for d, _subs, files in os.walk(ROOT):
        rel_d = os.path.relpath(d, ROOT).replace(os.sep, "/") + "/"
        if rel_d.startswith(VENDORED) or "/.staged/" in d or rel_d.startswith(OWNER):
            continue
        for f in files:
            if f.endswith((".c", ".h")):
                rel = (rel_d + f) if rel_d != "./" else f
                with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                    yield rel, fh.read()


def _found():
    out = set()
    for rel, text in _sources():
        for m in CODEC_FN.finditer(text):
            out.add((rel, m.group(1)))
        for m in STRUCT.finditer(text):
            body, name = m.group(1), m.group(2)
            if BUILDER.search(body):
                out.add((rel, name))
    return out


def _allowed(rel, name):
    return any(fnmatch.fnmatch(rel, g) and name == n for g, n in ALLOWED)


def test_no_codec_or_builder_outside_the_spine():
    stray = sorted("%s: %s" % (rel, name) for rel, name in _found()
                   if not _allowed(rel, name))
    assert not stray, ("a private codec or byte builder; take moy_codec.h or "
                       "moy_sb.h (native/moy_spine), or allow it with its why:\n  "
                       + "\n  ".join(stray))


def test_every_allowance_is_still_used():
    found = _found()
    stale = sorted("%s: %s" % k for k in ALLOWED if k not in found)
    assert not stale, "allowed but gone; drop the entry:\n  " + "\n  ".join(stale)


def test_the_owners_hold_the_codecs():
    with open(os.path.join(ROOT, "moy_spine", "moy_codec.h"), encoding="utf-8") as f:
        codec = f.read()
    for name in ("moy_b64_value", "moy_b64_decode", "moy_b64_encode", "moy_hex_value",
                 "moy_hex_decode", "moy_hex_encode", "moy_utf8_next", "moy_utf8_valid",
                 "moy_utf8_whole", "moy_utf8_put", "moy_utf8_count"):
        assert re.search(r"\b%s\s*\(" % name, codec), name
    with open(os.path.join(ROOT, "moy_spine", "moy_sb.h"), encoding="utf-8") as f:
        assert "} moy_sb_t;" in f.read()
