"""`AppContext` -- the narrowed shell interface, and the ratchets that keep it
narrow (ui_refactor_2026-08 Phase 6, docs/app_api_v1.md).

An app used to hold `ws` and reach through it for anything, private members
included. It now holds a context carrying ONLY the roles its `NEEDS` tuple
declares. These tests are the load-bearing half of that: without them `ctx` is
merely an ergonomics object, and Phase 7's `make_system_api` has no interface to
filter.

Five families:

  * DECLARED NEEDS -- exact in both directions. Every role an app's source names
    must be declared (or it is an AttributeError at runtime), and every role it
    declares must be named (an over-declaration is a permission nobody needs,
    and Phase 7 hands those out).
  * the FILTER -- an undeclared role is absent from the object, not merely
    unused.
  * PERF -- zero `property` forwards, and the role hoist actually applied
    (a counter budget with a lower bound first, the test_top_bar.py idiom).
  * the ROLE VERBS THEMSELVES, driven against a real tmp_path store and checked
    on the filesystem (#208 rank 4). Everything above is structural: it pins
    which roles an app holds and says nothing about what a verb DOES, which is
    how a dozen of them reached three boards with only their `def` line ever
    executed.
  * the RATCHET -- no `ws.` left in the migrated modules, and `ctx.shell` (the
    escape hatch) has a pinned, shrink-only consumer list.
"""

import ast
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"

from ws_helpers import build_ws as _ws

from runtime import app_context as _ac
from runtime import chrome as _chrome
from runtime import moy_carts as _store
from runtime import system_api as _api
from runtime.app_decls import APPS


# Every (module, class) on the app side of the seam. The seven registered apps
# come from the generated declaration -- so a NEW app is covered by these tests
# the moment it is declared, with no list to remember here (Phase 5's point).
# ArtworkService is not a Layer and so is not in APPS, but it is on the app side
# and takes a context exactly as an app does.
def _app_targets():
    out = []
    for d in APPS:
        mod, _, cls = str(d["entry"]).partition(":")
        out.append((mod, cls))
    out.append(("artwork", "ArtworkService"))
    return out


APP_TARGETS = _app_targets()


