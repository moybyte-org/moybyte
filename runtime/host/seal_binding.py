"""The sign-in's crypto (native/moy_net/moy_seal.c) and the apps' crypto rows'
body (native/moy_net/moy_crypto.c) for the host's one library.

Both compile against mbedTLS: a board's is the ESP-IDF's, the host's
the vendored copy of the same release (native/moy_net/mbedtls/README.md).
Its headers keep their folder (`mbedtls/aes.h`), so it is a static archive of
its own, as the shell's is (runtime/host/moy_sh.py), which the one library
(runtime/host/host_native.py) links whole. Everything else of the sign-in
(moy_webauth.c, moy_seal_web.c) includes moy_seal.h alone and compiles in
moy_net's part.
"""

from __future__ import annotations

import glob
import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

try:                                    # the host's runtime package
    from . import native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import native_build

NET = os.path.join(native_build.ROOT, "native", "moy_net")
MBEDTLS = os.path.join(NET, "mbedtls")
_CACHE = os.path.join(native_build.ROOT, ".build", "host")
FLAGS = ["-std=gnu99", "-O2", "-fPIC", "-w", "-I", NET, "-I", os.path.join(MBEDTLS, "include"),
         "-I", os.path.join(MBEDTLS, "library"),
         "-DMBEDTLS_CONFIG_FILE=\"moy_seal_mbedtls.h\""]


def sources():
    """The translation units: moy_seal.c, moy_crypto.c (the crypto rows'
    body, native/moy_net/moy_crypto.h) and the vendored library."""
    return [os.path.join(NET, "moy_seal.c"), os.path.join(NET, "moy_crypto.c")] + sorted(
        glob.glob(os.path.join(MBEDTLS, "library", "*.c")))


def archive(cflags=(), verbose=False):
    """The archive's path (built once per content), or None with no C compiler."""
    cc = native_build.cc()
    if cc is None:
        return None
    flags = FLAGS + list(cflags)
    h = hashlib.sha256(" ".join(flags).encode())
    paths = sources() + sorted(glob.glob(os.path.join(MBEDTLS, "**", "*.h"), recursive=True))
    paths += [os.path.join(NET, "moy_seal.h"), os.path.join(NET, "moy_crypto.h"),
              os.path.join(NET, "moy_seal_mbedtls.h")]
    for path in paths:
        h.update(path[len(native_build.ROOT):].encode())
        with open(path, "rb") as f:
            h.update(f.read())
    out = os.path.join(_CACHE, "moyseal-%s.a" % h.hexdigest()[:16])
    if os.path.exists(out):
        return out
    os.makedirs(_CACHE, exist_ok=True)
    with native_build.build_lock(out + ".lock"):
        if os.path.exists(out):
            return out
        obj_dir = "%s.%d.obj" % (out, os.getpid())
        os.makedirs(obj_dir, exist_ok=True)

        def one(src):
            obj = os.path.join(obj_dir, os.path.basename(src)[:-2] + ".o")
            r = subprocess.run([cc] + flags + ["-c", src, "-o", obj],
                               capture_output=True, text=True)
            return obj, r

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            done = list(pool.map(one, sources()))
        bad = [r.stderr for _o, r in done if r.returncode]
        if bad:
            raise RuntimeError("moy_seal build failed:\n%s" % "".join(bad))
        tmp = "%s.%d.tmp" % (out, os.getpid())
        r = subprocess.run(["ar", "rcs", tmp] + [o for o, _r in done],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("moy_seal archive failed:\n%s" % r.stderr)
        os.replace(tmp, out)
        for o, _r in done:
            os.remove(o)
        os.rmdir(obj_dir)
        if verbose:
            print("moy_seal: built %s" % os.path.relpath(out, native_build.ROOT))
    return out
