"""Every module a board stages has exactly one host face (docs/
refactor_2026-11.md, phase 6, T10): each MicroPython module a staged
native dir registers is named by one native/<dir>/face.json, and the host
registers a face's module through runtime/host/faces.py's table alone, never
by a sys.modules write of its own."""

import os
import re
import subprocess
import sys

from tools.gen_os import face as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRMWARE = os.path.join(ROOT, "firmware")


def _staged_dirs():
    out = set()
    for b in sorted(os.listdir(FIRMWARE)):
        if not os.path.isfile(os.path.join(FIRMWARE, b, "board.toml")):
            continue
        got = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "board_config.py"),
                              "list-native", os.path.join(FIRMWARE, b)],
                             capture_output=True, text=True, check=True).stdout
        out.update(got.split())
    return out


def _native_dir(d):
    for p in (os.path.join(ROOT, "native", d), os.path.join(ROOT, "native", "p4", d)):
        if os.path.isdir(p):
            return p
    raise AssertionError("no native dir %s" % d)


def _modules(path):
    out = set()
    for f in sorted(os.listdir(path)):
        if f.endswith(".c"):
            with open(os.path.join(path, f), errors="replace") as fh:
                out.update(re.findall(r"^MP_REGISTER_MODULE\(MP_QSTR_(\w+),", fh.read(), re.M))
    return out


def test_every_staged_module_has_one_face():
    faces = F.load()
    missing = []
    for d in sorted(_staged_dirs()):
        for m in sorted(_modules(_native_dir(d))):
            if m not in faces:
                missing.append("%s (native/%s)" % (m, d))
    assert not missing, "modules a board stages with no face.json entry: %s" % missing


def test_the_faces_table_registers_every_host_face():
    from runtime.faces import FACES
    faces = F.load()
    want = sorted(m for m, f in faces.items() if f.get("host") and f.get("registered", True))
    assert sorted(m for m, *_ in FACES) == want


def test_no_host_file_registers_a_face_s_module_itself():
    """The table is the one door: a face's module name written into
    sys.modules anywhere else is a second registration."""
    names = set(F.load())
    bad = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "runtime")):
        for f in files:
            if not f.endswith(".py") or f == "__init__.py":
                continue
            p = os.path.join(base, f)
            with open(p) as fh:
                for i, line in enumerate(fh, 1):
                    m = re.search(r"sys\.modules(?:\.setdefault\(|\[)\s*[\"'](\w+)[\"']", line)
                    if m and m.group(1) in names:
                        bad.append("%s:%d %s" % (os.path.relpath(p, ROOT), i, m.group(1)))
    assert not bad, bad
