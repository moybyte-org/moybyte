"""Pages compiled ahead of time (runtime/uitree/uitree_aot.py,
tools/uitree_compile.py; the format is native/moy_uitree/README.md's "A
compiled page"): every C engine run draws its record with its first pages
loaded from blobs, a blob of another format, layout, page or module is
refused by name, a blob carries every field of every record it stores, the
compiler's bytes are the same on every run, and a blob made before its
module changed is named stale.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import test_uitree_ports as PORTS  # noqa: E402
import uitree_parity as P  # noqa: E402
import uitree_compile as UC  # noqa: E402

import uitree_aot  # noqa: E402
import uitree_c  # noqa: E402

# The console's pages an image compiles: the modules whose pages the C runs open (PORTS.C_RUNS).
MODULES = UC.SYSTEM


@pytest.fixture(scope="module")
def compiled():
    return UC.compile_modules(MODULES)


def test_every_system_page_compiles(compiled):
    assert not compiled.skipped, compiled.skipped
    for m in MODULES:
        assert any(k.startswith(m + ".") for k in compiled.pages), m


def _board_order(monkeypatch):
    """Every model's fields in another dict order than the one the blobs were
    compiled with, as a board's dicts keep them."""
    import uitree as S
    for m in MODULES:
        for t in S.decl_of(__import__(m)).state._t.values():
            if isinstance(t, S.Model):
                monkeypatch.setattr(t, "fields", dict(reversed(list(t.fields.items()))))


@pytest.mark.parametrize("order", ("host", "board"))
@pytest.mark.parametrize("name", PORTS.C_RUNS)
def test_a_c_run_draws_its_record_with_its_pages_loaded(name, order, compiled, tmp_path, monkeypatch):
    import uitree_app
    if order == "board":
        _board_order(monkeypatch)
    run = [r for r in PORTS.RUNS if r[0] == name][0]
    monkeypatch.setattr(uitree_aot, "BLOBS", compiled.pages)
    loaded, built = [], []
    load = uitree_aot.load

    def counted_load(eng, x, blob):
        loaded.append(uitree_aot.key(eng.pages[0]))
        return load(eng, x, blob)
    monkeypatch.setattr(uitree_aot, "load", counted_load)
    lower = uitree_c.CEngine.lower

    def counted_lower(self, root):
        if len(self.pages) == 1:
            built.append(uitree_aot.key(self.pages[0]))
        return lower(self, root)
    monkeypatch.setattr(uitree_c.CEngine, "lower", counted_lower)
    refused = []
    monkeypatch.setattr(uitree_aot, "Refused", type("Refused", (Exception,), {
        "__init__": lambda self, m: (refused.append(m), Exception.__init__(self, m))[1]}))
    uitree_app.use_engine("c")
    try:
        P.check_recorded(name, PORTS.SURFACES[run[1]], tmp_path / "carts", run[4] if len(run) > 4 else None)
    finally:
        uitree_app.use_engine("reference")
    assert not refused, refused
    assert loaded, "no page of the run was loaded from a blob"
    assert not [k for k in built if k in compiled.pages], built


def _engine(blob_page, compiled):
    """A C engine for the page `blob_page` names, and its blob."""
    mod, _, fn = blob_page.partition(".")
    m = __import__(mod)
    import uitree as S
    return uitree_c.CEngine(S.decl_of(m), getattr(m, fn), None, None, None), compiled.pages[blob_page]


def _refusal(compiled, blob, page="settings_rows.rows_page"):
    eng, _ = _engine(page, compiled)
    x = uitree_c._New()
    try:
        r = x.load(blob, eng._page_id(), uitree_aot.VARIANT)
        return r, x.error()
    finally:
        x.free()


def _put32(b, at, v):
    b[at:at + 4] = bytes(((v >> s) & 255) for s in (0, 8, 16, 24))


