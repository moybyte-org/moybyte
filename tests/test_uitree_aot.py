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


def _reversed(request, d):
    """Dict `d` walked the other way from here on, in place (an engine holds
    the module's dicts themselves), and given back after the test."""
    items = list(d.items())
    d.clear()
    d.update(reversed(items))
    request.addfinalizer(lambda: (d.clear(), d.update(items)))


def _board_order(monkeypatch, request):
    """Every dict whose order could decide an index the C store or a blob
    keeps, in another order than the one the blobs were compiled with, as a
    board's dicts (and so its keyword arguments) keep them: each module's
    states (slots), actions, label tables and each table's keys, each
    model's fields and the document's fields."""
    import uitree as S
    monkeypatch.setattr(S, "DOC_FIELDS", dict(reversed(list(S.DOC_FIELDS.items()))))
    for m in MODULES:
        decl = S.decl_of(__import__(m))
        for t in decl.state._t.values():
            if isinstance(t, S.Model):
                monkeypatch.setattr(t, "fields", dict(reversed(list(t.fields.items()))))
        for tab in decl.labels.values():
            _reversed(request, tab)
        for d in (decl.state._t, decl.actions, decl.labels):
            _reversed(request, d)


@pytest.mark.parametrize("order", ("host", "board"))
@pytest.mark.parametrize("name", PORTS.C_RUNS)
def test_a_c_run_draws_its_record_with_its_pages_loaded(name, order, compiled, tmp_path, monkeypatch, request):
    if order == "board":
        _board_order(monkeypatch, request)
    run = [r for r in PORTS.RUNS if r[0] == name][0]
    moved = run[4] if len(run) > 4 else None
    built_frames = None
    if moved == P.BEHAVIOUR:
        # a run held to its states alone: its loaded frames are held to its built ones, up
        # to a playtest's first frame (a game's frames follow the clock, and the desk's
        # after it): the first that plays or shows another surface on top
        monkeypatch.setattr(uitree_aot, "BLOBS", {})
        built = P.check_recorded(name, PORTS.SURFACES[run[1]], tmp_path / "built", moved)
        top = built[0].state.split(" top=")[-1]
        built_frames = []
        for f in built:
            if "playing=True" in f.state or f.state.split(" top=")[-1] != top:
                break
            built_frames.append(f.digest())
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
    fs = P.check_recorded(name, PORTS.SURFACES[run[1]], tmp_path / "carts", moved)
    if built_frames is not None:
        moved_at = [i for i, (f, b) in enumerate(zip(fs, built_frames)) if f.digest() != b]
        assert not moved_at, "the loaded pages draw other frames than the built ones at steps %s" % moved_at
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
    head = blob[:28] + len(text).to_bytes(4, "little") + blob[32:at]
    with pytest.raises(uitree_aot.Refused, match="no action 'gone_%s'" % last.decode()):
        uitree_aot.load(eng, None, head + text)


@pytest.mark.parametrize("page, kind", (("achievements_ui.achievements_page", "model"),
                                        ("code_ui.code_page", "doc"), ("editor_ui.head", "enum")))
def test_a_blob_whose_fields_or_values_this_python_orders_otherwise_is_refused(page, kind, compiled,
                                                                              monkeypatch):
    """A model's fields, a document's or an enum's values in another order
    than the compile's (a dict walked otherwise) are refused by the load,
    and the page builds, rather than drawing one field's value as another's."""
    import uitree as S
    eng, blob = _engine(page, compiled)
    entry = uitree_aot.entry(blob)
    st = eng.s._t
    if kind == "doc":
        name = [n for n, t in st.items() if type(t) is S.Doc][0] + uitree_aot.DOC
        monkeypatch.setattr(S, "DOC_ORDER", S.DOC_ORDER[::-1])
    elif kind == "model":
        name = [n for n, t in st.items() if isinstance(t, S.Model)][0]
        monkeypatch.setattr(st[name], "order", st[name].order[::-1])
    else:
        name = [n for n, t in st.items() if type(t) is S.Enum][0]
        monkeypatch.setattr(st[name], "values", st[name].values[::-1])
    for b in (blob, entry):
        with pytest.raises(uitree_aot.Refused, match="state %s: its fields or values are in another order" % name):
            uitree_aot.load(eng, None, b)


