"""The public UI API (docs/ui_api_2026-10.md): its verbs in three runtimes,
the engine's focus and navigation, and the shell-strict ratchet.

  * one fixture page -- tests/fixtures/ui_page/page_py.moy, page_lua.moy and
    tests/fixtures/wasm/app_ui.moy, the same calls in Python, Lua and a
    compiled app -- draws the same pixels and answers the same values under
    the same input script, through the Player each runs in;
  * the engine (native/moy_ui/moy_ui_api.h) under scripted input: the focus
    order, spatial moves, activation by key and by tap, `keys_taken`, state
    kept by id and dropped with it, `nav` and `mark` off, a settled frame;
  * the shell is strict (§7): the shell's app modules and the system carts
    reach the engine layer (`runtime/ui_draw.py`) only where pinned below,
    and pass no override key but `id`, `current`, `disabled`, `steps` and `hint`.
"""

import os
import re
import shutil
from array import array

import pytest

from runtime import host_app, ui, ui_draw
from runtime.host_canvas import make_system_canvas
from runtime.chrome import THEME_NAMES
from ws_helpers import MOY_VARIANTS, build_ws, make_drv, open_cart, quiesce, use_theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(ROOT, "tests", "fixtures", "ui_page")
WASM = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_ui.moy")
_DT = 1.0 / 30.0
P_FRAMES, P_SOUND, P_VOL, P_CUR, P_SAVES, P_KEYS, P_PART, P_FOCUS, P_FAIL = range(9)

# -- one page, three runtimes ----------------------------------------------------------


def _wasm_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return True
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s"
                    % (wasm_binding.why_unavailable() or "unknown"))
    return False


@pytest.fixture(scope="module", params=MOY_VARIANTS)
def page_runs(request, tmp_path_factory):
    """Each runtime's page after the same script: (pixels, pmem) by name."""
    root = str(tmp_path_factory.mktemp("ui_page") / "carts")
    host_app.moy_carts.ensure_dirs(root)
    shutil.copytree(os.path.join(PAGES, "page_py.moy"), os.path.join(root, "page_py.moy"))
    shutil.copytree(os.path.join(PAGES, "page_lua.moy"), os.path.join(root, "page_lua.moy"))
    titles = {"python": "UI Page Py", "lua": "UI Page Lua"}
    if _wasm_or_skip():
        from tools import wasm_cart
        wasm_cart.build(WASM, os.path.join(root, "app_ui.moy"))
        titles["wasm"] = "App UI Wasm"
    out = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.look.set_theme("moy", persist=False, variant=request.param)
        ws.pointer.visible = False
        open_cart(ws, title)
        make_drv(ws).frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        out[name] = _script(ws)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        if name == "wasm":
            from runtime import moy_play
            _CENSUS[:] = [tuple(moy_play.info()[5]),
                          moy_play.census(os.path.join(root, "app_ui.moy"))]
        ws.exit()
    return out


_CENSUS = []


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(_DT)


def _pixels(ws):
    """The cart's canvas below the host's strip (the bar contract's content
    rect), where the three draw alike."""
    ns = ws.player.ns
    cv = ns["screen"]()
    x, y, w, h = ns["content_rect"]()
    buf = cv._buf
    out = bytearray()
    for j in range(y, y + h):
        out += buf[2 * (j * cv.w + x):2 * (j * cv.w + x + w)]
    return bytes(out)


def _pmem(ws):
    lua = getattr(ws.player, "_lua", None)
    if lua is not None and hasattr(lua._run, "pmem"):
        return list(lua._run.pmem()[1])[:9]
    if lua is not None:
        import sys
        img = array("i", bytearray(4 * 256))
        sys.modules[type(lua).__module__]._moycore.pmem_image(img)
        return list(img)[:9]
    pm = ws.player.ns["pmem"]
    return [pm(i) for i in range(9)]


def _script(ws):
    """Settle, walk down the page by key, change the volume, activate the
    list's next row, then tap the Save button."""
    drv = make_drv(ws)
    shots = []
    for _ in range(3):
        _frame(drv)
    shots.append((_pixels(ws), _pmem(ws)))
    for key in ("down", "right", "right", "down", "down", "a", "down", "down"):
        drv.press(key)
        _frame(drv)
        _frame(drv)
        shots.append((_pixels(ws), _pmem(ws)))
    drv.touch(250, 224)
    _frame(drv)
    drv.touch_up()
    _frame(drv)
    _frame(drv)
    shots.append((_pixels(ws), _pmem(ws)))
    return shots