def test_a_blob_of_another_format_or_layout_is_refused_by_name(compiled):
    import moy_uitree
    good = compiled.pages["settings_rows.rows_page"]
    assert _refusal(compiled, good) == (0, _refusal(compiled, good)[1])
    other = bytearray(good)
    other[4] = moy_uitree.FORMAT + 1
    r, why = _refusal(compiled, bytes(other))
    assert r == -7 and "format %d" % (moy_uitree.FORMAT + 1) in why, why
    other = bytearray(good)
    _put32(other, 8, moy_uitree.layout() ^ 1)
    r, why = _refusal(compiled, bytes(other))
    assert r == -7 and "layout %08x" % (moy_uitree.layout() ^ 1) in why, why
    other = bytearray(good)
    other[-1] ^= 0xFF
    r, why = _refusal(compiled, bytes(other))
    assert r == -7 and "damaged" in why, why
    r, why = _refusal(compiled, compiled.pages["tasks_ui.tasks_page"])
    assert r == -7 and "another page" in why, why


def test_a_refused_blob_builds_the_page_on_the_device(compiled, monkeypatch, capsys):
    import moy_uitree
    other = bytearray(compiled.pages["settings_rows.rows_page"])
    _put32(other, 8, moy_uitree.layout() ^ 1)
    monkeypatch.setattr(uitree_aot, "BLOBS", {"settings_rows.rows_page": bytes(other)})
    eng, _ = _engine("settings_rows.rows_page", compiled)
    try:
        eng._made()
        assert not eng._loaded and eng._x.stats()[0] == 0
        assert "settings_rows.rows_page: compiled page refused (a compiled page of layout" in capsys.readouterr().out
    finally:
        eng.forget()


def test_a_blob_naming_what_the_module_lacks_is_refused_by_name(compiled):
    eng, blob = _engine("achievements_ui.achievements_page", compiled)
    f = uitree_aot.front_of(blob)
    at, n = (int.from_bytes(blob[o:o + 4], "little") for o in (24, 28))
    last = f["A"][-1].encode()
    text = blob[at:at + n].replace(b"," + last + b"\n", b",gone_" + last + b"\n")
    assert text != blob[at:at + n]
    with pytest.raises(uitree_aot.Refused, match="no action 'gone_%s'" % last.decode()):
        uitree_aot.load(eng, None, blob[:at] + text)


def test_a_blob_carries_every_field_of_every_record():
    """Each struct a blob stores names every member in moy_uitree_aot.c's
    field tables; a slot's members the visit owns are listed there by name."""
    h = (ROOT / "native/moy_uitree/moy_uitree_int.h").read_text()
    aot = (ROOT / "native/moy_uitree/moy_uitree_aot.c").read_text()
    listed = set(re.findall(r"FLD\((\w+), (\w+)\)", aot))
    visit = re.search(r"The rest of a slot \(([^)]*)\)", aot.replace("\n//", "")).group(1)
    visit = set(v.strip() for v in visit.split(",")) | {"mf"}
    for t in ("uit_slot_t", "uit_fields_t", "uit_act_t", "uit_labels_t", "uit_tmpl_t", "uit_op_t",
              "uit_cond_t", "uit_term_t", "uit_node_t", "uit_prop_t"):
        body = re.search(r"typedef struct \{([^}]*)\} %s;" % t, h).group(1)
        body = re.sub(r"//[^\n]*", "", body)
        members = set(re.findall(r"(\w+)(?:\[\w+\])?\s*[,;]", body))
        missing = members - set(m for s, m in listed if s == t) - (visit if t == "uit_slot_t" else set())
        assert not missing, (t, missing)


def test_the_compiler_writes_the_same_bytes_every_run(tmp_path):
    outs = []
    for seed in ("1", "2"):
        out = tmp_path / ("pages_%s.py" % seed)
        env = dict(os.environ, PYTHONHASHSEED=seed)
        subprocess.run([sys.executable, str(ROOT / "tools/uitree_compile.py"), "settings_rows",
                        "achievements_ui", "--out", str(out)], check=True, env=env, cwd=ROOT,
                       stdout=subprocess.DEVNULL)
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    assert b"settings_rows.rows_page" in outs[0]


