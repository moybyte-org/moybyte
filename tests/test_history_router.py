"""HistoryRouter (#209 landing E) -- direct, mutation-checked.

`ws.history` is the #111 undo ROUTER: one bar pair over two undo mechanisms (each
Editor tab's in-RAM op stack, then the tab-scoped durable journal walk), the code
tab's typing burst, and the Stage-7 idle-typing autosave the frame loop ticks.

What this file covers is what the ROUTING gets wrong when it breaks, which the
goldens cannot see at all (no pixel moves) and which the existing #111 suites
(`test_op_history_wiring`, `test_journal_undo`, `test_journal_debounce`,
`test_graduation`) only reach through their happy paths: the ORDER the two
mechanisms are asked in, the guards that keep the paint path off the disk, the
burst lifecycle's two silent cases (net-cancel, editor rebind), the frame-loop
and keypress call SHAPES the temperature table (arch doc 3e) pins, and the
StoreHandle guard the walk re-derived by hand until this landing.
"""

import ast
from pathlib import Path

from ws_helpers import build_ws, quiesce

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "runtime" / "console.py"

SRC = "def _draw():\n    cls(1)\n"


def _cart_ws(tmp_path, src=SRC, title="Router", **kw):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(title, carts_dir, src=src, **kw)
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open_in_editor()
    return ws


def _open_code(ws, text=None):
    ws.set_menu_view("code")
    ws.screen = "menu"
    if text is not None:
        ws.editor.set_text(text)
        ws.editor.dirty = True
    return ws.editor


def _stroke(pe, x, y, color):
    pe.color = color
    pe.begin_stroke()
    pe.paint(x, y)
    pe.end_stroke()