def test_the_page_is_one_page_in_every_runtime(page_runs):
    names = sorted(page_runs)
    assert "python" in names and "lua" in names
    ref = page_runs["python"]
    for name in names:
        got = page_runs[name]
        assert len(got) == len(ref)
        for i, ((px, pm), (rpx, rpm)) in enumerate(zip(got, ref)):
            assert pm[:P_FAIL] == rpm[:P_FAIL], "%s step %d answered %r, python %r" % (
                name, i, pm, rpm)
            assert px == rpx, "%s step %d drew other pixels than python" % (name, i)
    if "wasm" in page_runs:
        assert all(s[1][P_FAIL] == 0 for s in page_runs["wasm"]), "a ui row failed"


def test_the_compiled_page_runs_with_no_vm(page_runs):
    """Every `ui` row is served in C: the run crosses into Python for
    nothing but the console's frame, and the census keeps it VM-free."""
    if "wasm" not in page_runs:
        pytest.skip("no host wasm binding")
    upcalls, census = _CENSUS
    assert upcalls[1:] == (0, 0, 0, 0, 0), "the run crossed into Python"
    assert census == ("wasm", True, "free")


def test_the_page_answers_its_script(page_runs):
    shots = page_runs["python"]
    first = shots[0][1]
    assert first[P_SOUND] == 1 and first[P_VOL] == 5 and first[P_CUR] == 1
    assert first[P_FOCUS] == len("Sound"), "the first stop drawn holds focus"
    assert first[P_KEYS] == 0
    # down: Volume; right, right: its value; down, down: into the list, which
    # starts on its current row, then its next; a: that row is current; down:
    # past the last row to the swatch, then the Save button placed below it
    after = [s[1] for s in shots]
    assert after[1][P_FOCUS] == len("Volume")
    assert after[3][P_VOL] == 7
    assert after[4][P_FOCUS] == len("Theme")
    assert after[6][P_CUR] == 2
    assert after[7][P_FOCUS] == len("swatch") and after[7][P_PART] & ui.FOCUS
    assert after[8][P_FOCUS] == len("Save") and after[8][P_SAVES] == 0
    assert after[9][P_SAVES] == 1, "the tap on Save activated it"


# -- the engine under scripted input -----------------------------------------------------


class _Run:
    """A page of verbs on a system canvas, frame by frame, with the keys and
    pointer a test scripts."""

    BITS = {"left": 1, "right": 2, "up": 4, "down": 8, "a": 16, "b": 32, "run": 64}

    def __init__(self, draw, w=320, h=240):
        self.ws = None
        self.cv = make_system_canvas(w, h)
        self.th = _theme()
        self.ctx = ui_draw.ui_context()
        self.inp = array("i", [0] * ui_draw.UI_IN_LEN)
        self.draw = draw
        self.out = None
        self.settled = None

    def frame(self, press=(), ptr=None, key=0):
        i = self.inp
        i[0] = i[1] = 0
        for k in press:
            i[1] |= self.BITS[k]
        i[0] = i[1]
        if ptr is None:
            i[2] = i[3] = i[4] = 0
        else:
            i[2], i[3], i[4] = ptr
        i[5] = key
        self.settled = ui_draw.ui_frame(self.ctx, self.cv, self.th, None, i)
        self.out = self.draw()
        return self.out


_TH = []


def _theme():
    """The lookless `night` fixture's colours, installed afresh for each page:
    the installed look is process-wide, as on a board, and these pages' stops
    sit where night lays them out."""
    if not _TH:
        import tempfile
        from pathlib import Path
        _TH.append(build_ws(Path(tempfile.mkdtemp())))
    use_theme(_TH[0], "night", "dark")
    return _TH[0].theme_colors


def _page():
    st = {}

    def draw():
        ui.page("Nav")
        st["a"] = ui.button("A")
        ui.at(200, 14, 60, 18)
        st["side"] = ui.button("Side")
        st["b"] = ui.button("B")
        st["c"] = ui.button("C")
        return st
    return draw