def _class_node(mod_name, cls_name):
    tree = ast.parse((RUNTIME / (mod_name + ".py")).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            return node
    raise AssertionError("no class %s in runtime/%s.py" % (cls_name, mod_name))


def _roles_named(cls_node):
    """Every AppContext ROLE the class's source reaches, however it is spelled:
    `ctx.files`, `self.ctx.files`, or the hoist `f = ctx.files`. Every form goes
    through one `<something>.ctx.<role>` or `ctx.<role>` attribute node, so one
    walk catches them all."""
    found = set()
    for node in ast.walk(cls_node):
        if not isinstance(node, ast.Attribute) or node.attr not in _ac.ROLES:
            continue
        base = node.value
        if isinstance(base, ast.Name) and base.id == "ctx":
            found.add(node.attr)
        elif isinstance(base, ast.Attribute) and base.attr == "ctx":
            found.add(node.attr)
    return found


def _declared(mod_name, cls_name):
    mod = __import__("runtime." + mod_name, None, None, (cls_name,))
    return tuple(getattr(getattr(mod, cls_name), "NEEDS", ()))


# -- DECLARED NEEDS, exact in both directions ---------------------------------

@pytest.mark.parametrize("mod,cls", APP_TARGETS)
def test_every_app_declares_its_needs(mod, cls):
    needs = _declared(mod, cls)
    assert needs, cls + " declares no NEEDS -- see runtime/app_context.py"
    assert len(set(needs)) == len(needs), cls + " repeats a NEED"
    for name in needs:
        assert name in _ac.ROLES, cls + " declares unknown role " + name


@pytest.mark.parametrize("mod,cls", APP_TARGETS)
def test_an_app_touches_only_what_it_declares(mod, cls):
    """The forward direction. An undeclared role is an AttributeError the moment
    that line runs -- which may be a code path no test drives -- so this catches
    it statically, on every line of the class."""
    used = _roles_named(_class_node(mod, cls))
    undeclared = used - set(_declared(mod, cls))
    assert not undeclared, "%s reaches undeclared role(s) %s" % (
        cls, sorted(undeclared))


@pytest.mark.parametrize("mod,cls", APP_TARGETS)
def test_an_app_declares_nothing_it_does_not_use(mod, cls):
    """The reverse direction, and the more valuable half: a NEED nobody uses is
    a capability granted for nothing. Phase 7 keys `make_system_api` on exactly
    these tuples, so a stale entry becomes a real over-privilege."""
    declared = set(_declared(mod, cls))
    unused = declared - _roles_named(_class_node(mod, cls))
    assert not unused, "%s declares unused role(s) %s" % (cls, sorted(unused))


# -- the context is a FILTER, not a bag ---------------------------------------

def test_the_context_carries_only_the_declared_roles(tmp_path):
    ws = _ws(tmp_path)
    for _mod, cls in APP_TARGETS:
        app = ws.artwork if cls == "ArtworkService" else None
        if app is None:
            app = next(a for a, _t in ws._apps if type(a).__name__ == cls)
        ctx = app.ctx
        declared = set(_declared(_mod, cls))
        for role in _ac.ROLES:
            if role in declared:
                assert hasattr(ctx, role), cls + " lost declared role " + role
            else:
                assert not hasattr(ctx, role), \
                    "%s can reach undeclared role %s" % (cls, role)


def test_an_unknown_need_is_refused_at_construction(tmp_path):
    """A typo in a NEEDS tuple must fail loudly at boot, not silently grant
    nothing and AttributeError on some rare code path."""
    ws = _ws(tmp_path)
    with pytest.raises(ValueError):
        ws.app_context("demo", ("surface", "sirface"))


def test_a_context_with_no_needs_has_no_roles(tmp_path):
    ws = _ws(tmp_path)
    ctx = ws.app_context("demo")
    assert ctx.app_id == "demo"
    for role in _ac.ROLES:
        assert not hasattr(ctx, role)


# -- PERF: no property forwards ------------------------------------------------

def test_no_role_uses_a_property_forward():
    """MEASURED, not stylistic (ui_refactor_2026-08 Section 2.4): on this repo's
    unix MicroPython build, scaled by the P4 factor, a plain attribute hop costs
    +0.5us and the same forward written as a `@property` costs +5.1us. Across
    Settings' 137 per-frame accesses that is +69us versus +700us.

    Checked twice over -- the SOURCE (every decorator on every method, so a
    `@property` cannot arrive under an alias or inside a class the loop below
    never reaches) and the built classes' own descriptors."""
    tree = ast.parse((RUNTIME / "app_context.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            assert name != "property", \
                "app_context.py: %s is a property forward" % node.name
    for name in dir(_ac):
        obj = getattr(_ac, name)
        if not isinstance(obj, type):
            continue
        for attr, value in vars(obj).items():
            assert not isinstance(value, property), \
                "%s.%s is a property forward" % (name, attr)


# -- PERF: the hoist is applied -------------------------------------------------

_HOT = {
    # app id -> the cap on `ctx.surface.canvas()` calls in one drawn frame.
    # ONE is the target: `cv = self._surf.canvas()` at the top of draw(). The
    # caps are the MEASURED counts (2026-08-19) with no slack above the highest
    # observed value, so a canvas() call added inside a per-widget helper fails
    # here instead of costing a hop per widget on glass.
    "calc": 1, "artwork": 1, "appearance": 1, "writer": 1,
    "storybook": 1, "sheets": 1, "files": 1,
}


@pytest.mark.parametrize("kind", sorted(_HOT))
def test_the_canvas_role_is_hoisted_once_per_drawn_frame(tmp_path, kind):
    """The hoist mandate as a counter budget. `ctx.surface` is a plain attribute
    and cannot be counted without adding the very descriptor this file forbids,
    so the countable signal is the role's own verb: how many times a frame asks
    the surface role for the canvas.

    LOWER BOUND FIRST -- without it the test passes when the app stops drawing
    at all, or when the counter is wired to the wrong object, which is exactly
    how a budget quietly stops being one (test_top_bar.py records the same
    lesson from a real regression)."""
    ws = _ws(tmp_path)
    app = ws._apps_by_id[kind]
    assert ws.open_app(app), kind + " has no identity cart"
    surf = app.ctx.surface
    calls = [0]
    real = surf.canvas

    def counted():
        calls[0] += 1
        return real()

    surf.canvas = counted
    frames = 4
    for _ in range(frames):
        ws._dirty = True
        app.draw(1 / 30.0)
    assert calls[0] >= frames, \
        "no canvas() call counted in %d frames -- is the counter on the right role?" \
        % frames
    per_frame = calls[0] / float(frames)
    assert per_frame <= _HOT[kind], \
        "%s asks ctx.surface for the canvas %.1f times per frame (cap %d): hoist it" \
        % (kind, per_frame, _HOT[kind])


def _quiesce_frame(ws):
    """Take everything transient or clock-driven off the frame -- the shell-golden
    recipe (tests/test_app_api.py carries the same list). Without it the toast
    timers, the FPS chip and the OS bar's live clock dirty the shell on their own
    and every redraw-gate assertion reads as a failure of whatever changed last."""
    if ws.pointer is not None:
        ws.pointer.visible = False
    ws._toast_until = 0
    ws._egg_until = 0
    ws._confetti_until = 0
    ws.show_achievements = False
    ws.show_fps = False
    ws.perf_hud = False
    ws.perf_capture = False
    ws.sysmenu.open = False
    ws._about = False
    ws._notice = None
    ws._notice_until = 0
    ws.bar_layer._clock_text = lambda: "00:00"


def test_the_no_store_sentinel_survives_the_dual_import(tmp_path):
    """`_persist` distinguishes "no storage" from "the write failed" by IDENTITY
    (`err is NO_STORE`), and every module reaches `app_context` through the
    two-namespace ladder this tree writes everywhere (`import app_context`, else
    `from runtime import ...`). If those two paths ever produced two module
    objects, the sentinel would compare unequal and every no-store failure would
    render as CAN'T SAVE None instead of CAN'T SAVE HERE."""
    from runtime import app_shell
    from runtime import artwork
    assert app_shell.NO_STORE is _ac.NO_STORE
    assert artwork.NO_STORE is _ac.NO_STORE
    ws = _ws(tmp_path)
    ws.carts_store = None
    _v, err = ws.app_context("demo", ("files",)).files.load("docs", "x")
    assert ws.sheets_app._persist((None, err)) is False
    assert ws.sheets_app.status == "CAN'T SAVE HERE"


# -- PERF: the roles are built at BOOT, never per frame ------------------------

_ROLE_CLASSES = ("Damage", "Surface", "Theme", "Files", "Carts", "Nav", "Prefs",
                 "Notify", "WallpaperRole", "_RawFiles", "_RawCarts")


def test_role_objects_are_allocated_once_at_boot_and_never_per_frame(tmp_path,
                                                                     monkeypatch):
    """An allocation budget in this repo's idiom (test_top_bar.py): count on the
    construction path only, so the net is free in the healthy case, and put the
    LOWER BOUND FIRST so a deleted counter fails instead of passing.

    What it guards: a context rebuilt per frame -- the obvious way to make
    `ctx` "always fresh" and the one that would put eleven object allocations
    into every frame on a board with ~23KB of internal SRAM free in play."""
    built = []
    for name in _ROLE_CLASSES:
        cls = getattr(_ac, name)
        real = cls.__init__

        def counted(self, *a, _r=real, _n=name, **kw):
            built.append(_n)
            return _r(self, *a, **kw)

        monkeypatch.setattr(cls, "__init__", counted)

    ws = _ws(tmp_path)
    boot = len(built)
    # Lower bound first: eight contexts (seven apps + ArtworkService), each with
    # several roles, are built during boot. A zero here means the counter is not
    # on the construction path any more.
    assert boot >= 8, "no role construction counted at boot (%d)" % boot

    app = ws._apps_by_id["files"]
    assert ws.open_app(app)
    del built[:]
    for _ in range(24):
        ws._dirty = True
        ws.frame(1 / 30.0)
    assert built == [], \
        "%d role object(s) allocated in 24 frames: %s" % (len(built),
                                                          sorted(set(built)))


@pytest.mark.parametrize("kind", ("calc", "files", "sheets", "writer"))
def test_an_open_app_still_paints_only_on_damage(tmp_path, kind):
    """The redraw gate, per app. `ctx.damage.all()` replaced ~90 `ws._dirty =
    True` assignments, and a role verb called where a bare assignment was not
    would dirty the shell every frame -- invisible except as a flat battery.
    ui_refactor_2026-08 Section 7 names an idle scenario that starts capturing
    frames as its own regression check.

    The app's HANDLERS are driven too, not just `frame()`. That is the whole
    net: `_dirty` is cleared AFTER the draw (console.py's frame tail), so a
    `damage.all()` inside `draw()` is structurally swallowed and only the input
    and pointer paths can dirty a quiet frame -- verified by perturbation, which
    is also how this test was caught being vacuous before the handlers were
    added."""
    ws = _ws(tmp_path)
    app = ws._apps_by_id[kind]
    assert ws.open_app(app), kind + " has no identity cart"

    def quiet_loop():
        _quiesce_frame(ws)
        app.handle_input(ws.input)        # no key down, no button pressed
        app.handle_pointer(-1, -1, False)  # a sample off the surface, no click
        ws.frame(1 / 30.0)

    for _ in range(3):                    # the entry paint + any settling
        quiet_loop()
    before = ws._frames_drawn
    assert before >= 1, "the app never painted -- is _frames_drawn still wired?"
    for _ in range(12):
        quiet_loop()
    painted = ws._frames_drawn - before
    assert painted <= 1, \
        "%s painted %d of 12 quiet frames: something calls damage.all() per frame" \
        % (kind, painted)


# -- storage: (value, err), never a raise --------------------------------------

def test_storage_verbs_report_no_store_instead_of_raising(tmp_path):
    """`ctx.files`/`ctx.carts` return `(value, err)` -- the contract
    `app_shell._persist` hand-rolled in three apps. With no store wired, `err`
    is the NO_STORE singleton, which is what maps to CAN'T SAVE HERE rather than
    CAN'T SAVE <why>."""
    ws = _ws(tmp_path)
    ctx = ws.app_context("demo", ("files", "carts"))
    ws.carts_store = None
    assert ctx.files.ready() is False
    for res in (ctx.files.load("docs", "x"),
                ctx.files.save("docs", "x", "y"),
                ctx.files.trash_list(),
                ctx.files.batch(lambda f: f.list("docs")),
                ctx.carts.load_deck({"path": "/nope"}),
                ctx.carts.save_code({"path": "/nope"}, "x")):
        value, err = res
        assert value is None
        assert err is _ac.NO_STORE


def test_a_failing_store_surfaces_as_err_text_not_an_exception(tmp_path):
    ws = _ws(tmp_path)
    ctx = ws.app_context("demo", ("files",))

    class _Boom:
        @staticmethod
        def load_file(kind, name, root):
            raise OSError("disk on fire")

        @staticmethod
        def save_file(kind, name, blob, root):
            raise OSError("disk on fire")

    ws.carts_store = _Boom()
    value, err = ctx.files.load("docs", "x")
    assert value is None and "disk on fire" in str(err) and err is not _ac.NO_STORE
    value, err = ctx.files.save("docs", "x", "y")
    assert value is None and "disk on fire" in str(err)


def test_the_persist_status_contract_still_distinguishes_the_two(tmp_path):
    """`_persist` maps NO_STORE to CAN'T SAVE HERE and anything else to
    CAN'T SAVE <why>. Those strings are DRAWN, so this pins the seam that used
    to be three hand-rolled try/excepts."""
    ws = _ws(tmp_path)
    app = ws.sheets_app
    ws.carts_store = None
    assert app._persist((None, _ac.NO_STORE)) is False
    assert app.status == "CAN'T SAVE HERE"
    assert app._save_failed is True
    assert app._persist((None, "disk on fire")) is False
    assert app.status.startswith("CAN'T SAVE disk")
    assert app._persist((1, None)) is True
    assert app._save_failed is False


def test_the_no_store_sentinel_renders_as_words(tmp_path):
    """`_persist` branches on IDENTITY, so nothing formats this today -- which
    is exactly why it has to stay right. The moment a caller renders the pair
    instead of branching, a kid reads either NO STORAGE or a repr with a memory
    address in it."""
    assert str(_ac.NO_STORE) == "NO STORAGE"
    ws = _ws(tmp_path)
    ws.carts_store = None
    _v, err = ws.app_context("demo", ("files",)).files.save("docs", "x", "y")
    assert "%s" % (err,) == "NO STORAGE"


# -- prefs are namespaced, and the shipped key did not move -------------------

def test_prefs_namespace_defaults_to_the_app_id(tmp_path):
    ws = _ws(tmp_path)
    ctx = ws.app_context("demo", ("prefs",))
    ctx.prefs.set("scroll", 7, persist=False)
    assert ws.system["demo_scroll"] == 7
    assert ctx.prefs.get("scroll") == 7
    ctx.prefs.clear("scroll", persist=False)
    assert "demo_scroll" not in ws.system
    assert ctx.prefs.get("scroll", "fallback") == "fallback"


def test_paints_document_pointer_keeps_its_on_disk_key(tmp_path):
    """The namespace is a constructor argument and not a hard-wired app id for
    exactly one reason: `paint_doc` has been the key in real cards' system.json
    since #108. Renaming it would lose every kid's open drawing on the next
    boot, silently."""
    ws = _ws(tmp_path)
    ws.artwork._set_doc_name("hello")
    assert ws.system.get("paint_doc") == "hello"
    assert ws.artwork.doc_name() == "hello"


def test_prefs_writes_and_clears_reach_system_json(tmp_path):
    """The default `persist=True` path -- the one a real app takes, and the one
    no test drove. Both halves have to reach the FILE: a cleared key that only
    left the in-RAM dict is back on the next boot."""
    ws = _ws(tmp_path)
    prefs = ws.app_context("demo", ("prefs",)).prefs
    prefs.set("doc", "sketch_1")
    assert _system_json(ws)["demo_doc"] == "sketch_1"
    prefs.clear("doc")
    assert "demo_doc" not in ws.system
    assert "demo_doc" not in _system_json(ws)


# -- the role verbs, EXECUTED against a real store (#208 rank 4) ---------------
#
# Everything above this line is structural: it pins WHICH roles an app holds.
# A statement-level sweep of the whole suite found TEN verbs on this module
# whose bodies had never executed -- the seven #208 names (`Files.count`,
# `Files.empty_trash`, `Files.decode_text`, `_RawFiles.empty_trash`,
# `Carts.save_deck`, `Carts.images`, `Theme.set_variant`) plus `Files.history`,
# `_RawFiles.history` and `_NoStore.__str__` -- and two branches nothing
# reached: `Nav.open_app`'s id resolution and `Prefs.clear`'s persist.
#
# So each test below drives the verb and asserts what landed on the FILESYSTEM,
# not what the call returned -- a listing built by the same module that did the
# write agrees with itself whether or not the bytes moved.


def _system_json(ws):
    """`system.json` as it is ON DISK -- `ws.system` is the live dict every
    persist verb also mutates, so reading it back proves nothing about the
    write."""
    with open(_store.system_store_path(ws.carts_root)) as fh:
        return json.load(fh)


def _trash_dir(ws, kind):
    return "%s/%s/%s" % (_store.files_root(ws.carts_root), _store.TRASH_DIR, kind)


def _kind_ext(kind):
    return _store.FILE_KINDS[kind][0]


def _files(tmp_path):
    ws = _ws(tmp_path)
    return ws, ws.app_context("demo", ("files",)).files


def _new_cart(carts, title):
    """A cart of this test's own making. Borrowing `ws.carts.all[0]` makes an
    assertion about a cart's FOLDER hostage to whichever seed sorts first --
    one with a deck.json or an images/ dir would read as a role verb that
    wrote when it should not have."""
    cart, err = carts.batch(lambda raw: raw.create(title, type="story"))
    assert err is None, err
    return cart


# -- ctx.files -----------------------------------------------------------------

def test_files_count_is_the_kinds_item_count_not_its_listing(tmp_path):
    """`count_files` exists so the Files kinds screen never pays `list_files`'
    per-item stat+sort just for a badge. The role verb therefore has to return
    the NUMBER, and it has to track the same directory the listing walks -- a
    badge that disagrees with the screen behind it is the whole failure mode."""
    ws, files = _files(tmp_path)
    assert files.count("docs") == (0, None)
    for name in ("one", "two", "three"):
        assert files.save("docs", name, _store.encode_text(name))[1] is None
    n, err = files.count("docs")
    assert err is None
    assert n == 3
    assert n == len(files.list("docs")[0])
    assert n == len(os.listdir(_store.file_kind_dir("docs", ws.carts_root)))
    assert files.delete("docs", "two")[1] is None
    assert files.count("docs") == (2, None)
    assert files.count("drawings") == (0, None)     # a kind nobody has written
    assert files.count("nope")[0] is None           # and a kind that is not one


def test_empty_trash_destroys_every_trashed_item_on_disk(tmp_path):
    """The trash is the kid's recovery bin and EMPTY is the one verb that
    really destroys, so it must clear every KIND rather than the first one it
    finds -- and the files have to be gone from the filesystem, not merely
    absent from a listing the same module builds."""
    ws, files = _files(tmp_path)
    assert files.save("docs", "note", _store.encode_text("hi"))[1] is None
    assert files.save("tables", "grid", "{}")[1] is None
    assert files.delete("docs", "note")[1] is None
    assert files.delete("tables", "grid")[1] is None
    listed, err = files.trash_list()
    assert err is None and sorted(listed) == [("docs", "note"), ("tables", "grid")]
    dirs = [_trash_dir(ws, k) for k in ("docs", "tables")]
    assert [os.listdir(d) for d in dirs] == [["note" + _kind_ext("docs")],
                                             ["grid" + _kind_ext("tables")]]
    assert files.empty_trash() == (None, None)
    assert files.trash_list() == ([], None)
    for d in dirs:
        assert os.listdir(d) == [], d


def test_empty_trash_is_a_WRITE_and_a_read_only_store_keeps_the_trash(tmp_path):
    """`can_manage` is False wherever the carts are baked into the image.
    Routing a destroying verb through `_read` would let such a build wipe a bin
    it has no business touching, and the `(None, NO_STORE)` pair is what tells
    the app to say CAN'T SAVE HERE instead of reporting an empty trash."""
    ws, files = _files(tmp_path)
    files.save("docs", "note", _store.encode_text("hi"))
    files.delete("docs", "note")
    ws.can_manage = False
    assert files.readable() is True and files.ready() is False
    assert files.empty_trash() == (None, _ac.NO_STORE)
    assert os.listdir(_trash_dir(ws, "docs")) == ["note" + _kind_ext("docs")]
    assert files.trash_list()[0] == [("docs", "note")]     # reads still work


def test_stamp_passes_the_blob_through_when_there_is_no_store(tmp_path):
    """The codec verbs promise not to raise, and `stamp` was the one without a
    store guard -- on a build with no store it raised AttributeError from inside
    Paint's copy-on-use path, where every neighbouring call degrades quietly.

    It passes the blob THROUGH rather than returning None, matching what
    `stamp_provenance` already does with a blob it cannot parse: None here would
    be handed to the save that follows (artwork.py:1134), losing the drawing."""
    ws, files = _files(tmp_path)
    blob = _store.encode_moyimg(1, 1, bytearray([0]))
    ws.carts_store = None
    assert files.sig(blob) is None                  # the siblings' degradation
    assert files.stamp(blob, "drawings", "kite", 7) == blob
    ws.carts_store = _store
    stamped = files.stamp(blob, "drawings", "kite", files.sig(blob))
    assert stamped != blob and "drawings/kite" in stamped


def test_the_raw_view_runs_the_same_verbs_inside_one_session(tmp_path):
    """`batch(fn)` hands `fn` the RAW view -- bare values, exceptions
    propagating -- so that no verb name ever has two return shapes. `_RawFiles`
    is a separate class for exactly that, and its bodies are reachable ONLY
    this way, so nothing else in the suite executes them."""
    ws, files = _files(tmp_path)
    files.save("docs", "note", _store.encode_text("hi"))
    files.history_commit("docs", "note", [["ins", 1]])
    files.delete("docs", "note")
    seen, err = files.batch(lambda raw: (raw.count("docs"),
                                         raw.trash_list(),
                                         raw.history("docs", "note"),
                                         raw.empty_trash(),
                                         raw.trash_list()))
    assert err is None
    assert seen == (0, [("docs", "note")], [], None, [])
    assert os.listdir(_trash_dir(ws, "docs")) == []
    # RAW means a failure RAISES -- and `batch`'s one try/except is what turns
    # it back into the module's `(value, err)`.
    value, err = files.batch(lambda raw: raw.count("nope"))
    assert value is None and "nope" in str(err) and err is not _ac.NO_STORE


def test_decode_text_reads_a_stored_document_and_never_a_bare_string(tmp_path):
    """A `.moytext` holds a `moytext-v1` blob, not a string. The codec is on the
    role so a USER APP writes what Writer and Files can read back -- a bare
    string decodes to nothing, silently, and looks exactly like a save that did
    not happen."""
    ws, files = _files(tmp_path)
    blob = files.encode_text("HELLO\nWORLD")
    assert files.save("docs", "greeting", blob)[1] is None
    stored, err = files.load("docs", "greeting")
    assert err is None and stored == blob
    assert files.decode_text(stored) == ["HELLO", "WORLD"]
    assert files.decode_text("HELLO\nWORLD") == []       # the bare-string trap
    assert files.decode_text(files.encode_text("")) == []
    assert files.decode_text("") == [] and files.decode_text(None) == []
    ws.carts_store = None
    assert files.decode_text(blob) == []                 # no store, no codec


def test_the_history_role_reads_back_what_history_commit_wrote(tmp_path):
    """#111's op-history sidecar, through the role. `history()` is the raw
    records; `history_ops()` is the window AFTER the last keyframe, which is
    the seed an `op_history.History` takes -- a reader that confuses the two
    re-seeds ops the keyframe already accounts for."""
    ws, files = _files(tmp_path)
    files.save("docs", "note", _store.encode_text("hi"))
    assert files.history("docs", "note") == ([], None)    # no sidecar yet
    assert files.history_commit("docs", "note", [["ins", 1]],
                                keyframe={"lines": ["hi"]}) == (None, None)
    assert files.history_commit("docs", "note", [["ins", 2]]) == (None, None)
    sidecar = "%s/%s/docs/note%s" % (_store.files_root(ws.carts_root),
                                     _store.HISTORY_DIR, _store.HISTORY_EXT)
    assert os.path.exists(sidecar)
    recs, err = files.history("docs", "note")
    assert err is None
    assert [r["t"] for r in recs] == ["kf", "seg", "seg"]
    assert recs[0]["doc"] == {"lines": ["hi"]}
    assert files.history_ops("docs", "note") == ([["ins", 1], ["ins", 2]], None)
    files.history_commit("docs", "note", [["ins", 3]], keyframe={"lines": ["ho"]})
    assert files.history("docs", "note")[0][-3:][0]["t"] == "kf"
    assert files.history_ops("docs", "note") == ([["ins", 3]], None)


# -- ctx.carts -----------------------------------------------------------------

def test_save_deck_writes_the_deck_into_the_carts_own_folder(tmp_path):
    """Storybook's authoring verb, and the reason `carts` is a role of its own:
    it writes into a CART, which is executable content."""
    ws = _ws(tmp_path)
    carts = ws.app_context("demo", ("carts",)).carts
    cart = _new_cart(carts, "Deck Test")
    deck = '{"format": "moydeck-v1", "pages": []}'
    assert carts.load_deck(cart) == (None, None)          # never a deck before
    assert carts.save_deck(cart, deck) == (None, None)
    path = cart["path"] + "/" + _store.DECK_NAME
    with open(path) as fh:
        assert fh.read() == deck
    assert carts.load_deck(cart) == (deck, None)
    ws.can_manage = False
    assert carts.save_deck(cart, "{}") == (None, _ac.NO_STORE)
    with open(path) as fh:
        assert fh.read() == deck                          # the refusal wrote nothing


def test_carts_images_reads_the_assets_by_name_from_a_cart_or_a_path(tmp_path):
    """`moy_carts.load_images` takes the cart's PATH, so the raw verb accepts a
    path string as well as a cart dict. Both spellings are live -- and the
    dict branch is the one that would silently return `{}` for every cart if
    the unwrap were dropped, because a dict is not a path and a missing
    `images/` dir is not an error."""
    ws = _ws(tmp_path)
    carts = ws.app_context("demo", ("carts",)).carts
    cart = _new_cart(carts, "Art Test")
    assert carts.images(cart) == ({}, None)               # no images/ dir yet
    blob = carts.encode_image(1, 1, [0])
    assert carts.save_image(cart, "star", blob)[1] is None
    assert carts.save_image(cart, "moon", blob)[1] is None
    by_cart, err = carts.images(cart)
    assert err is None
    assert sorted(by_cart) == ["moon", "star"] and by_cart["star"] == blob
    assert carts.images(cart["path"]) == (by_cart, None)
    assert sorted(os.listdir(cart["path"] + "/" + _store.IMAGES_DIR)) == \
        ["moon" + _store.IMAGE_EXT, "star" + _store.IMAGE_EXT]


# -- ctx.theme -----------------------------------------------------------------

def test_set_variant_flips_the_live_tokens_and_persists_them(tmp_path):
    """The DARK/LIGHT half of the theme (visual_identity_v1 4.3). Three things
    have to happen together: `light()` -- the gate every surface's light branch
    reads -- follows; `ws.theme_colors` is REBOUND, because the launcher's cache
    keys fold `id(ws.theme_colors)` and the rebind IS the invalidation; and the
    choice reaches system.json or it is gone at the next boot."""
    ws = _ws(tmp_path)
    theme = ws.app_context("demo", ("theme",)).theme
    assert theme.variant() == "dark" and theme.light() is False
    before = theme.colors()
    family = theme.name()
    theme.set_variant("light")
    assert theme.variant() == "light" and theme.light() is True
    assert theme.name() == family                    # the FAMILY does not move
    assert theme.colors() is not before
    assert ws.system["theme_variant"] == "light"
    assert _system_json(ws)["theme_variant"] == "light"


def test_set_variant_without_persist_leaves_the_stored_choice_alone(tmp_path):
    ws = _ws(tmp_path)
    theme = ws.app_context("demo", ("theme",)).theme
    theme.set_variant("light")
    theme.set_variant("dark", persist=False)
    assert theme.variant() == "dark" and theme.light() is False
    assert _system_json(ws)["theme_variant"] == "light"


def test_an_unknown_variant_falls_back_instead_of_sticking(tmp_path):
    """It must land on the DEFAULT, not keep the current one: the tokens are
    looked up by this string on every theme swap, and an unknown value stored
    in system.json would be re-applied at every boot."""
    ws = _ws(tmp_path)
    theme = ws.app_context("demo", ("theme",)).theme
    theme.set_variant("light")
    theme.set_variant("purple")
    assert theme.variant() == _chrome.DEFAULT_VARIANT
    assert _system_json(ws)["theme_variant"] == _chrome.DEFAULT_VARIANT


# -- ctx.nav -------------------------------------------------------------------

def test_nav_opens_a_registered_app_by_id_and_refuses_an_unknown_one(tmp_path):
    """`open_app` resolves by REGISTERED id so an app never holds a reference
    to another app's class. That branch is also what `system_api` publishes to
    a `"launch"`-granted cart, whose argument is a string out of a manifest
    nobody validated -- so the miss has to be False, not a traceback."""
    ws = _ws(tmp_path)
    nav = ws.app_context("demo", ("nav",)).nav
    assert ws.screen == "launcher"
    assert nav.app("no_such_app") is None
    assert nav.open_app("no_such_app") is False
    assert ws.screen == "launcher"
    assert nav.app("calc") is ws._apps_by_id["calc"]
    assert nav.open_app("calc") is True
    assert ws.screen == "calc"


# -- the CART-facing narrowing of ctx.files (system_api.ScopedFiles) -----------
#
# tests/test_user_apps.py pins the manifest -> grant FILTER (an ungranted name
# is absent, a scoped grant cannot name another kind). What no test executed is
# the granted verbs themselves: `ready`/`load`/`save`/`delete`/`rename`/
# `duplicate`/`load_text` had never run.

def test_the_scoped_handle_acts_on_its_granted_kind_only(tmp_path):
    """The kind is bound at construction and is never an argument, so every
    published verb spells ONE kind -- the granted one. Driven here against a
    real store so the claim is about where bytes landed."""
    ws, files = _files(tmp_path)
    scoped = _api.ScopedFiles(files, "docs")
    assert scoped.ready() is True
    assert scoped.save_text("note", "HELLO\nWORLD") == ("note", None)
    assert scoped.load_text("note") == ("HELLO\nWORLD", None)
    assert scoped.load("note") == (_store.encode_text("HELLO\nWORLD"), None)
    assert scoped.save("plain", "{}") == ("plain", None)
    assert scoped.duplicate("note") == ("note_2", None)
    assert scoped.rename("note_2", "copy") == ("copy", None)
    assert sorted(scoped.list()[0]) == ["copy", "note", "plain"]
    assert scoped.delete("copy") == ("copy", None)
    assert sorted(scoped.list()[0]) == ["note", "plain"]
    ext = _kind_ext("docs")
    assert sorted(os.listdir(_store.file_kind_dir("docs", ws.carts_root))) == \
        ["note" + ext, "plain" + ext]
    for kind in ("drawings", "tables", "sprites", "music"):
        assert files.count(kind) == (0, None), kind


def test_the_scoped_handle_degrades_to_NO_STORE_rather_than_raising(tmp_path):
    """A cart written by a kid must not be able to crash on a missing card, and
    a try/except around every save is not a thing to teach.

    `save_text`'s own `blob is None -> NO_STORE` guard is an EQUIVALENT MUTANT
    under this (deleting it still yields `(None, NO_STORE)`): `encode_text`
    returns None only when there is no store, and `save` is gated on the same
    readiness, so the guard short-circuits a path that already answers the same
    way. It is kept as a short-circuit, not as behaviour, and no test can tell
    the difference."""
    ws, files = _files(tmp_path)
    scoped = _api.ScopedFiles(files, "docs")
    ws.carts_store = None
    assert scoped.ready() is False
    assert scoped.save_text("note", "hi") == (None, _ac.NO_STORE)
    assert scoped.load_text("note") == ("", _ac.NO_STORE)
    for res in (scoped.list(), scoped.load("note"), scoped.save("note", "x"),
                scoped.delete("note"), scoped.rename("note", "x"),
                scoped.duplicate("note"), scoped.new_name()):
        assert res == (None, _ac.NO_STORE)


# -- the cart globals `make_system_api` publishes ------------------------------
#
# The same sweep found every one of these closures with a `def` line and no
# body: the ungated `theme()`/`screen()`/`bar_h()`, and the `"appearance"` and
# `"launch"` grants. test_user_apps.py pins which NAMES appear for which
# manifest; nothing called them.

def test_the_ungated_verbs_answer_for_every_app_cart(tmp_path):
    """`ui`/`theme()`/`screen()`/`bar_h()` are published with no permission at
    all, because they are not capabilities -- they are how an app draws.
    `bar_h` exists so a cart stops hardcoding 18 (a documented defect in
    several seed carts), so it has to answer a number where the host passes no
    strip rather than raise."""
    ws = _ws(tmp_path)
    canvas = ws.sys_canvas
    ns = _api.make_system_api(ws.app_context, {"title": "Plain"},
                              canvas=canvas, bar_h=lambda: 18)
    assert ns["ui"] is not None
    assert ns["screen"]() is canvas
    assert ns["bar_h"]() == 18
    assert ns["theme"]() is ws.theme_colors
    ws.look.set_theme("berry")
    assert ns["theme"]() is ws.theme_colors      # live tokens, never a snapshot
    bare = _api.make_system_api(ws.app_context, {"title": "Plain"})
    assert bare["screen"]() is None and bare["bar_h"]() == 0


def test_the_appearance_grant_publishes_a_working_theme_picker(tmp_path):
    """READING the theme is ungated; CHANGING it is the `"appearance"` grant.
    The two published functions are bound verbs and not the role, so a granted
    cart can restyle the console and reach nothing else through them."""
    ws = _ws(tmp_path)
    ns = _api.make_system_api(ws.app_context,
                              {"title": "Skinner", "permissions": ["appearance"]})
    assert sorted(ns) == ["bar_h", "screen", "set_theme", "theme", "themes", "ui"]
    # NAMES, one per installed theme -- the round trip the docstring promises,
    # since `set_theme` takes a name and falls back to DEFAULT_THEME silently on
    # anything else. Feeding the picker's own output back is the case that used
    # to always land on "night".
    assert ns["themes"]() == [n for n, _t in _chrome.THEMES]
    for name in ns["themes"]():
        ns["set_theme"](name)
        assert ws.look.theme_name == name
    ns["set_theme"]("berry")
    assert ws.look.theme_name == "berry"
    assert ns["theme"]() is ws.theme_colors
    assert _system_json(ws)["theme"] == "berry"


def test_the_launch_grant_opens_apps_by_id_and_nothing_else(tmp_path):
    ws = _ws(tmp_path)
    ns = _api.make_system_api(ws.app_context,
                              {"title": "Jumper", "permissions": ["launch"]})
    assert "open_app" in ns
    assert "set_theme" not in ns and "files" not in ns and "prefs" not in ns
    assert ns["open_app"]("no_such_app") is False
    assert ws.screen == "launcher"
    assert ns["open_app"]("calc") is True
    assert ws.screen == "calc"


def test_an_untitled_cart_still_gets_a_stable_prefs_namespace():
    """`app_id_for` keys an app's prefs namespace and its crash-guard strikes.
    An empty id would put a bare `_scroll` into the shell's own settings dict,
    beside `theme` and `font_scale`."""
    assert _api.app_id_for(None) == "app"
    assert _api.app_id_for({}) == "app"
    assert _api.app_id_for({"title": ""}) == "app"
    assert _api.app_id_for({"title": "My Notes!"}) == "my_notes"


# -- the LEAVING hook is a host guarantee -------------------------------------

def test_the_host_calls_close_on_every_registered_app(tmp_path):
    """`go_home()` used to name four apps and four different persist verbs. An
    app that persists on an idle debounce and was not on that list lost the
    kid's work, silently -- the bar-contract bug class one level down. So this
    is BEHAVIOURAL, over a stub app the shell has never heard of."""
    ws = _ws(tmp_path)

    class Demo:
        id = "leaver"
        domain = "system"
        TITLE = "LEAVER"
        closed = 0

        def is_app(self, cart):
            return False

        def open(self):
            pass

        def relayout(self, w, h, fs):
            pass

        def draw(self, dt):
            pass

        def handle_input(self, i):
            return True

        def handle_pointer(self, px, py, click):
            return True

        def close(self):
            Demo.closed += 1

    demo = Demo()
    ws.register_app(demo)
    ws.open_app(demo, cart=ws.carts.all[0])
    assert ws.screen == "leaver"
    ws.go_home()
    assert ws.screen == "launcher"
    assert Demo.closed == 1, "the host did not call close() on the way home"


@pytest.mark.parametrize("kind", ("writer", "sheets", "storybook", "artwork"))
def test_every_persisting_app_implements_the_leaving_hook(kind, tmp_path):
    ws = _ws(tmp_path)
    app = ws._apps_by_id[kind]
    assert callable(getattr(app, "close", None)), \
        kind + " persists on a debounce but has no close() -- see go_home()"


# -- the RATCHET ---------------------------------------------------------------

# Scoped deliberately. The source plan's stop-condition grep was
# `runtime/*_app.py runtime/artwork.py`, whose glob also catches editor_app.py
# and host_app.py -- neither is a system app and neither is in Phase 6's scope,
# so that condition is unsatisfiable as written.
MIGRATED = ("calc_app", "appearance_app", "writer_app", "storybook_app",
            "sheets_app", "files_app", "artwork", "app_shell")


@pytest.mark.parametrize("mod", MIGRATED)
def test_no_migrated_module_reaches_the_workstation(mod):
    """The old road is closed: an app talks to the shell through its context.

    Both spellings are checked. `ws.<name>` is the obvious one; `self.ws` is the
    one that matters, because `getattr(self.ws, "clipboard", None)` is invisible
    to a `ws.` grep -- which is how the source plan concluded the clipboard had
    zero consumers while five shipped."""
    src = (RUNTIME / (mod + ".py")).read_text(encoding="utf-8")
    for line_no, line in enumerate(src.splitlines(), 1):
        assert "self.ws" not in line, "%s:%d still holds a ws reference" % (mod, line_no)
        code = line.split("#", 1)[0]
        assert "ws." not in code.replace("news.", "").replace("rows.", ""), \
            "%s:%d still reaches through ws" % (mod, line_no)


# `ctx.shell` is the un-narrowed Workstation and exists for ONE reason: the
# shared FileGridView widget still duck-types on ws.carts_store / ws.carts_root /
# ws._with_sd. This set may only SHRINK -- giving that widget the files role is
# what deletes the escape hatch, and Phase 7 must never grant it to a cart.
SHELL_CONSUMERS = {"PaintAppLayer", "WriterAppLayer", "SheetsAppLayer",
                   "FilesAppLayer"}


def test_the_shell_escape_hatch_has_a_pinned_consumer_list():
    have = set()
    for mod, cls in APP_TARGETS:
        if "shell" in _declared(mod, cls):
            have.add(cls)
    assert have <= SHELL_CONSUMERS, \
        "new ctx.shell consumer(s) %s -- use a role, or argue in app_api_v1.md" % (
            sorted(have - SHELL_CONSUMERS),)
    assert have == SHELL_CONSUMERS, (
        "ctx.shell consumers shrank to %s -- update SHELL_CONSUMERS (good news)"
        % sorted(have))


def test_the_only_shell_use_is_constructing_the_file_grid():
    """Pins WHY the hatch is open. Every `ctx.shell` reference in an app module
    must be handing the raw Workstation to FileGridView; anything else is a
    reach-in wearing the hatch's clothes."""
    for mod, _cls in APP_TARGETS:
        src = (RUNTIME / (mod + ".py")).read_text(encoding="utf-8")
        for line_no, line in enumerate(src.splitlines(), 1):
            if "ctx.shell" not in line and "self._shell" not in line:
                continue
            ok = ("FileGridView(" in line
                  or "self._shell = ctx.shell" in line
                  or line.strip().startswith("#"))
            assert ok, "%s:%d uses the shell hatch for something else: %s" % (
                mod, line_no, line.strip())
