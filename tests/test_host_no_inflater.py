"""The host's one library without the inflater's part, as a machine with no
desktop MicroPython tree builds it (CI's browser-console build, in every
board's firmware job and in the web E2E: runtime/host/host_native.py).

A binding loads its row table whole at its first call, so a row whose symbol
only the inflater's part defines takes the whole binding down on such a
machine, for every caller, while every check on a machine with the tree
passes. Every `*SIGS` table under runtime/host names no symbol that part
alone defines, but the tables a binding loads only once it has seen that part
is there (GATED).
"""

import ast
import os
import shutil
import subprocess
import warnings

import pytest

import host_native

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.join(os.path.dirname(HERE), "runtime", "host")

# (module, table): loaded only after a probe of the inflater's part.
GATED = {
    ("moy_store_binding", "_PACK_SIGS"),
    ("moy_ufiles_binding", "_SIGS"),
    ("moy_ufiles_face", "_SIGS"),
}


def _tables():
    """{(module, table): [symbol]} for every module-level `*SIGS` tuple of
    rows whose first item is a symbol's name."""
    out = {}
    for f in sorted(os.listdir(HOST)):
        if not f.endswith(".py"):
            continue
        with open(os.path.join(HOST, f)) as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple)):
                continue
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.endswith("SIGS"):
                    out[(f[:-3], t.id)] = [
                        row.elts[0].value for row in node.value.elts
                        if isinstance(row, ast.Tuple) and row.elts
                        and isinstance(row.elts[0], ast.Constant)
                        and isinstance(row.elts[0].value, str)]
    return out


def _defined(path):
    nm = shutil.which("nm")
    assert nm, "nm (binutils) reads the library's symbols"
    lines = subprocess.run([nm, "-D", "--defined-only", path], check=True,
                           capture_output=True, text=True).stdout.splitlines()
    return {ln.split()[-1] for ln in lines if ln.split()}


def test_a_binding_loaded_whole_names_no_symbol_only_the_inflater_defines():
    if not host_native.inflater(True):
        text = ("no desktop MicroPython tree (make unix-micropython): the "
                "library with the inflater's part, which this test compares "
                "with the one without, cannot build here")
        if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_UNIX_MP"):
            pytest.fail(text)
        warnings.warn(UserWarning(text))
        pytest.skip("no desktop MicroPython tree")
    full = host_native.build(with_inflater=True)
    lean = host_native.build(with_inflater=False)
    if full is None or lean is None:
        pytest.skip("no C compiler for the host build")
    only = _defined(full) - _defined(lean)
    assert only, "the inflater's part defines nothing of its own: the build ignored it"
    tables = _tables()
    assert GATED <= set(tables), "a gated table is gone: %s" % (GATED - set(tables))
    bad = sorted("%s.%s: %s" % (m, t, s) for (m, t), names in tables.items()
                 if (m, t) not in GATED for s in names if s in only)
    assert bad == [], ("a row whose symbol only the inflater's part defines, in a "
                       "table loaded whole on a machine with no desktop "
                       "MicroPython tree: %s" % bad)
    gated = {s for k in GATED for s in tables[k]}
    assert gated & only, "no gated table names the inflater's part: GATED is stale"