def test_the_first_stop_drawn_holds_focus_and_tab_walks_call_order():
    r = _Run(_page())
    r.frame()
    assert ui.focus() == "A"
    seen = []
    for _ in range(4):
        r.frame(key=9)
        r.frame()
        seen.append(ui.focus())
    assert seen == ["Side", "B", "C", "A"], seen


def test_arrows_move_to_the_nearest_stop_in_their_direction():
    r = _Run(_page())
    r.frame()
    r.frame(("down",))
    assert ui.focus() == "B", "down from A is the row below, not the placed button"
    r.frame(("up",))
    r.frame(("right",))
    assert ui.focus() == "Side"
    r.frame(("left",))
    assert ui.focus() == "A"
    r.frame(("up",))
    assert ui.focus() == "A", "nothing above: focus stays"
    assert ui.keys_taken(), "the ui took the arrow even where it moved nothing"


def test_activation_by_key_and_by_tap():
    r = _Run(_page())
    r.frame()
    got = r.frame(("a",))
    assert got["a"] and not got["b"]
    assert not r.frame()["a"], "a button answers true the frame it is activated only"
    # a tap released inside B activates it and focuses it
    r.frame(ptr=(20, 45, 1 | 2 | 4))
    got = r.frame(ptr=(20, 45, 1))
    assert got["b"] and ui.focus() == "B"
    # a press dragged away from the stop is no tap
    r.frame(ptr=(20, 45, 1 | 2 | 4))
    r.frame(ptr=(20, 30, 1 | 2))
    got = r.frame(ptr=(20, 30, 1))
    assert not got["b"] and not got["a"]


def test_keys_taken_and_nav_off():
    st = {}

    def draw():
        ui.page("Free", {"nav": False})
        st["x"] = ui.button("X")
        st["y"] = ui.button("Y")
        return st
    r = _Run(draw)
    r.frame()
    r.frame(("down", "a"))
    assert not ui.keys_taken(), "a nav-off page leaves the keys to the cart"
    assert ui.focus() == "X" and not st["x"]
    ui.focus("Y")
    r.frame()
    assert ui.focus() == "Y", "the cart moves focus itself"


def test_a_part_with_nav_off_keeps_focus_and_its_keys():
    st = {}

    def draw():
        ui.page("Grid")
        st["before"] = ui.button("Before")
        st["grid"] = ui.part("grid", 0, 40, {"nav": False})
        st["after"] = ui.button("After")
        return st
    r = _Run(draw)
    r.frame()
    r.frame(("down",))
    assert ui.focus() == "grid", "focus arrives by navigation"
    r.frame(("down",))
    assert ui.focus() == "grid" and not ui.keys_taken(), "and stays: the keys are the cart's"
    assert st["grid"][4] & ui.FOCUS


def test_mark_off_draws_no_focus_mark_but_keeps_the_state():
    def draw_with(mark):
        def draw():
            ui.page("M")
            return ui.part("p", 0, 20, None if mark else {"mark": False})
        return draw
    on, off = _Run(draw_with(True)), _Run(draw_with(False))
    for r in (on, off):
        r.frame()
        r.frame()
    assert on.out[4] & ui.FOCUS and off.out[4] & ui.FOCUS
    ui_draw.marks(None, True)
    on.frame()
    drew_on = ui_draw.marks(None, True)[2]
    off.frame()
    drew_off = ui_draw.marks(None, True)[2]
    if ui_draw.marks_on():
        assert drew_on == 1 and drew_off == 0
    assert bytes(on.cv._buf) != bytes(off.cv._buf) or not ui_draw.marks_on()


def test_a_lists_row_and_a_sliders_value_are_kept_by_id():
    st = {"cur": -1, "v": 3}

    def draw():
        ui.page("Keep")
        st["v"] = ui.slider("Level", st["v"], 0, 5)
        if st.get("show", True):
            st["cur"] = ui.list("Pick", ["one", "two", "three"], st["cur"])
        return st
    r = _Run(draw)
    r.frame()
    r.frame(("right",))
    r.frame(("right",))
    r.frame(("right",))
    assert st["v"] == 5, "a focused slider keeps left and right, clamped"
    r.frame(("down",))
    assert ui.focus() == "Pick"
    r.frame(("down",))
    r.frame(("a",))
    assert st["cur"] == 1, "down moved the list's row; a made it current"
    r.frame(("down",))
    r.frame(("down",))
    assert ui.focus() == "Pick", "the last row keeps down... until"
    r.frame(("up",))
    r.frame(("up",))
    r.frame(("up",))
    assert ui.focus() == "Level", "up past the first row leaves the list"
    st["show"] = False
    r.frame()
    st["show"] = True
    r.frame(("down",))
    r.frame(("a",))
    assert st["cur"] == 1, "the list's row was dropped with it: back on its current row"