def _pool(blob):
    at = 32
    at += 1 + blob[at]                  # the page id
    at += 1 + blob[at]                  # the variant
    at += 1                             # placed
    n = int.from_bytes(blob[at:at + 4], "little")
    return blob[at + 4:at + 4 + n]


def test_a_pages_pool_keeps_each_text_once(compiled):
    """A page's texts repeat (its props' names, their sources, its labels'
    words): the store keeps each once (`uit_pool_put`), but for the lists
    (an enum's values, a label table's keys and words), which are kept as
    they come."""
    import uitree as S
    for page, blob in compiled.pages.items():
        decl = S.decl_of(__import__(page.partition(".")[0]))
        pool = _pool(blob)
        lists = ["\0".join(t.values) for t in decl.state._t.values() if type(t) is S.Enum]
        for tab in decl.labels.values():
            lists += ["\0".join(str(k) for k in tab), "\0".join(str(tab[k]) for k in tab)]
        for text in lists:
            pool = pool.replace(text.encode("utf-8") + b"\0", b"", 1)
        texts = pool.split(b"\0")[:-1]
        twice = sorted(set(t for t in texts if texts.count(t) > 1))
        assert not twice, (page, twice[:8])


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


def test_staging_compiles_the_staged_pages_and_names_a_stale_one(compiled, tmp_path, capsys):
    mods = _staged(tmp_path)
    assert UC.stage(str(mods)) == 0
    assert "uitree pages: %d compiled from %d staged modules" % (len(compiled.pages), len(MODULES)) in capsys.readouterr().out
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


def test_a_board_with_no_desk_stages_no_desk_page(compiled, tmp_path, capsys):
    """A handheld's image carries no page only a desk opens (each SYSTEM
    module's `DESK_PAGES`: the Editor's workspaces, the Licences page's
    desk build); which boards have a desk is the shell's metric class of each
    board's glass."""
    desk = dict((d.name, UC.has_desk(d)) for d in (ROOT / "firmware").iterdir()
                if (d / "board.toml").exists())
    assert desk["lilygo_t_deck_plus_mainline"] is False and desk["guition_jc3248w535"] is False
    assert desk["esp32_p4_wifi6_touch_lcd_7b"] is True and desk["guition_jc8012p4a1c"] is True
    gone = UC.desk_pages()
    assert gone == set(["about_ui.desk_page"] + ["editor_ui.%s_space" % k for k in
                                                  ("settings", "code", "art", "world", "sound", "theme")])
    assert gone <= set(compiled.pages)
    mods = _staged(tmp_path)
    assert UC.stage(str(mods), desk=False) == 0
    assert "(no desk: no desk page)" in capsys.readouterr().out
    ns = {}
    exec((mods / "uitree_pages.py").read_text(), ns)
    assert set(ns["PAGES"]) == set(compiled.pages) - gone


def test_a_host_with_no_inflater_stages_every_page_on_the_c_engine_alone(compiled, tmp_path):
    """The step a CI build takes: its host library has no inflater's part
    (no desktop MicroPython tree, runtime/host/host_native.py), and every
    console page still compiles on it."""
    mods = _staged(tmp_path)
    code = ("import sys; sys.path.insert(0, 'tools'); import uitree_compile as UC; "
            "sys.exit(UC.main(['--staged', sys.argv[1], '--require']))")
    r = subprocess.run([sys.executable, "-c", code, str(mods)], cwd=ROOT, capture_output=True, text=True,
                       env=dict(os.environ, MOYHOST_NO_INFLATER="1"))
    assert r.returncode == 0, r.stderr[-2000:]
    assert "uitree pages: %d compiled" % len(compiled.pages) in r.stdout


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
    drv = M.DRIVER.replace('''    import uitree_c, uitree as S
''', '''    import uitree_c, uitree as S
    import uitree_aot
    uitree_aot.BLOBS = BLOBS
''')
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


