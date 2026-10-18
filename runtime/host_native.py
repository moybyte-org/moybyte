"""The host's ONE native library: every native module the console runs,
compiled into one shared object and loaded once, as a board links them into
one image.

On a board the shell's `moy_ui` (its theme, depth, fonts, the cart-text
boundary), a Lua run's UI rows and a compiled run's are one set of globals,
because they are one firmware. The host matches that by construction: each
binding (`gfx_binding`, `glass_binding`, `lua_binding` with the compiled-cart
run, `moy_input`, `moy_loop`, `moy_play`, `net_binding`, the spine's, the
store's and the index's components in tools/moy_index_spike.py, the
user-files layer) declares its sources as a `native_build.Part`, `build()`
compiles the union into `.build/host/moyhost-<hash>.so`, and every binding
ctypes-loads that one path -- the dynamic loader hands each the same handle,
so a state one sets is the state every other reads. There is no state to copy
across and no forwarder between libraries.

Two parts are taken only where their prerequisites are: the compiled-cart run
(runtime/moyhost_wasm.c and native/moy_wasm) where WAMR builds for the host
(runtime/wasm_binding.wamr), and the user-files layer and the C image decoder
(native/moy_store's moy_ufiles, moy_img) where the desktop MicroPython's tree
has the inflater (`make unix-micropython`). Without the latter a Lua run's
paint image decodes through the Python codec (runtime/moyhost_lua.c), as it
always could.

What stays a library of its own, each saying why in its module: the audio
(runtime/audio_binding.py, compiled double-widened for the parity harness;
the Player reaches its sessions through hl_kernel's audio slots), and the
builds that exist to be DIFFERENT from this one -- the tick model at double
and fixed precision (runtime/moy_play.py), a sanitizer build
(tools/moy_index_spike.py), a style edited under test
(glass_binding.build(ui_dir=...)) and a test's own harness.
"""

import os
import sys

try:                                    # the host's runtime package
    from . import native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import native_build

CACHE = os.path.join(native_build.ROOT, ".build", "host")

# One flag set for every part, as an image has one: libmoy's pixel is the
# boards' RGB565; MOY_WITH_LUA compiles libmoy's Lua binding; MOY_PLAY_HOST
# gives the kernel's Player its sessions side by side; MOYCORE_UI the Lua
# face of the public UI API.
CFLAGS = native_build.BASE_CFLAGS + [
    "-DMOY_PIXEL_RGB565=1", "-DMOY_WITH_LUA=1", "-DMOY_PLAY_HOST=1", "-DMOYCORE_UI=1",
    "-Wno-double-promotion", "-Wno-float-conversion",
]

_PATH = [None]


def _module(name):
    if native_build.ROOT not in sys.path:
        sys.path.insert(0, native_build.ROOT)
    import importlib
    return importlib.import_module(name)


def parts(verbose=False):
    """([Part], extra cflags, link flags): what the library is made of here."""
    out = [_module("runtime." + m).PART for m in (
        "gfx_binding", "glass_binding", "lua_binding", "moy_input", "moy_loop",
        "moy_play", "net_binding")]
    spike = _module("tools.moy_index_spike")
    out += [spike.part(c) for c in (spike.SPINE, spike.FS, spike.INDEX)]
    out.append(_module("runtime.moy_play").FILES_PART)
    cflags, link = [], ["-lm"]
    uf = _module("tools.moy_ufiles_binding")
    if os.path.isdir(uf.UZLIB):
        out += [uf.part(), _module("runtime.moy_play").IMG_PART]
        cflags.append("-DMOYHOST_IMG_C=1")
    wasm = _module("runtime.wasm_binding")
    got = wasm.wamr(verbose) if wasm.vendored() else None
    if got is not None:
        inc, lib = got
        out.append(_module("runtime.lua_binding").WASM_PART)
        cflags += ["-DMOY_WASM=1", "-isystem", inc, "-DMOYHOST_WAMR_PIN=%s" % wasm.pin()]
        link = [lib, "-lm", "-lpthread", "-ldl"]
    return out, cflags, link


def build(verbose=False):
    """The one library's path (built once per content), or None with no C
    compiler. A process asks once; every binding loads what this answers."""
    if _PATH[0] is None:
        ps, cflags, link = parts(verbose)
        _PATH[0] = native_build.build_parts("moyhost", ps, CACHE, cflags=CFLAGS + cflags,
                                            link_flags=link, verbose=verbose) or False
    return _PATH[0] or None