def test_focus_on_an_id_no_longer_drawn_passes_to_the_nearest_stop():
    st = {"show": True}

    def draw():
        ui.page("Pass")
        ui.button("One")
        if st["show"]:
            ui.button("Two")
        ui.button("Three")
        return st
    r = _Run(draw)
    r.frame()
    r.frame(("down",))
    assert ui.focus() == "Two"
    st["show"] = False
    r.frame()
    r.frame()
    assert ui.focus() in ("One", "Three")


def _rows_page(n, end):
    def draw():
        content = ui.page("Rows")
        for k in range(n):
            ui.label("row %d" % k)
        if end:
            ui.end()
        return content
    return draw


def test_the_page_end_fades_its_foot_only_where_rows_run_on_below():
    """`ui.end()` fades the page's last rows toward its ground where its rows
    run on past what shows (design review D-09), and draws nothing where
    they all show."""
    def rows(n, end):
        r = _Run(_rows_page(n, end))
        _TH[0].look.set_theme("moy", persist=False, variant="light")   # a look with a ground
        r.th = _TH[0].theme_colors
        r.frame()
        page = r.frame()
        return bytes(r.cv._buf), page, r.cv.w
    long_plain, page, w = rows(40, False)
    long_faded, _p, _w = rows(40, True)
    x, y, pw, ph = page
    foot = (y + ph - 8) * w * 2                  # the fade's band, over a row's text
    top = y * w * 2
    assert long_faded[foot:foot + 8 * w * 2] != long_plain[foot:foot + 8 * w * 2]
    assert long_faded[top:top + w * 2] == long_plain[top:top + w * 2]
    short_plain, _p, _w = rows(2, False)
    short_faded, _p, _w = rows(2, True)
    assert short_faded == short_plain


def test_a_settled_page_draws_the_same_pixels():
    r = _Run(_page())
    r.frame()
    r.frame()
    before = bytes(r.cv._buf)
    r.frame()
    assert r.settled, "no input: the frame is settled"
    assert bytes(r.cv._buf) == before
    r.frame(("down",))
    assert not r.settled


# -- the shell is strict (§7) ------------------------------------------------------------

# The shell's app modules and system carts not yet moved onto the verbs: each
# screen step that restyles an app takes it off (docs/look_2026-10.md 19-24;
# steps 23 and 24 restyled the editors in place, docs/ui_api_2026-10.md §1a),
# and step 27 holds the list empty.
NOT_MOVED = frozenset((
    "runtime/block_editor_ui.py",
    "runtime/code_layer.py",
    "runtime/editor_app.py",
    "runtime/file_widgets.py",
    "runtime/map_editor_ui.py",
    "runtime/music_editor_ui.py",
    "runtime/paint_layer.py",
    "runtime/scene_editor_ui.py",
))

# The shell's apps (§7): their modules and the system carts' code.
APPS = (
    "runtime/appearance_app.py", "runtime/artwork.py", "runtime/paint_app.py",
    "runtime/myart_app.py", "runtime/drawing_pages.py", "runtime/block_editor_ui.py",
    "runtime/calc_app.py", "runtime/code_layer.py", "runtime/editor_app.py",
    "runtime/editor_blocks.py", "runtime/editor_desk.py", "runtime/editor_map.py", "runtime/editor_music.py",
    "runtime/editor_parts.py", "runtime/editor_scene.py", "runtime/editor_settings.py",
    "runtime/editor_sound.py",
    "runtime/file_widgets.py", "runtime/files_app.py", "runtime/getcarts_app.py",
    "runtime/map_editor_ui.py", "runtime/music_editor_ui.py", "runtime/paint_layer.py",
    "runtime/scene_editor_ui.py", "runtime/settings_layer.py", "runtime/storybook_app.py",
    "runtime/story_edit.py",
)