def _fn(name):
    """The AST of one `Workstation` method."""
    tree = ast.parse(CONSOLE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Workstation")
    return next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


# -- ownership ---------------------------------------------------------------

def test_the_router_owns_its_state_with_no_console_mirror(tmp_path):
    """One author per piece of state (arch doc 2a). The burst pair and the
    debounce pair live on `ws.history` and NOWHERE else -- a mirror on the
    console would be a second author of a value the router disarms."""
    ws = build_ws(tmp_path)
    h = ws.history
    for name in ("_code_hist", "_code_burst_before", "edit_ms", "edit_debounce_ms"):
        assert hasattr(h, name), name
    for gone in ("_code_hist", "_code_burst_before", "_edit_ms", "_edit_debounce_ms",
                 "undo", "redo", "can_undo", "can_redo", "_bar_undo_bits",
                 "_journal_idle_tick", "_autosave_code", "_journal_walk"):
        assert not hasattr(ws, gone), gone + " is still on the console"


def test_the_router_takes_the_shared_store_handle(tmp_path):
    """The SAME `StoreHandle` the settings funnel and the roster take -- the
    walk re-derived the (store, root, can_manage, with_sd) guard by hand before
    this landing, which is the 4-tuple the handle exists to stop copying."""
    ws = build_ws(tmp_path)
    assert ws.history.store is ws.store
    assert ws.prefs.store is ws.store


# -- the routing ORDER -------------------------------------------------------

def test_undo_takes_the_local_op_before_the_journal(tmp_path):
    """The whole point of the object: local in-RAM ops FIRST, the durable walk
    only once they are exhausted. With a committed sprite AND a live stroke on
    top, one press reverts the stroke and leaves the FILE alone."""
    ws = _cart_ws(tmp_path)
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    ox, oy = ws.sheet.tile_origin(0)
    _stroke(pe, 0, 0, 5)
    ws.save_sprites()                    # commit: (0,0)=5 is now on disk
    on_disk = (Path(ws.cart["path"]) / "sprites.moygfx").read_text()
    _stroke(pe, 1, 1, 7)                 # a live, uncommitted stroke on top

    assert ws.history.undo() is True
    assert ws.sheet.pget(ox + 1, oy + 1) == 0   # the local stroke went...
    assert ws.sheet.pget(ox + 0, oy + 0) == 5   # ...and the commit did NOT
    assert (Path(ws.cart["path"]) / "sprites.moygfx").read_text() == on_disk


def test_undo_falls_through_to_the_journal_once_the_local_stack_is_empty(tmp_path):
    """...and the NEXT press crosses the boundary into the durable walk."""
    ws = _cart_ws(tmp_path)
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    ox, oy = ws.sheet.tile_origin(0)
    _stroke(pe, 0, 0, 5)
    ws.save_sprites()                    # commit V1: (0,0)=5
    _stroke(pe, 2, 2, 6)
    ws.save_sprites()                    # commit V2: + (2,2)=6
    _stroke(pe, 1, 1, 7)                 # a live stroke on top of both
    assert ws.history.undo() is True     # local
    assert ws.sheet.pget(ox + 2, oy + 2) == 6
    assert ws.history.undo() is True     # journal: V2 -> V1
    assert ws.sheet.pget(ox + 2, oy + 2) == 0
    assert ws.sheet.pget(ox + 0, oy + 0) == 5


def test_the_walk_is_handed_the_active_tabs_file_set(tmp_path, monkeypatch):
    """The scoped walk is a store ARGUMENT, not a filter applied afterwards --
    a mutant that passes None walks the whole project and reverts another tab."""
    ws = _cart_ws(tmp_path)
    _open_code(ws)
    seen = []
    real = ws.carts_store.journal_undo
    monkeypatch.setattr(ws.carts_store, "journal_undo",
                        lambda p, f=None: (seen.append(f), real(p, f))[1])
    ws.history._journal_walk(False)
    assert seen == [("main.py",)]


def test_the_scoped_set_names_the_carts_own_main_file(tmp_path):
    """#67: a lua cart's code tab walks `main.lua`, matching how its commits are
    named. A hard-coded "main.py" leaves every lua cart's UNDO dark."""
    ws = _cart_ws(tmp_path, src="function _draw() cls(1) end\n",
                  title="Lua", runtime="lua", main="main.lua")
    _open_code(ws)
    assert ws.history.active_tab_files() == ("main.lua",)


# -- the bar's dim state stays OFF the disk ----------------------------------

def test_bar_undo_bits_never_reads_the_journal(tmp_path, monkeypatch):
    """Its docstring's load-bearing claim, made executable: the bits are read
    once per bar-strip cache key, i.e. per painted bar frame, so consulting the
    SD-backed journal there would put a disk read in the paint path. Counted
    against a cart that HAS a walkable journal, so a mutant has something to
    find."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws, SRC + "# c1\n")
    assert ws.save_code() is True
    ed.set_text(SRC + "# c2\n"); ed.dirty = True
    assert ws.save_code() is True        # two commits -> the walk has a target
    reads = []
    for name in ("journal_can_undo", "journal_can_redo", "journal_undo",
                 "journal_redo"):
        real = getattr(ws.carts_store, name)
        monkeypatch.setattr(
            ws.carts_store, name,
            (lambda n, r: lambda *a, **k: (reads.append(n), r(*a, **k))[1])(name, real))
    assert ws.history.bar_undo_bits() == (False, False)
    assert reads == [], "the bar cache key reached the journal: " + repr(reads)
    # ...while the REPAINT-time query does read it, which is the contrast.
    assert ws.history.can_undo() is True
    assert reads == ["journal_can_undo"]


def test_bar_undo_bits_reports_an_unrecorded_typing_burst(tmp_path):
    """A burst holds a net change that is on no History yet; without it the bar
    UNDO icon stays dim until the debounce closes the burst ~1.5s later."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    assert ws.history.bar_undo_bits() == (False, False)
    ws.history.code_burst_open()
    ed.set_text(SRC + "# typed\n")
    assert ws.history.bar_undo_bits()[0] is True


def test_reading_the_bits_does_not_seal_the_burst(tmp_path):
    """The dim-state read is called from a cache KEY -- a side effect there would
    record an undo op on a frame the kid is still typing into."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    ws.history.code_burst_open()
    ed.set_text(SRC + "# typed\n")
    ws.history.bar_undo_bits()
    ws.history.can_undo()
    assert ws.history.code_op_history().can_undo() is False
    assert ws.history._code_burst_before is not None


# -- the typing burst lifecycle ----------------------------------------------

def test_a_burst_that_nets_back_to_its_start_records_nothing(tmp_path):
    """Typed then fully backspaced is not an undo step -- recording it would make
    the bar UNDO a no-op press the kid has to hit twice."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    ws.history.code_burst_open()
    ed.set_text(SRC + "# oops\n")
    ed.set_text(SRC)
    ws.history.close_code_burst()
    assert ws.history.code_op_history().can_undo() is False


def test_opening_a_burst_is_idempotent_so_a_run_of_keys_is_ONE_step(tmp_path):
    """`code_burst_open` fires on EVERY edit key (code_layer pokes it from nine
    sites). It must snapshot the pre-image ONCE: re-snapshotting per keystroke
    turns a typed word into one undo step per letter -- and, worse, makes the
    step revert to the middle of the word rather than to where the kid started."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    for tail in ("# a\n", "# ab\n", "# abc\n"):
        ws.history.code_burst_open()      # one poke per keystroke
        ed.set_text(SRC + tail)
    ws.history.close_code_burst()
    hist = ws.history.code_op_history()
    assert hist.can_undo() is True
    assert hist.undo() is not None
    assert ed.text() == SRC               # back to where the burst BEGAN
    assert hist.can_undo() is False       # ...in exactly one step


def test_a_rebuilt_editor_drops_the_stale_pre_image(tmp_path):
    """A journal walk / a reopen builds a FRESH CodeEditor. The History rebinds
    with it, and the open burst's pre-image must die with the old doc -- diffing
    one cart's text against another's is a corrupt op."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    ws.history.code_burst_open()
    ed.set_text(SRC + "# stale\n")
    assert ws.history._code_burst_before is not None
    ws.editor = None
    _open_code(ws)                        # a fresh CodeEditor over the same cart
    assert ws.history.code_op_history() is not None
    assert ws.history._code_burst_before is None
    assert ws.history.bar_undo_bits() == (False, False)


def test_undo_seals_a_live_burst_before_it_looks_for_a_step(tmp_path):
    """A just-typed, not-yet-recorded burst is undo's FIRST target -- otherwise
    the press skips past the kid's newest edit into an older commit."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    ws.history.code_burst_open()
    ed.set_text(SRC + "# newest\n")
    assert ws.history.undo() is True
    assert ed.text() == SRC


def test_redo_seals_a_stray_open_burst_too(tmp_path):
    """The same seal on the redo side: a stray open burst left behind a redo
    press must be recorded, not silently dropped when the doc is rewritten."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    ws.history.code_burst_open()
    ed.set_text(SRC + "# a\n")
    ws.history.undo()                     # seals + reverts -> a redo is available
    assert ws.history.redo() is True
    assert "# a" in ed.text()


def test_an_undone_buffer_stays_dirty_so_the_next_commit_persists_it(tmp_path):
    """set_text() clears the editor's dirty flag, so a local undo has to re-arm
    it -- without that the REVERT is what never reaches the disk, and the file
    silently keeps the text the kid just undid."""
    from runtime import console as C
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws)
    path = Path(ws.cart["path"]) / "main.py"
    ws.history.code_burst_open()
    ed.set_text(SRC + "# regretted\n")
    assert ws.history.undo() is True
    assert ed.dirty is True
    ws.history.edit_ms = C._ticks_ms() - 5000
    ws.history.idle_tick()
    assert "# regretted" not in path.read_text()


