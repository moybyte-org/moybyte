"""Every native module with a face.json has one set of names and one set of
answers on every face (docs/refactor_2026-11.md, phase 6).

  * names: the MicroPython globals the hand file's table holds (its
    generated MOY_FACE_<M>_ROWS expanded), the host face's public names and
    the face.json's rows and constants are one set, less the rows a face
    declares absent;
  * answers: one script, generated from the face.json (tools/gen_os/face.py
    parity_script), calls each row on its example and prints each constant,
    and its output on CPython over the ctypes face equals its output on the
    desktop MicroPython in the boards' object model. A `noalloc` row is
    measured there and must allocate nothing.

The generated files themselves are tools/gen_os_table.py's outputs, which
tests/test_os_table.py regenerates and compares."""

import json
import os
import re
import subprocess
import sys
import types

import pytest

from tools.gen_os import face as F
from unix_mp import require_unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACES = F.load()
GENERATED = sorted(m for m in FACES if "rows" in FACES[m])


def mp_names(m, face):
    """The names in the module's MicroPython globals table, read from its C."""
    with open(F.hand_c(m, face)) as f:
        src = f.read()
    reg = re.search(r"MP_REGISTER_MODULE\(MP_QSTR_%s,\s*(\w+)\)" % m, src)
    assert reg, "%s registers no module %s" % (F.hand_c(m, face), m)
    glob = re.search(r"%s\s*=\s*\{.*?\.globals\s*=\s*\(mp_obj_dict_t\s*\*\)\s*&(\w+)"
                     % reg.group(1), src, re.S).group(1)
    table = re.search(r"MP_DEFINE_CONST_DICT\(%s,\s*(\w+)\)" % glob, src).group(1)
    body = re.search(r"%s\[\]\s*=\s*\{(.*?)\n\};" % table, src, re.S).group(1)
    names = set(re.findall(r"\{\s*MP_ROM_QSTR\(MP_QSTR_(\w+)\)", body)) - {"__name__"}
    if "MOY_FACE_%s_ROWS" % m.upper() in body:
        names |= {r.name for r in F.rows(m, face)
                  if not r.row.get("hand") and "mp" not in r.row.get("absent", ())}
        names |= {p for _, p in F.consts(face)}
    return names


def mp_arities(m, face):
    """{name: {(least, most)}} of the hand file's globals that are functions,
    read from their MP_DEFINE_CONST_FUN_OBJ_* (one per build where an #if
    gives a row two)."""
    with open(F.hand_c(m, face)) as f:
        src = f.read()
    out = {}
    for name, obj in re.findall(r"\{\s*MP_ROM_QSTR\(MP_QSTR_(\w+)\),\s*MP_ROM_PTR\(&(\w+)\)", src):
        for kind, rest in re.findall(r"MP_DEFINE_CONST_FUN_OBJ_(\w+)\(%s,([^;]*)\);" % obj, src):
            if kind.isdigit():
                got = (int(kind), int(kind))
            elif kind == "VAR_BETWEEN":
                lo, hi = [x.strip() for x in rest.split(",")[:2]]
                got = (int(lo), None if hi == "MP_OBJ_FUN_ARGS_MAX" else int(hi))
            elif kind == "VAR":
                got = (int(rest.split(",")[0]), None)
            else:
                continue
            out.setdefault(name, set()).add(got)
    return out


@pytest.mark.parametrize("m", sorted(m for m in FACES if "rows" in FACES[m]))
def test_every_hand_row_takes_its_signature_s_arguments(m):
    face = FACES[m]
    ar = mp_arities(m, face)
    for r in face["rows"]:
        name, params, star = F.py_sig(r)
        if not r.get("hand") or name not in ar or "(" not in r["py"]:
            continue
        need = sum(1 for _, d in params if d is None)
        want = (need, None if star else len(params))
        assert want in ar[name], (name, want, ar[name])


def host_names(m):
    import runtime  # noqa: F401  (registers the host faces)
    face = FACES[m]
    if face.get("registered", True):
        mod = sys.modules[m]
    else:
        import importlib
        mod = importlib.import_module(face["host"])
    out = set()
    for n in dir(mod):
        v = getattr(mod, n)
        if n.startswith("_") or isinstance(v, types.ModuleType):
            continue
        if type(v).__name__ in ("_Feature", "Part"):
            continue          # an import's feature; the host build's sources
        out.add(n)
    return out


@pytest.mark.parametrize("m", GENERATED)
def test_every_face_holds_the_table_s_names(m):
    face = FACES[m]
    assert mp_names(m, face) == set(F.names(m, face, "mp"))
    if face.get("host"):
        assert host_names(m) == set(F.names(m, face, "host"))


def answers(m, runtime):
    """The parity script's lines on `runtime` ("cpython" or "mp")."""
    script = F.parity_script(m, FACES[m])
    if runtime == "cpython":
        head = ("import sys\nsys.path.insert(0, %r)\nimport runtime\nimport importlib\n"
                "M = importlib.import_module(%r)\nMP = False\n"
                % (ROOT, m if FACES[m].get("registered", True) else FACES[m]["host"]))
        done = subprocess.run([sys.executable, "-c", head + script], cwd=ROOT,
                              capture_output=True, text=True, timeout=300)
    else:
        exe = require_unix_mp(m, board_model=True,
                              why="the generated MicroPython face of %s" % m)
        done = subprocess.run([str(exe), "-c", "import %s as M\nMP = True\n" % m + script],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.splitlines()


def unix_mp_dirs():
    """The native dirs `make unix-micropython` compiles in."""
    with open(os.path.join(ROOT, "Makefile")) as f:
        mk = f.read()
    m = re.search(r"^UNIX_MP_MODULES \?= ((?:.*\\\n)*.*)$", mk, re.M)
    return set(m.group(1).replace("\\\n", " ").split())


ON_BOTH = [m for m in GENERATED if FACES[m].get("host") and F.generated(FACES[m])
           and FACES[m]["dir"] in unix_mp_dirs()]


@pytest.mark.parametrize("m", ON_BOTH)
def test_one_script_answers_alike_on_both_runtimes(m):
    mp = answers(m, "mp")
    grew = [json.loads(line) for line in mp]
    assert [g[0] for g in grew if len(g) == 4 and g[2]] == [], mp
    assert answers(m, "cpython") == mp