def _app_sources():
    out = list(APPS)
    for d in sorted(os.listdir(os.path.join(ROOT, "system_carts"))):
        for name in ("main.py", "main.lua"):
            if os.path.isfile(os.path.join(ROOT, "system_carts", d, name)):
                out.append("system_carts/%s/%s" % (d, name))
    return out


def _engine(src):
    return re.search(r"\bui_draw\b", src) is not None


def test_only_the_pinned_apps_reach_the_engine_layer():
    reach = set()
    for rel in _app_sources():
        with open(os.path.join(ROOT, rel)) as f:
            if _engine(f.read()):
                reach.add(rel)
    assert reach - NOT_MOVED == set(), "reaches the engine layer: %s" % sorted(reach - NOT_MOVED)
    assert NOT_MOVED - reach == set(), (
        "moved onto the verbs: take it off NOT_MOVED: %s" % sorted(NOT_MOVED - reach))


_OVERRIDE = re.compile(r"[\"'](fill|ink|border|nav|mark)[\"']\s*[:=]")


def test_the_shells_apps_pass_no_override_but_id_current_and_disabled():
    for rel in _app_sources():
        if rel in NOT_MOVED:
            continue
        with open(os.path.join(ROOT, rel)) as f:
            src = f.read()
        assert not _OVERRIDE.search(src), "%s overrides the theme" % rel


def test_apps_draw_focus_through_the_marks():
    """No app draws its own focus ring: the engine draws the theme's mark (a
    verb's stop, `ui_draw.focus`, or a part's `focus=`), and `focus_ring` is
    the ring a theme without marks falls back to inside it."""
    hits = []
    for name in sorted(os.listdir(os.path.join(ROOT, "runtime"))):
        if name.endswith(".py") and name != "ui_draw.py":
            with open(os.path.join(ROOT, "runtime", name)) as f:
                if "focus_ring(" in f.read():
                    hits.append(name)
    assert hits == []


def _first_page(root, title, theme, variant=None):
    """The page's first settled frame in a fresh workstation under `theme`."""
    ws = host_app.build_workstation(root)
    ws.pointer.visible = False
    if theme in THEME_NAMES:
        ws.look.set_theme(theme, persist=False, variant=variant)
    else:
        use_theme(ws, theme, variant)             # a fixture package
    open_cart(ws, title)
    drv = make_drv(ws)
    drv.frame(_DT)
    assert ws.player.cart_error is None, ws.player.cart_error
    for _ in range(3):
        _frame(drv)
    px = _pixels(ws)
    ws.exit()
    return px


def test_a_later_workstation_never_draws_the_look_of_an_earlier_one(tmp_path):
    """A compiled or Lua run keeps its look keyed on the theme generation,
    which each workstation counts from its own start: the second workstation
    of a process, on another theme at the same generation, drew the first's
    colours."""
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("page_py.moy", "page_lua.moy"):
        shutil.copytree(os.path.join(PAGES, d), os.path.join(root, d))
    for theme in ("platinum", "slate", "night"):
        py = _first_page(root, "UI Page Py", theme)
        lua = _first_page(root, "UI Page Lua", theme)
        assert lua == py, "the Lua page under %s drew other pixels than python" % theme


def test_a_python_pages_ground_is_a_colour_the_cart_canvas_has(tmp_path):
    """Under Moy Day the page's ground is the shell's block colour 64, which a
    cart canvas does not hold: it drew as index 0, a black page. Inside the
    cart boundary a block colour is its nearest MOY64, as the Lua page's is."""
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("page_py.moy", "page_lua.moy"):
        shutil.copytree(os.path.join(PAGES, d), os.path.join(root, d))

    def ground(px):
        words = [px[i:i + 2] for i in range(0, len(px), 2)]
        return max(set(words), key=words.count)

    for variant in ("light", "dark"):
        py = ground(_first_page(root, "UI Page Py", "moy", variant))
        lua = ground(_first_page(root, "UI Page Lua", "moy", variant))
        assert py == lua, variant
        if variant == "light":
            assert py != b"\x00\x00", "the page's ground is black"
