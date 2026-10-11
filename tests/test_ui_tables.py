"""native/moy_ui's tables are generated from its data and committed
(docs/kernel_toolkit_2026-10.md §2.1, §10 "style is data").

* The drift test: tools/gen_ui_tables.py run over the tree's style.json and
  strings reproduces the committed moy_ui_tables.h/.c byte for byte.
* The data test: a copy of style.json with one state's token, one metric and
  one new kind changed, regenerated and built through runtime/native_build.py's
  own path (the glass's library, as the host builds it), resolves the change where it was made and nowhere else -- so no
  value lives in a C body.
"""

import ctypes
import json
import os
import shutil

from runtime import glass_binding
from tools import gen_ui_tables

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "native", "moy_ui")
ABSENT = -(1 << 31)


def test_the_committed_tables_are_the_datas():
    h, c = gen_ui_tables.generate(*gen_ui_tables.load(UI))
    with open(os.path.join(UI, "moy_ui_tables.h")) as f:
        assert f.read() == h, "moy_ui_tables.h is stale: run tools/gen_ui_tables.py"
    with open(os.path.join(UI, "moy_ui_tables.c")) as f:
        assert f.read() == c, "moy_ui_tables.c is stale: run tools/gen_ui_tables.py"


def _lib(d, cache):
    so = glass_binding.build(ui_dir=d, cache=cache)
    lib = ctypes.CDLL(so)
    lib.moy_ui_colors.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_int16)]
    lib.moy_ui_metrics.restype = ctypes.POINTER(ctypes.c_int16)
    lib.moy_ui_kind_known.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    return lib


def _resolve(lib, kinds, states, tokens):
    row = (ctypes.c_int32 * len(tokens))(*tokens)
    assert lib.moy_ui_look_set(0, row) == 0
    out = {}
    for k in kinds:
        kid = lib.moy_ui_kind_known(k.encode(), len(k))
        for si, st in enumerate(states):
            c = (ctypes.c_int16 * 3)()
            lib.moy_ui_colors(0, kid, si, c)
            out[(k, st)] = tuple(c)
        n = ctypes.c_int()
        p = lib.moy_ui_metrics(kid, ctypes.byref(n))
        out[(k, "metrics")] = tuple(p[i] for i in range(n.value)) if p else None
    return out


def test_a_style_edit_is_drawn_where_it_was_made_and_nowhere_else(tmp_path):
    with open(os.path.join(UI, "style.json")) as f:
        style = json.load(f)
    base_dir = tmp_path / "base"
    edit_dir = tmp_path / "edit"
    for d in (base_dir, edit_dir):
        shutil.copytree(UI, d)
    style["kinds"]["row"]["hover"][0] = 42               # a state's token
    style["metrics"]["button"]["pad"] = 5                # a metric
    style["kinds"]["toggle"] = dict(style["kinds"]["chip"],     # a new kind
                                    on=[["play", 11], 0, ["edge", 13]])
    with open(edit_dir / "style.json", "w") as f:
        json.dump(style, f)
    h, c = gen_ui_tables.generate(*gen_ui_tables.load(str(edit_dir)))
    (edit_dir / "moy_ui_tables.h").write_text(h)
    (edit_dir / "moy_ui_tables.c").write_text(c)

    cache = str(tmp_path / "cache")
    base = _lib(str(base_dir), cache)
    edit = _lib(str(edit_dir), cache)
    tokens = [ABSENT] * len(style["tokens"])
    for i, name in enumerate(style["tokens"]):
        tokens[i] = (i * 5 + 3) % 64
    kinds = sorted(set(style["kinds"]) | set(style["metrics"]))
    a = _resolve(base, kinds, style["states"], tokens)
    b = _resolve(edit, kinds, style["states"], tokens)
    play = tokens[style["tokens"].index("play")]
    # a kind with no style entry resolves as a row: its colours are row's, so
    # only the colour kinds and every kind's metrics are compared
    coloured = set(style["kinds"])
    moved = {k for k in a if a[k] != b[k]
             and (k[0] in coloured or k[1] == "metrics")}
    assert b[("row", "hover")][0] == 42
    assert b[("button", "metrics")][0] == 5
    # the new kind is chip's look, with its own ON
    assert b[("toggle", "on")][0] == play
    assert b[("toggle", "rest")] == b[("chip", "rest")]
    # in the tree's tables `toggle` is no kind, so it resolves as a row
    assert a[("toggle", "rest")] == a[("row", "rest")]
    assert {k for k in moved if k[0] != "toggle"} == {
        ("row", "hover"), ("button", "metrics")}, sorted(moved)