FRAMES_DRIVER = r'''
LOADS = []


def frames(loop):
    """Every page the console opens on the C engine, built and then loaded
    from the host's blobs, in one boot: the frame each draws."""
    import moy_lcd
    import uitree_aot
    ws = loop.ws
    W, H = moy_lcd.WIDTH, moy_lcd.HEIGHT
    load = uitree_aot.load

    def counted(eng, x, blob):
        LOADS.append(uitree_aot.key(eng.pages[0]))
        return load(eng, x, blob)
    uitree_aot.load = counted

    def steps(n):
        for _ in range(n):
            loop.step()

    sl = lambda: ws.settings_layer
    def page(p):
        return lambda: sl().open_page(p)

    def code():
        ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Star Catcher"))
        steps(5)
        ws.text.set_menu_view("code")
        ed = ws.editor
        ed.set_text("\n".join("value_%d = probe(%d) + 1" % (i, i) for i in range(120)))
        ed.row, ed.col = 5, 3

    def leave():
        if ws.editor is not None:
            ws.spine.exit()
        ws.home.go_home()

    routes = [("settings_rows", None, ws.open_settings), ("tz", ws.open_settings, page("tz")),
              ("log", ws.open_settings, page("log")), ("permissions", ws.open_settings, page("permissions")),
              ("licences", ws.open_settings, page("licences")), ("paired", ws.open_settings, page("paired")),
              ("achievements", ws.open_settings, page("achievements")),
              ("wifi", ws.open_settings, page("wifi")), ("tasks", None, ws.open_tasks), ("code", None, code)]
    steps(40)
    out = {"routes": [r[0] for r in routes], "moved": {}, "loads": {}}
    shots = {}
    # a first round built: what the routes themselves move (an achievement's progress, its
    # toast) is moved before the two rounds compared
    for arm, blobs in (("warm", {}), ("built", {}), ("loaded", BLOBS)):
        uitree_aot.BLOBS = blobs
        for e in ALL:
            e.forget()
        del LOADS[:]
        for name, prep, opener in routes:
            if prep is not None:
                prep()
                steps(2)
            opener()
            steps(6)
            # the glass's buffers, in no order: a frame owed in one round and not the other
            # swaps which buffer holds the last frame, not what it shows
            shot = b"".join(sorted(bytes(moy_lcd.fb(i)) for i in range(moy_lcd.nfbs())))
            if arm == "built":
                shots[name] = shot
            elif arm == "loaded" and name != "log":         # the log's lines grow between rounds
                a, row = shots[name], W * 2
                rows = [y % H for y in range(len(a) // row) if a[y * row:(y + 1) * row] != shot[y * row:(y + 1) * row]]
                if rows:
                    out["moved"][name] = (rows[0], rows[-1], len(rows))
            leave()
            steps(4)
        out["loads"][arm] = list(LOADS)
    print("RESULT " + __import__("json").dumps(out))
'''


def test_on_the_boards_python_every_loaded_page_draws_its_built_frame(compiled, tmp_path):
    """The boards' MicroPython keeps its dicts, and so its keyword arguments,
    in another order than the CPython that compiled the blobs: every page the
    T-Deck's console opens, the Editor's Code tab among them (a document's
    status line: its caret and length), draws from its blob the frame it
    draws built there, in one boot."""
    import test_frame_alloc as FA
    import test_uitree_c_memory as M
    entries = dict((k, uitree_aot.entry(v)) for k, v in compiled.pages.items())
    info, text = FA._run(tmp_path, entry="(_pre() or frames)",
                         more="ROUNDS = 1\nBLOBS = %r\n" % (entries,) + M.DRIVER + FRAMES_DRIVER,
                         why="a compiled page's frame on the boards' MicroPython: this is its check there")
    assert "refused" not in text, text[-2000:]
    assert not info["loads"]["built"] and not info["loads"]["warm"], info["loads"]
    loaded = set(info["loads"]["loaded"])
    for page in ("settings_rows.rows_page", "achievements_ui.achievements_page", "tasks_ui.tasks_page", "code_ui.code_page",
                 "editor_ui.head"):
        assert page in loaded, (page, sorted(loaded))
    assert not info["moved"], info["moved"]
