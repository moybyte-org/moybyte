"""Every ctypes structure a host face mirrors is the C's: its size and each
field's offset, read from a probe the host's C compiler builds over the
host library's own sources and flags, equal the generated mirror's
(docs/refactor_2026-11.md, phase 6: T9, N9).

The mirrors are generated from the native headers by tools/gen_os_table.py
(tools/gen_os/structs.py) into runtime/host/<m>_face.py, one class per C
type, named as C names it."""

import importlib
import os
import subprocess

import pytest

from tools.gen_os import face as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _faces():
    out = []
    for m, face in sorted(F.load().items()):
        if face.get("host") and face.get("structs"):
            mir = F.mirrors(face)
            out.append((m, mir))
    return out


FACES = _faces()


def _probe(mirrors):
    """The probe's C: one line a structure (`S name size`), one a field
    (`F name field offset`)."""
    o = ["#include <stdio.h>", "#include <stddef.h>"]
    heads = []
    ix = F.index()
    for _m, mir in mirrors:
        for n in mir.named:
            h = os.path.join(ROOT, ix.bodies[n][2])
            if h not in heads:
                heads.append(h)
    o += ['#include "%s"' % h for h in heads]
    o.append("int main(void) {")
    for _m, mir in mirrors:
        for n in mir.named:
            o.append('    printf("S %s %%zu\\n", sizeof(%s));' % (n, n))
            for f, member in _fields(mir, n):
                o.append('    printf("F %s %s %%zu\\n", offsetof(%s, %s));' % (n, f, n, member))
    o += ["    return 0;", "}"]
    return "\n".join(o) + "\n"


def _fields(mir, n, prefix=""):
    """(python path, C member) of each field `n` holds, nested structures by
    value included."""
    out = []
    for f, member in mir.fields[n]:
        out.append((prefix + f, (prefix.replace("__", ".") + member)))
    return out


@pytest.fixture(scope="module")
def layout(tmp_path_factory):
    import native_build
    from runtime import host_native
    so = host_native.build()
    if so is None:
        pytest.skip("no C compiler")
    src = so[:-3] + ".src"
    d = tmp_path_factory.mktemp("probe")
    c = d / "probe.c"
    c.write_text(_probe(FACES))
    exe = d / "probe"
    flags = [f for f in host_native.CFLAGS if f not in ("-shared", "-fPIC")]
    dirs = sorted({os.path.dirname(os.path.join(ROOT, F.index().bodies[n][2]))
                   for _m, mir in FACES for n in mir.named})
    inc = ["-I", src] + [x for d in dirs for x in ("-I", d)]
    got = subprocess.run([native_build.cc()] + flags + ["-w"] + inc + [str(c), "-o", str(exe)],
                         capture_output=True, text=True)
    assert got.returncode == 0, got.stderr[-4000:]
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
    sizes, offs = {}, {}
    for line in out.splitlines():
        kind, *rest = line.split()
        if kind == "S":
            sizes[rest[0]] = int(rest[1])
        else:
            offs[(rest[0], rest[1])] = int(rest[2])
    return sizes, offs


@pytest.mark.parametrize("m,mir", FACES, ids=[m for m, _ in FACES])
def test_every_mirror_has_the_c_size_and_offsets(m, mir, layout):
    import runtime  # noqa: F401  (the host faces' path)
    sizes, offs = layout
    mod = importlib.import_module("%s_face" % m)
    bad = []
    for n in mir.named:
        cls = getattr(mod, n)
        if ctypes_sizeof(cls) != sizes[n]:
            bad.append("%s: %d bytes, C %d" % (n, ctypes_sizeof(cls), sizes[n]))
        for f, _member in _fields(mir, n):
            at = getattr(cls, f).offset
            if at != offs[(n, f)]:
                bad.append("%s.%s: at %d, C %d" % (n, f, at, offs[(n, f)]))
    assert not bad, "\n".join(bad)


def ctypes_sizeof(cls):
    import ctypes
    return ctypes.sizeof(cls)


def test_the_mirrors_are_named_by_a_face():
    """A ctypes Structure under runtime/host is a generated face's: none is
    written by hand."""
    import re
    hand = []
    host = os.path.join(ROOT, "runtime", "host")
    for f in sorted(os.listdir(host)):
        if not f.endswith(".py") or f.endswith("_face.py"):
            continue
        with open(os.path.join(host, f)) as fh:
            for i, line in enumerate(fh, 1):
                if re.search(r"class \w+\((?:\w+\.)?(?:Structure|Union)\)", line):
                    hand.append("%s:%d" % (f, i))
    assert not hand, "hand-written ctypes structures (name them in a face.json's structs): %s" % hand