# -- the Stage-7 idle tick ---------------------------------------------------

def test_the_idle_tick_fires_on_a_frame_the_console_does_not_paint(tmp_path):
    """The Stage-7 property its comment records: the tick runs BEFORE the redraw
    gate, so the between-frames SD write lands on exactly the frame a static
    editor screen was going to skip. Moving the call after the gate leaves a kid
    who stops typing and stops touching the screen with an unsaved buffer."""
    from runtime import console as C
    ws = _cart_ws(tmp_path)
    _open_code(ws, SRC + "# quiet\n")
    quiesce(ws)                           # a visible cursor animates every frame
    ws.frame(1 / 30.0)                    # paint once, then let it go quiet
    ws.frame(1 / 30.0)
    painted = ws._frames_drawn
    ws.history.edit_ms = C._ticks_ms() - 5000
    ws.frame(1 / 30.0)
    assert ws._frames_drawn == painted, "the frame was not a quiet one"
    assert not ws.editor.dirty
    assert "# quiet" in (Path(ws.cart["path"]) / "main.py").read_text()


def test_the_idle_tick_early_outs_with_no_pending_edit(tmp_path):
    """The common path is EVERY frame of every session that is not typing, so it
    must cost one attribute test -- never the editor probe, never the commit."""
    ws = _cart_ws(tmp_path)
    _open_code(ws)
    calls = []
    ws.history._autosave_code = lambda: calls.append(1)
    assert ws.history.edit_ms is None
    for _ in range(5):
        ws.frame(1 / 30.0)
    assert calls == []


def test_the_idle_tick_disarms_when_the_edit_was_saved_elsewhere(tmp_path):
    """A hard commit (SAVE/PLAY/tab-leave) clears editor.dirty. The armed
    debounce must disarm rather than fire a second, redundant commit."""
    from runtime import console as C
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws, SRC + "# hard\n")
    assert ws.save_code() is True
    assert ed.dirty is False
    ws.history.edit_ms = C._ticks_ms() - 5000
    calls = []
    real = ws.project.commit_code
    ws.project.commit_code = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    ws.history.idle_tick()
    assert ws.history.edit_ms is None
    assert calls == []


# -- the StoreHandle guard ---------------------------------------------------