def test_a_blob_made_before_its_module_changed_is_stale(compiled, tmp_path):
    blob = compiled.pages["log_ui.log_page"]
    import log_ui
    assert UC.stale({"log_ui.log_page": blob}) == []
    src = Path(log_ui.__file__).read_text()
    copy = tmp_path / "log_ui.py"
    copy.write_text(src + "\n# a change\n")
    assert UC.stale({"log_ui.log_page": blob}, paths={"log_ui": str(copy)}) == ["log_ui.log_page"]


def _staged(tmp_path):
    import importlib
    mods = tmp_path / "modules"
    mods.mkdir()
    for m in MODULES:
        (mods / (m + ".py")).write_text(Path(importlib.import_module(m).__file__).read_text())
    return mods


def test_staging_compiles_the_staged_pages_and_names_a_stale_one(tmp_path, capsys):
    mods = _staged(tmp_path)
    assert UC.stage(str(mods)) == 0
    assert "uitree pages: 14 compiled from 10 staged modules" in capsys.readouterr().out
    ns = {}
    exec((mods / "uitree_pages.py").read_text(), ns)
    blobs = dict((k, v[0]) for k, v in ns["PAGES"].items())
    assert set(blobs) == set(UC.compile_modules(MODULES).pages)
    paths = dict((m, str(mods / (m + ".py"))) for m in MODULES)
    assert UC.stale(blobs, paths) == []
    with open(mods / "tasks_ui.py", "a") as f:
        f.write("\n# a change\n")
    assert UC.stale(blobs, paths) == ["tasks_ui.tasks_page"]
    with pytest.raises(SystemExit, match="tasks_ui"):
        UC.stage(str(mods))


def test_a_host_that_cannot_compile_stages_no_pages_and_says_so_in_one_line(tmp_path):
    mods = _staged(tmp_path)
    (mods / "uitree_pages.py").write_text("PAGES = {}\n")
    r = subprocess.run([sys.executable, str(ROOT / "tools/uitree_compile.py"), "--staged", str(mods)],
                       env=dict(os.environ, CC="/nonexistent/cc"), cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == UC.CANNOT_RUN, r.stderr
    assert r.stdout.count("\n") == 1 and "every page builds on the device" in r.stdout
    assert not (mods / "uitree_pages.py").exists()


def test_on_the_boards_python_a_loaded_page_neither_builds_nor_lowers_and_keeps_the_built_bytes(compiled, tmp_path):
    """The boards' MicroPython (the board model) loads the host's blobs: the
    first open of every page builds and lowers nothing, and each page left
    keeps the bytes its built twin keeps (`test_uitree_c_memory`'s rounds)."""
    import test_frame_alloc as FA
    import test_uitree_c_memory as M
    entries = dict((k, uitree_aot.entry(v)) for k, v in compiled.pages.items())
    drv = M.DRIVER.replace('''    uitree_app.use_engine("c")''', '''    uitree_app.use_engine("c")
    import uitree_aot
    uitree_aot.BLOBS = BLOBS''')
    out = {}
    for arm, blobs in (("built", {}), ("loaded", entries)):
        (tmp_path / arm).mkdir()
        info, text = FA._run(tmp_path / arm, entry="(_pre() or mem)",
                             more="ROUNDS = 2\nBLOBS = %r\n" % (blobs,) + drv,
                             why="a compiled page on the boards' MicroPython: this is its check there")
        assert "refused" not in text, text[-2000:]
        out[arm] = info
    first = [c for c in out["loaded"]["calls"] if c[0] == 0]
    assert all(c[2] == 0 and c[3] == 0 for c in first), first
    assert out["loaded"]["kept"] == out["built"]["kept"], (out["loaded"]["kept"], out["built"]["kept"])
    assert out["loaded"]["forgot"] == out["loaded"]["desk"] == 0
