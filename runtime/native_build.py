"""Compile a C shim against vendored sources, cached by content hash.

Three host bindings need exactly this -- audio (`moyhost_audio.c`), the indexed
raster (`moyhost_raster.c`) and the RGB565 compositor (`moyhost_gfx.c`) -- and
the first two had grown their own copy of it. That is the same duplication this
module exists to help retire from the canvases, so it is not repeated a third
time: the differences between the three are a source list, a shim, some flags
and a cache directory, which are arguments.

The cache key is the CONTENT of every source plus the flags plus the compiler's
version string, not a timestamp. That matters because the vendored libmoy
sources move when someone re-vendors from moy-spec, and a mtime-keyed cache
would happily keep serving a library built from the previous drop -- the exact
staleness class that already bit the staged `modules/` trees.

No compiler is not an error here (`None` comes back and the caller decides);
a compiler that FAILS is, because that is a broken tree rather than an absent
toolchain.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, ".."))

# The vendored libmoy every host binding compiles against. One copy, in the
# T-Deck's native tree, which is its canonical home for the boards too.
LIBMOY = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_micropython",
                      "native", "moy_gfx", "libmoy")

# -O2, and NOT -O3: these have to agree with the conformance goldens pixel for
# pixel, and the raster is integer throughout, so there is nothing a more
# aggressive optimizer could win that would be worth re-arguing conformance
# over. (The boards reached the same verdict independently -- see the -O2
# pragmas in the vendored Lua sources and the #159/#190 A/Bs.)
BASE_CFLAGS = ["-std=c99", "-O2", "-fPIC", "-shared"]


def cc():
    """The C compiler, or None. `CC` wins so a caller can pin one."""
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


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
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("%s build failed:\n%s" % (name, proc.stderr))
    os.replace(tmp, so_path)            # atomic vs a parallel test run
    if verbose:
        print("%s: built %s" % (name, os.path.relpath(so_path, ROOT)))
    return so_path