def test_writes_disabled_means_no_walk_and_no_check(tmp_path):
    """`can_manage` is False wherever the carts are baked into the image. There
    is no journal to walk there, and asking anyway is an SD read on a board that
    has no SD."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws, SRC + "# c1\n")
    assert ws.save_code() is True
    ed.set_text(SRC + "# c2\n"); ed.dirty = True
    assert ws.save_code() is True
    assert ws.history.can_undo() is True
    ws.can_manage = False
    assert ws.history.can_undo() is False
    assert ws.history.undo() is False
    ws.can_manage = True
    ws.carts_root = None                  # the other half of the handle's guard
    assert ws.history.can_undo() is False


def test_a_store_without_the_journal_verbs_is_a_safe_no_op(tmp_path):
    """An older/embedded store simply has no journal API. Probing it must be a
    dark icon, not an AttributeError inside a bar repaint."""
    ws = _cart_ws(tmp_path)
    _open_code(ws)

    class _Bare:
        def load(self, path):
            return None
    ws.carts_store = _Bare()
    assert ws.history.can_undo() is False
    assert ws.history.can_redo() is False
    assert ws.history.undo() is False
    assert ws.history.redo() is False


def test_a_failing_store_is_reported_and_never_crashes_the_shell(tmp_path,
                                                                 monkeypatch):
    """A walk runs from a bar TAP inside the frame loop; an exception there takes
    the whole console down with it."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws, SRC + "# c1\n")
    assert ws.save_code() is True
    ed.set_text(SRC + "# c2\n"); ed.dirty = True
    assert ws.save_code() is True

    def _boom(*a, **k):
        raise OSError("card gone")
    monkeypatch.setattr(ws.carts_store, "journal_undo", _boom)
    monkeypatch.setattr(ws.carts_store, "journal_can_undo", _boom)
    assert ws.history.undo() is False
    assert ws.history.can_undo() is False


# -- the walk's visible tail -------------------------------------------------

def test_a_walk_invalidates_the_bar_so_the_icons_re_check(tmp_path):
    """The cursor just moved, flipping can_undo()/can_redo(); the strip cache
    deliberately does NOT key on the journal, so the walk has to say so or the
    icons keep their stale enabled/dimmed look until something unrelated dirties
    the bar."""
    ws = _cart_ws(tmp_path)
    ed = _open_code(ws, SRC + "# c1\n")
    assert ws.save_code() is True
    ed.set_text(SRC + "# c2\n"); ed.dirty = True
    assert ws.save_code() is True
    ws.bar_layer.invalidate()
    gen = ws.bar_layer._bar_cache_gen
    assert ws.history.undo() is True
    assert ws.bar_layer._bar_cache_gen != gen


def test_a_walk_keeps_the_kids_place_in_the_code(tmp_path):
    """Owner report 2026-07-23: the rebuilt CodeEditor starts at the top, which
    read as "undo threw me to the start of the file". The caret is carried over
    the reload and clamped into a shrunken restore."""
    base = SRC + "".join("# %d\n" % i for i in range(12))
    ws = _cart_ws(tmp_path, src=base)
    ed = _open_code(ws)
    ed.set_text(base + "# v1\n"); ed.dirty = True
    assert ws.save_code() is True
    ed.set_text(base + "# v2\n"); ed.dirty = True
    assert ws.save_code() is True
    ed.goto_row(9, 0)
    assert ws.history.undo() is True
    assert ws.editor is not ed                     # a FRESH editor over fresh data
    assert ws.editor.row == 9


# -- the two call SHAPES the temperature table pins (arch doc 3e) ------------

def test_the_frame_loop_calls_the_router_directly(tmp_path):
    """`frame()` runs the tick every iteration, so it must name the collaborator
    -- never a `self._journal_idle_tick()` forward (doc 3b)."""
    calls = [n for n in ast.walk(_fn("frame"))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "idle_tick"]
    assert len(calls) == 1
    fn = calls[0].func
    assert isinstance(fn.value, ast.Attribute) and fn.value.attr == "history"
    assert isinstance(fn.value.value, ast.Name) and fn.value.value.id == "self"


def test_the_keypress_site_stores_the_tick_it_does_not_call(tmp_path):
    """`handle_input` re-arms the debounce on EVERY keystroke that reaches the
    code tab. Doc 3e: one attribute store, never a method call."""
    src = ast.dump(_fn("handle_input"))
    assert "edit_ms" in src
    stores = [n for n in ast.walk(_fn("handle_input"))
              if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Attribute) and t.attr == "edit_ms"
                      for t in n.targets)]
    assert len(stores) == 1
    target = stores[0].targets[0]
    assert isinstance(target.value, ast.Attribute) and target.value.attr == "history"
    calls = [n for n in ast.walk(_fn("handle_input"))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Attribute)
             and n.func.value.attr == "history"]
    assert calls == []
