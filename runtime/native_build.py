"""Compile a C shim against vendored sources, cached by content hash.

Three host bindings need exactly this -- audio (`moyhost_audio.c`), the RGB565
compositor (`moyhost_gfx.c`) and Lua (`moyhost_lua.c`) -- and two of them had
grown their own copy of it. That is the same duplication this module exists to
help retire from the canvases, so it is not repeated a third time: the
differences between the three are a source list, a shim, some flags and a cache
directory, which are arguments. (A fourth, `moyhost_raster.c`, was libmoy built
INDEXED for the deleted host raster; it went with `runtime/canvas.py`.)

The cache key is the CONTENT of every source plus the flags plus the compiler's
version string, not a timestamp. That matters because the vendored libmoy
sources move when someone re-vendors from moy-spec, and a mtime-keyed cache
would happily keep serving a library built from the previous drop -- the exact
staleness class that already bit the staged `modules/` trees.

No compiler is not an error here (`None` comes back and the caller decides);
a compiler that FAILS TO COMPILE is, because that is a broken tree rather than
an absent toolchain. A compiler that cannot be EXECUTED at all -- `CC` pointing
at a path that is not there -- counts as absent, not broken: the tree is fine
and the answer is the same sentence `explain_no_cc` prints.

WHY THAT SENTENCE MATTERS MORE THAN IT USED TO. Until runtime/canvas.py was
deleted the host had a pure-Python raster to fall back on, so "no compiler" cost
you host audio and nothing else. It does not any more: the host draws on
`device_canvas.DeviceCanvas` over `runtime/gfx_binding.py`, which ctypes-loads
what this module builds. So no C compiler is now no host console -- and what a
user actually SAW was `AttributeError: 'NoneType' object has no attribute
'mg_fill'` out of a ctypes wrapper, four frames deep, naming nothing they could
act on. There is deliberately no fallback added here; the failure is simply
stated, once, in the terms of the thing they have to install.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, ".."))

# The vendored libmoy every host binding compiles against. One copy, in the
# T-Deck's native tree, which is its canonical home for the boards too.
LIBMOY = os.path.join(ROOT, "native", "moy_gfx", "libmoy")

# -O2, and NOT -O3: these have to agree with the conformance goldens pixel for
# pixel, and the raster is integer throughout, so there is nothing a more
# aggressive optimizer could win that would be worth re-arguing conformance
# over. (The boards reached the same verdict independently -- see the -O2
# pragmas in the vendored Lua sources and the #159/#190 A/Bs.)
#
# ONE FILE OVERRIDES THIS AND IS MEANT TO: native/moy_gfx/moy_gfx_kernels.c,
# the compositor the host binding and both boards all compile, carries an
# in-source `#pragma GCC optimize("O3")`. It has to -- on the boards it used to
# live inside modmoy_gfx.c, which has carried that pragma since #77, and a new
# translation unit without it would silently halve those loops. Since the file
# is single-source, the host gets the same code generation as the glass, which
# is the stronger guarantee; the conformance goldens and
# tests/test_gfx_binding.py both run against it either way.
BASE_CFLAGS = ["-std=c99", "-O2", "-fPIC", "-shared"]


# How to get a compiler, per platform. Kept as data next to the code that
# reports it missing, so the answer and the diagnosis cannot drift apart.
INSTALL_HINTS = (
    ("linux", "Debian/Ubuntu: sudo apt install build-essential"),
    ("linux", "Fedora/RHEL:   sudo dnf install gcc"),
    ("linux", "Arch:          sudo pacman -S base-devel"),
    ("darwin", "macOS:         xcode-select --install"),
)

_WARNED = [False]


def cc():
    """The C compiler, or None. `CC` wins so a caller can pin one."""
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


def explain_no_cc(detail=None):
    """The sentence a user gets instead of a ctypes AttributeError.

    It has to answer three things, because the failure answers none of them:
    WHAT is missing (a C compiler), WHY this project wants one (the host console
    is drawn by compiled libmoy -- it is not an optional accelerator), and WHAT
    TO TYPE. The install lines are filtered to the running platform so the
    answer is one line rather than a menu to read.
    """
    plat = "darwin" if sys.platform == "darwin" else \
           "windows" if os.name == "nt" else "linux"
    lines = [
        "moybyte: NO C COMPILER -- the host console cannot run.",
        "",
        "  The host draws through a small library compiled from the vendored",
        "  libmoy sources (the same raster both boards run). There is no",
        "  Python fallback raster any more, so without a compiler the",
        "  simulator dies at its first draw and `make test` cannot render.",
        "",
        "  Looked for: $CC%s, then `cc`, then `gcc` on PATH."
        % ("=" + os.environ["CC"] if os.environ.get("CC") else " (unset)"),
    ]
    if detail:
        lines.append("  Reason:     %s" % detail)
    lines.append("")
    hints = [text for osname, text in INSTALL_HINTS if osname == plat]
    if hints:
        lines.append("  Install one:")
        lines.extend("    " + h for h in hints)
    else:
        lines.append("  Install a C toolchain for this platform (gcc or clang),")
        lines.append("  or point $CC at one you already have.")
    lines.append("")
    lines.append("  Firmware builds are unaffected -- they use the ESP-IDF"
                 " toolchain.")
    return "\n".join(lines)


def warn_no_cc(detail=None, once=True):
    """Print `explain_no_cc` to stderr, at most once per process by default.

    Once, because the three host bindings (gfx, audio, lua) each reach this
    independently and three copies of the same paragraph reads like three
    different problems. Stderr, because the callers that route past this are
    guarded and their output is a console the user is trying to look at.
    """
    if once and _WARNED[0]:
        return
    _WARNED[0] = True
    print(explain_no_cc(detail), file=sys.stderr)


def read_sources(shim, libmoy_names, libmoy_dir=None):
    """{filename: text} for the shim plus the vendored files it needs.

    Read rather than referenced so the cache key covers what was actually
    compiled, and so the build directory is self-contained (a failed build can
    be reproduced by hand from what is on disk).

    `libmoy_dir` may be one directory or SEVERAL, searched in order: the Lua
    binding compiles libmoy's raster, libmoy's Lua binding and the vendored Lua
    itself, which live in three sibling `native/` trees. The files are copied
    into one build directory either way, so a name that appears in two of them
    would be ambiguous -- and is an error rather than a silent first-wins.
    """
    if libmoy_dir is None:
        libmoy_dir = LIBMOY
    dirs = [libmoy_dir] if isinstance(libmoy_dir, str) else list(libmoy_dir)
    out = {}
    for name in libmoy_names:
        found = [os.path.join(d, name) for d in dirs
                 if os.path.isfile(os.path.join(d, name))]
        if not found:
            raise FileNotFoundError(name)
        if len(found) > 1:
            raise RuntimeError("%s is in %d source dirs: %s"
                               % (name, len(found), ", ".join(found)))
        with open(found[0]) as fh:
            out[name] = fh.read()
    with open(shim) as fh:
        out[os.path.basename(shim)] = fh.read()
    return out


def cache_key(compiler, sources, cflags):
    h = hashlib.sha256()
    for name in sorted(sources):
        h.update(name.encode())
        h.update(sources[name].encode())
    h.update(" ".join(cflags).encode())
    try:
        ver = subprocess.run([compiler, "--version"], capture_output=True,
                             text=True, timeout=10).stdout.splitlines()[:1]
        h.update((ver[0] if ver else "").encode())
    except Exception:   # noqa: BLE001 -- version only refines the cache key
        pass
    return h.hexdigest()[:16]


def build(name, shim, libmoy_names, cache_dir, cflags=None, compile_names=None,
          libmoy_dir=None, link_flags=None, verbose=False):
    """Compile (or reuse) `name`'s cached .so. None when there is no compiler.

    `compile_names` are the translation units handed to the compiler; it
    defaults to the shim plus every .c in `libmoy_names`, which is what all
    three callers want (the .h files are read for the cache key and for the
    include directory, but are not compiled).

    `link_flags` go AFTER the sources, which is where a `-l` has to be: with
    --as-needed (the default on most distros) a library named before the
    objects that reference it is dropped from the link.
    """
    compiler = cc()
    if compiler is None:
        warn_no_cc()
        return None
    cflags = list(cflags if cflags is not None else BASE_CFLAGS)
    link_flags = list(link_flags or ())
    sources = read_sources(shim, libmoy_names, libmoy_dir)
    so_path = os.path.join(
        cache_dir,
        "%s-%s.so" % (name, cache_key(compiler, sources, cflags + link_flags)))
    if os.path.exists(so_path):
        return so_path
    os.makedirs(cache_dir, exist_ok=True)
    src_dir = so_path[:-3] + ".src"
    os.makedirs(src_dir, exist_ok=True)
    for fname, text in sources.items():
        with open(os.path.join(src_dir, fname), "w") as fh:
            fh.write(text)
    if compile_names is None:
        compile_names = [os.path.basename(shim)] + [n for n in libmoy_names
                                                    if n.endswith(".c")]
    tmp = so_path + ".tmp"
    cmd = ([compiler] + cflags + ["-I", src_dir]
           + [os.path.join(src_dir, n) for n in compile_names]
           + ["-o", tmp] + link_flags)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        # The compiler could not be RUN -- a `CC` pointing at a path that is not
        # there, or one that is not executable. That is an absent toolchain, not
        # a broken tree, so it takes the `None` lane and the same explanation a
        # missing one gets. It used to escape as a bare FileNotFoundError from
        # subprocess, naming the shim it was trying to build and not the setting
        # that sent it nowhere.
        warn_no_cc("%s: %s" % (compiler, exc))
        return None
    if proc.returncode != 0:
        raise RuntimeError("%s build failed:\n%s" % (name, proc.stderr))
    os.replace(tmp, so_path)            # atomic vs a parallel test run
    if verbose:
        print("%s: built %s" % (name, os.path.relpath(so_path, ROOT)))
    return so_path


def check():
    """Report the host toolchain in one line, or explain its absence in
    several. Returns the compiler, or None.

    `python -m runtime.native_build` is exactly this, and `make setup` runs it
    so the answer arrives while the user is still at the terminal that installed
    everything else -- rather than an hour later, out of a ctypes wrapper, the
    first time they start the simulator.
    """
    compiler = cc()
    if compiler is None:
        warn_no_cc(once=False)
        return None
    try:
        out = subprocess.run([compiler, "--version"], capture_output=True,
                             text=True, timeout=10)
    except OSError as exc:
        warn_no_cc("%s: %s" % (compiler, exc), once=False)
        return None
    first = (out.stdout or out.stderr or "").splitlines()[:1]
    print("moybyte: C compiler %s -- %s" % (compiler, first[0] if first else "?"))
    return compiler


if __name__ == "__main__":                            # pragma: no cover
    raise SystemExit(0 if check() is not None else 1)
