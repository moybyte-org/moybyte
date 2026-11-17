"""The host's ONE native library: every native module the console runs,
compiled into one shared object and loaded once, as a board links them into
one image.

On a board the shell's `moy_ui` (its theme, depth, fonts, the cart-text
boundary), a Lua run's UI rows and a compiled run's are one set of globals,
because they are one firmware. The host matches that by construction: each
binding (`gfx_binding`, `glass_binding`, `lua_binding` with the compiled-cart
run, `moy_input`, `moy_loop`, `moy_play`, `net_binding`, the spine's, the
store's and the index's components in runtime/host/native_parts.py, the
user-files layer) declares its sources as a `native_build.Part`, `build()`
compiles the union into `.build/host/moyhost-<hash>.so`, and every binding
ctypes-loads that one path -- the dynamic loader hands each the same handle,
so a state one sets is the state every other reads. There is no state to copy
across and no forwarder between libraries.

The C shell (native/moy_sh) is the one part that is not a Part: dash's
sources compile under a shim of their own, so runtime/host/moy_sh.py builds
them as a static archive, as a board builds its own static library, and the
library links it whole.

Two parts are taken only where their prerequisites are: the compiled-cart run
(runtime/host/moyhost_wasm.c and native/moy_wasm) where WAMR builds for the host
(runtime/wasm_binding.wamr), and the user-files layer and the C image decoder
(native/moy_store's moy_ufiles, moy_img) where the desktop MicroPython's tree
has the inflater (`make unix-micropython`). Without the latter a Lua run's
paint image decodes through the Python codec (runtime/host/moyhost_lua.c), as it
always could. MOYHOST_NO_INFLATER=1 builds without it where the tree is, as a
machine with none does (CI's browser-console and board builds):
tests/test_host_no_inflater.py holds every row a binding loads unconditionally
to that build.

What stays a library of its own, each saying why in its module: the audio
(runtime/host/audio_binding.py, compiled double-widened for the parity harness;
the Player reaches its sessions through hl_kernel's audio slots), and the
builds that exist to be DIFFERENT from this one -- the tick model at double
and fixed precision (runtime/host/moy_play.py), a sanitizer build
(tools/native_check.py), a style edited under test
(glass_binding.build(ui_dir=...)) and a test's own harness.
"""

import os
import sys

import native_build

CACHE = os.path.join(native_build.ROOT, ".build", "host")

# One flag set for every part, as an image has one: libmoy's pixel is the
# boards' RGB565; MOY_WITH_LUA compiles libmoy's Lua binding; MOY_PLAY_HOST
# gives the kernel's Player its sessions side by side; MOYCORE_UI the Lua
# face of the public UI API.
CFLAGS = native_build.BASE_CFLAGS + [
    "-DMOY_PIXEL_RGB565=1", "-DMOY_WITH_LUA=1", "-DMOY_PLAY_HOST=1", "-DMOYCORE_UI=1",
    # The web host's folders are a developer machine's: a tmp_path under a
    # long TMPDIR runs past a board's 96 bytes.
    "-DMOY_WEB_PATH_MAX=4096",
    "-Wno-double-promotion", "-Wno-float-conversion",
    # Newer compilers (GCC 14, CI's) refuse an implicit declaration; older
    # ones only warn, so a missing feature macro passed here and failed there.
    "-Werror=implicit-function-declaration",
]

# MOYHOST_ASAN=1 builds the one library under AddressSanitizer (a library of
# its own content hash): the process then runs with libasan preloaded
# (tools/asan_pytest.sh runs pytest so).
ASAN = ["-fsanitize=address", "-fno-omit-frame-pointer", "-g"]

_PATH = [None]


def _module(name):
    if native_build.ROOT not in sys.path:
        sys.path.insert(0, native_build.ROOT)
    import importlib
    return importlib.import_module(name)


def inflater(want=None):
    """Whether the library takes the inflater's part: where the desktop
    MicroPython's tree is and `want` (default: MOYHOST_NO_INFLATER unset)."""
    if want is None:
        want = not os.environ.get("MOYHOST_NO_INFLATER")
    return bool(want) and os.path.isdir(_module("runtime.moy_ufiles_binding").UZLIB)


def parts(verbose=False, with_inflater=None):
    """([Part], extra cflags, link flags): what the library is made of here."""
    out = [_module("runtime." + m).PART for m in (
        "gfx_binding", "glass_binding", "lua_binding", "moy_input", "moy_lease", "moy_http", "moy_mic", "moy_lora", "moy_gatt", "moy_usb", "moy_midi", "moy_media", "moy_loop", "moy_evq",
        "moy_play", "net_binding", "elf_binding")]
    np = _module("runtime.native_parts")
    out += [np.part(c) for c in (np.SPINE, np.FS, np.INDEX)]
    out.append(_module("runtime.moy_play").FILES_PART)
    cflags, link = [], ["-lm", "-lpthread", "-ldl"]
    if inflater(with_inflater):
        out += [_module("runtime.moy_ufiles_binding").part(), _module("runtime.moy_play").IMG_PART]
        cflags.append("-DMOYHOST_IMG_C=1")
    wasm = _module("runtime.wasm_binding")
    got = wasm.wamr(verbose) if wasm.vendored() else None
    if got is not None:
        inc, lib = got
        out.append(_module("runtime.lua_binding").WASM_PART)
        cflags += ["-DMOY_WASM=1", "-isystem", inc, "-DMOYHOST_WAMR_PIN=%s" % wasm.pin()]
        link = [lib, "-lm", "-lpthread", "-ldl"]
    sh = _module("runtime.moy_sh").archive(ASAN if os.environ.get("MOYHOST_ASAN") else (),
                                           verbose)
    if sh is not None:
        link = link + ["-Wl,--whole-archive", sh, "-Wl,--no-whole-archive"]
    seal = _module("runtime.seal_binding").archive(
        ASAN if os.environ.get("MOYHOST_ASAN") else (), verbose)
    if seal is not None:
        link = link + ["-Wl,--whole-archive", seal, "-Wl,--no-whole-archive"]
    return out, cflags, link


def build(verbose=False, with_inflater=None):
    """The one library's path (built once per content), or None with no C
    compiler. A process asks once; every binding loads what this answers.
    `with_inflater` False is the build of a machine with no desktop
    MicroPython tree, for a test to read, never this process's library."""
    if with_inflater is not None:
        return _build(verbose, with_inflater)
    if _PATH[0] is None:
        _PATH[0] = _build(verbose, None) or False
    return _PATH[0] or None


def _build(verbose, with_inflater):
    ps, cflags, link = parts(verbose, with_inflater)
    if os.environ.get("MOYHOST_ASAN"):
        cflags = cflags + ASAN
        link = link + ASAN
    return native_build.build_parts("moyhost", ps, CACHE, cflags=CFLAGS + cflags,
                                    link_flags=link, verbose=verbose)

