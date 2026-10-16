"""The toolkit's style and strings are DATA (docs/kernel_toolkit_2026-10.md
§2): native/moy_ui/style.json holds what the twin's tables hold, the strings
C draws are native/moy_spine/strings/en.json's and none is left a literal, and every theme key a style spec
reads is in the token vocabulary the C resolution reads from.
"""

import json
import os
import re

import ui_twin as twin
from runtime.chrome import THEME_VARIANTS, THEME_NAMES, theme_colors

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLE = os.path.join(ROOT, "native", "moy_ui", "style.json")
STRINGS = os.path.join(ROOT, "native", "moy_spine", "strings", "en.json")
TOKENS_C = os.path.join(ROOT, "native", "moy_app", "moy_app.c")


def _style():
    with open(STYLE) as f:
        return json.load(f)


def _spec(s):
    """A JSON token spec in the twin's spelling: lists are tuples."""
    if isinstance(s, list):
        return tuple(_spec(x) for x in s)
    return s


def _kinds(j):
    return {k: {st: tuple(_spec(x) for x in v) for st, v in states.items()}
            for k, states in j.items()}


def _metrics(j):
    return {k: tuple(v.values()) for k, v in j.items()}


# Kinds the style gained after the crossing: data with no Python body behind
# them, so the twin has no table for them.
ADDED_KINDS = ("toggle",)
# Metrics likewise: the parts' (docs/look_2026-10.md step 12), whose slots are
# moy_ui_tables.h's MOY_UI_M_GROUP_* and MOY_UI_M_VALUE_ROW_*.
ADDED_METRICS = ("group", "value_row")


def test_the_style_file_is_the_twins_tables():
    d = _style()
    for k in ADDED_KINDS:
        del d["kinds"][k]
    for k in ADDED_METRICS:
        del d["metrics"][k]
    assert tuple(d["states"]) == twin.STATES
    assert {k: tuple(v) for k, v in d["aliases"].items()} == twin._STATE_ALIAS
    assert d["button_kinds"] == twin._BUTTON_KIND_KEY
    assert _kinds(d["kinds"]) == twin.DEFAULT_SPECS
    assert _metrics(d["metrics"]) == twin.DEFAULT_METRICS
    assert d["quirks"] == dict(twin.NON_DATA_QUIRKS)


def test_the_metric_slots_are_named_once_per_kind():
    d = _style()
    # the slot constants the toolkit exports are the slots' positions
    consts = {
        "button": "BTN_PAD BTN_ICON BTN_GLYPH BTN_EDGE BTN_ALIGN",
        "chip": "CHIP_CLIP CHIP_MINX CHIP_MINY CHIP_EDGE CHIP_ALIGN",
        "row": "ROW_PAD ROW_ICON ROW_GLYPH ROW_GAP ROW_EDGE ROW_STACK ROW_STACK_DESK "
               "ROW_STACK_GAP",
        "cell": "CELL_PAD CELL_CAP CELL_INDENT CELL_ICON CELL_GLYPH CELL_EDGE",
        "tab": "TAB_GAP TAB_PAD TAB_ICON",
        "status": "ST_RULE ST_PAD ST_GAP ST_DY",
        "panel": "PANEL_EDGE PANEL_INSET PANEL_STRIP PANEL_PAD",
        "focus": "FOCUS_RINGS FOCUS_GAP",
        "scrollbar": "SB_W SB_MIN",
        "text_field": "TF_PADX TF_PADY TF_CARET_W TF_CARET_H TF_FIELD "
                      "TF_RING TF_INK TF_HINT TF_CARET",
        "game_btn": "GB_PAD GB_RING GB_INK",
        "game_icon_btn": "GI_PAD GI_GLYPH GI_LABEL GI_RING GI_INK",
        "mini_btn": "MB_PADX MB_PADY MB_INK",
        "dialog": "DLG_EDGE DLG_FILL DLG_RING",
    }
    assert set(consts) == set(d["metrics"]) - set(ADDED_METRICS)
    for kind, names in consts.items():
        names = names.split()
        assert len(names) == len(d["metrics"][kind]), kind
        for i, const in enumerate(names):
            assert getattr(twin, const) == i, (kind, const)


def test_the_token_row_is_the_app_abis_vocabulary_in_order():
    """moy_ui's token row is moy_app's live token table, so a compiled run's
    look is fed from it as it stands."""
    with open(TOKENS_C) as f:
        src = f.read()
    m = re.search(r"TOKEN_NAMES\[MOY_APP_TOKENS\] = \{(.*?)\};", src, re.S)
    assert _style()["tokens"] == re.findall(r'"([a-z_]+)"', m.group(1))


def test_hits_capacity_is_configuration():
    """Twice the most rects any surface registers in the goldens' frames:
    16 (the calculator's keypad), measured with step 1 of the toolkit pass.
    tests/test_shell_goldens.py checks the frames still fit half of it."""
    assert _style()["hits_capacity"] == 32


def _vocabulary():
    with open(TOKENS_C) as f:
        src = f.read()
    m = re.search(r"TOKEN_NAMES\[MOY_APP_TOKENS\] = \{(.*?)\};", src, re.S)
    return set(re.findall(r'"([a-z_]+)"', m.group(1)))


def _roles_read(spec, out):
    if isinstance(spec, str):
        out.add(spec)
        out.update(twin._STATE_ALIAS.get(spec, ()))
    elif isinstance(spec, tuple):
        for s in spec:
            _roles_read(s, out)


def test_every_key_a_spec_reads_is_in_the_token_vocabulary():
    """The C resolution reads a theme as a token row: a key a style spec reads
    that a theme dict holds but the vocabulary does not name would be dropped
    on the way in and paint its fallback instead."""
    read = set()
    for table in (twin.DEFAULT_SPECS,):
        for states in table.values():
            for triple in states.values():
                for spec in triple:
                    _roles_read(spec, read)
    vocab = _vocabulary()
    held = set()
    for name in THEME_NAMES:
        for variant in THEME_VARIANTS:
            held.update(k for k in theme_colors(name, variant) if k in read)
    assert held, "no theme holds any key a spec reads: the ratchet is vacuous"
    assert held <= vocab, "keys outside the vocabulary: %s" % sorted(held - vocab)
    # what the vocabulary lacks is reached only through an alias chain
    for role in read - vocab:
        assert role in twin._STATE_ALIAS, role


# --- the strings C draws -------------------------------------------------------

C_DRAWS = ("native/moy_play/moy_chrome.c", "native/moy_kernel/moy_recovery.c",
           "native/moy_kernel/moy_kernel.c")
CONVERSION = re.compile(r"%(?:\d*l*[sdux]|08x|%)")


def _strings():
    with open(STRINGS) as f:
        return json.load(f)["strings"]


def _c_literals():
    """Every C string literal in the files that draw, adjacent ones joined."""
    out = set()
    for rel in C_DRAWS:
        with open(os.path.join(ROOT, rel)) as f:
            src = re.sub(r"//[^\n]*", "", f.read())
        for run in re.findall(r'(?:"(?:[^"\\\n]|\\.)*"\s*)+', src):
            out.add("".join(re.findall(r'"((?:[^"\\\n]|\\.)*)"', run)))
    return out


def test_no_string_c_draws_is_left_a_literal():
    """Each entry's words, every run between conversions of four bytes or
    more, appear in no C literal of the files that draw: they read the table."""
    lits = _c_literals()
    for sid, text in _strings().items():
        assert re.match(r"^[a-z_]+\.[a-z_]+$", sid), sid
        for run in re.split(r"%(?:08x|[sdu])", text):
            if len(run.strip()) >= 4:
                hit = [lit for lit in lits if run.strip() in lit]
                assert not hit, "%s: %r is still a literal in %s" % (sid, run, hit)


def test_each_entry_keeps_its_conversions_in_order():
    for sid, text in _strings().items():
        convs = CONVERSION.findall(text)
        assert all(c in ("%s", "%u", "%d", "%08x") for c in convs), (sid, convs)


# -- button kinds (docs/look_2026-10.md step 10) -----------------------------------

KINDS = ("normal", "primary", "tonal", "danger", "toggle")


def test_the_button_kinds_are_the_theme_models_in_its_order():
    """`button(kind=...)`'s names are the theme model's `buttons` keys, in the
    order the C's derivation table (moy_ui.c's k_own) and the loader share,
    and the button fields' leaves are in the order moy_ui.c names them."""
    from runtime import chrome, ui_draw
    d = _style()
    assert tuple(d["button_kinds"]) == tuple(d["theme"]["look"]["button_kinds"]) == KINDS
    assert ui_draw.BUTTON_KINDS == KINDS
    assert chrome.look_fields()[2] == (
        "fill", "ink", "border.w", "border.c", "shape", "r", "font", "bevel")
    assert {k: tuple(tuple(_spec(x) for x in form) for form in forms)
            for k, forms in d["floors"].items()} == twin.FLOORS


def _install(src):
    import moy_ui
    from runtime import chrome
    rc, _notes = moy_ui.theme_install(json.dumps(src).encode())
    assert rc == 0
    chrome.forget_themes()


def _uninstall():
    from runtime import chrome, ui_draw
    ui_draw.theme_uninstall()
    ui_draw.use_shape(None)
    chrome.forget_themes()


_BLOCK = [None]


def _live(name, variant="dark"):
    from runtime import chrome, ui_draw
    live = chrome.live_colors(name, variant)
    ui_draw._C.theme_block(live.block, live.used)
    ui_draw.use_buttons(live)
    _BLOCK[0] = live.block
    return live


def _field(th, kind, app=None, state=None):
    """The field and ink a button of `kind` draws at rest, read off its pixels:
    the corner inside the edge, and the most common other colour."""
    from runtime import ui_draw
    from runtime.host_canvas import make_system_canvas
    cv = make_system_canvas(80, 24)
    if _BLOCK[0] is not None:
        cv.theme_block(_BLOCK[0])
    cv.cls(1)
    ui_draw.button(cv, th, (0, 0, 80, 24), "MMMM", kind=kind, app=app, state=state)
    i = (3 * cv.w + 3) * 2
    return cv._buf[i] << 8 | cv._buf[i + 1]          # the wire order


def _word(v):
    """A colour's RGB565 word as the canvas holds it (a slot's from the live
    block)."""
    from runtime import ui_draw
    return ui_draw._C.theme_rgb(v)[0]


def test_each_kind_resolves_from_the_base_roles():
    """With no `buttons` in the theme each kind is DESIGN.md's derivation:
    primary the value fill and value_ink with no border, tonal the normal
    field with the value ink, danger with the danger ink, toggle a cell --
    where a derived ink misses the floor, the kind's floor form."""
    from runtime import ui_draw
    for name in ("machine", "night", "moy"):
        for variant in THEME_VARIANTS:
            th = _live(name, variant).tokens
            look = ui_draw._look(th)

            def style(kind, state=ui_draw.REST):
                kid = ui_draw._kind_id(ui_draw._button_kind(kind))
                return ui_draw._C.button_style(look, kid, ui_draw._STATE_ID[state], -1)[:3]

            own = {k: v[0] for a, k, v in _live(name, variant).buttons
                   if a is None and v[0] != -2 ** 31}
            field = own.get("normal", th["surface"])    # moy's buttons.normal.fill
            assert style("primary") == (th["value"], th["value_ink"], None)
            assert style("normal") == (field, th["ink"], th["shade_dark"])
            tonal, danger = style("tonal"), style("danger")
            assert tonal[2] == danger[2] == th["shade_dark"]
            if "tonal" in own:                          # moy's buttons.tonal.fill
                assert tonal[:2] == (own["tonal"], th["value"])
            elif ui_draw._C.theme_reads(field, th["value"]):
                assert tonal[:2] == (field, th["value"])
            else:
                assert tonal[:2] == (field, th["ink"])
            if ui_draw._C.theme_reads(field, th["danger"]):
                assert danger[:2] == (field, th["danger"])
            else:
                assert danger[0] == th["danger"]
            cell = tuple(ui_draw.state_colors(th, "cell", ui_draw.REST))
            assert tuple(style("toggle")) == ((field,) + cell[1:] if "normal" in own else cell)
            for kind in KINDS:          # the shared marks stay the style's
                assert style(kind, ui_draw.PRESSED)[:2] == ui_draw.state_colors(
                    th, ui_draw._button_kind(kind), ui_draw.PRESSED)[:2]
            assert _field(th, "primary") == _word(th["value"])
    _uninstall()


def test_a_theme_styles_each_kind_and_an_app_its_own():
    """`buttons[kind]` restyles a kind everywhere; every kind but primary
    takes normal's fill; `app_buttons[app][kind]` restyles it for that app
    only (THEMES.md, "buttons")."""
    from runtime import ui_draw
    _install({"format": 2, "name": "kt", "inherits": "machine", "look": {
        "buttons": {"normal": {"fill": "#ececf0", "ink": "#1d1d1f"},
                    "tonal": {"ink": "#c4231b"}},
        "app_buttons": {"calc": {"primary": {"fill": "#ff9f0a", "ink": "#000000"},
                                 "normal": {"fill": "#e4e4e9", "border": None}}}}})
    try:
        live = _live("kt")
        th = live.tokens
        rows = {(a, k): v for a, k, v in live.buttons}
        normal_fill = rows[(None, "normal")][0]
        calc_fill = rows[("calc", "primary")][0]
        calc_normal = rows[("calc", "normal")][0]
        assert normal_fill >= 64 and calc_fill >= 64 and calc_normal >= 64
        assert _field(th, "normal") == _word(normal_fill)
        assert _field(th, "tonal") == _word(normal_fill)            # from normal
        assert _field(th, "danger") == _word(normal_fill)
        assert _field(th, "primary") == _word(th["value"])          # its own
        assert _field(th, "primary", app="calc") == _word(calc_fill)
        assert _field(th, "primary", app="files") == _word(th["value"])
        assert _field(th, "tonal", app="calc") == _word(calc_normal)   # calc's normal
        assert _field(th, "normal", app="files") == _word(normal_fill)
        look = ui_draw._look(th)
        kid = ui_draw._kind_id(ui_draw._button_kind("normal"))
        row = ui_draw._app_row("calc")
        assert ui_draw._C.button_style(look, kid, ui_draw._STATE_ID[ui_draw.REST], row)[2] is None
        assert ui_draw._C.button_style(look, kid, ui_draw._STATE_ID[ui_draw.REST], -1)[2] == th["shade_dark"]
        assert ui_draw.button_unreadable(th, ("calc",)) == []
    finally:
        _uninstall()


def test_an_unreadable_kind_is_reported_and_never_refused():
    from runtime import ui_draw
    _install({"format": 2, "name": "ku", "inherits": "machine", "look": {
        "buttons": {"danger": {"fill": "#ffffff", "ink": "#fefefe"}},
        "app_buttons": {"calc": {"tonal": {"ink": "#ffffff", "fill": "#f0f0f0"}}}}})
    try:
        th = _live("ku").tokens
        assert ui_draw.button_unreadable(th, ("calc", "files")) == [
            (None, "danger"), ("calc", "tonal"), ("calc", "danger"),
            ("files", "danger")]
        assert _field(th, "danger") == _word(_live("ku").buttons[3][2][0])
    finally:
        _uninstall()


def test_one_primary_per_surface_is_warned_of(capsys):
    """A second primary on one canvas in one frame is said once; the same
    button drawn twice, another canvas, or the next frame is not."""
    from runtime import ui_draw
    from runtime.host_canvas import make_system_canvas
    th = theme_colors("machine")
    a, b = make_system_canvas(80, 60), make_system_canvas(80, 60)
    del ui_draw.primary_warnings[:]
    ui_draw._PRIMARY_WARNED.clear()
    ui_draw.begin_frame()
    ui_draw.button(a, th, (0, 0, 40, 20), "GO", kind="primary")
    ui_draw.button(a, th, (0, 0, 40, 20), "GO", kind="primary")
    ui_draw.button(b, th, (0, 0, 40, 20), "OK", kind="primary")
    ui_draw.button(a, th, (0, 30, 40, 20), "NO", kind="tonal")
    assert ui_draw.primary_warnings == []
    ui_draw.button(a, th, (0, 30, 40, 20), "YES", kind="primary")
    ui_draw.button(a, th, (0, 30, 40, 20), "YES", kind="primary")
    assert ui_draw.primary_warnings == [("GO", "YES")]
    ui_draw.begin_frame()
    ui_draw.button(a, th, (0, 30, 40, 20), "YES", kind="primary")
    assert ui_draw.primary_warnings == [("GO", "YES")]
    assert capsys.readouterr().out.count("second primary") == 1
    del ui_draw.primary_warnings[:]
    ui_draw._PRIMARY_WARNED.clear()
    ui_draw.begin_frame()


def test_an_unknown_kind_draws_as_normal():
    th = theme_colors("machine")
    _BLOCK[0] = None
    assert _field(th, "nosuch") == _field(th, "normal") == _word(th["surface"])


_KIND_DRIVER = r'''
import moy_ui
from array import array
NONE = -2147483648
OFF = NONE + 1
toks = moy_ui.tokens()
kinds = ("normal", "primary", "tonal", "danger", "toggle")
moy_ui.buttons_clear()
moy_ui.buttons_set(None, 0, [12, 1, 200, 8, NONE, NONE, NONE, NONE])
moy_ui.buttons_set(None, 2, [NONE, 9, NONE, NONE, 1, 40, 1, 1])
moy_ui.buttons_set("calc", 1, [10, 0, OFF, OFF, 1, NONE, NONE, NONE])
moy_ui.buttons_set("calc", 0, [NONE, NONE, 0, NONE, NONE, NONE, NONE, NONE])
for name in moy_ui.themes():
    for light in (False, True):
        row = moy_ui.theme(name, light)[0]
        r = array("i", [NONE] * len(toks))
        for i in range(len(toks)):
            r[i] = row[i] if row[i] < 64 else moy_ui.theme_rgb(row[i])[2]
        moy_ui.look(0, r)
        for k in kinds:
            kid = moy_ui.kind(moy_ui.kinds()[moy_ui.button_kind(k)])
            for st in range(len(moy_ui.states())):
                for app in (-1, moy_ui.buttons_app("calc"), -2):
                    print(repr(moy_ui.button_style(0, kid, st, app)))
print(repr([moy_ui.theme_reads(a, b) for a in (0, 7, 8, 13) for b in (0, 7, 11)]))
'''


def test_the_boards_module_resolves_the_kinds_as_the_host_does(tmp_path):
    """The button kinds' resolution, the theme's leaves and an app's over
    them, and the floor forms, through the module every board imports."""
    import subprocess
    import moy_ui
    from unix_mp import require_unix_mp
    exe = require_unix_mp("moy_ui", why="Without it the button kinds' MicroPython "
                          "face, the one every board imports, is never run on the host.")
    script = tmp_path / "kinds.py"
    script.write_text(_KIND_DRIVER)
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    host = {}
    exec(compile(_KIND_DRIVER.replace("print(repr(", "_out.append(repr("), "k", "exec"),
         {"_out": host.setdefault("out", []), "__name__": "k"})
    moy_ui.buttons_clear()
    from runtime import ui_draw
    ui_draw.look_moved()                    # the driver wrote look slot 0
    assert out.stdout.splitlines() == host["out"], out.stderr[-400:]


def _px(cv, x, y):
    i = (y * cv.w + x) * 2
    return cv._buf[i] << 8 | cv._buf[i + 1]


def _drawn(th, kind, app=None, rect=(0, 0, 80, 24)):
    from runtime import ui_draw
    from runtime.host_canvas import make_system_canvas
    cv = make_system_canvas(90, 30)
    if _BLOCK[0] is not None:
        cv.theme_block(_BLOCK[0])
    cv.cls(1)
    ui_draw.button(cv, th, rect, "MM", kind=kind, app=app)
    return cv


def test_a_kind_draws_its_shape():
    """A theme with a look rounds its keys at key_r (a circle, a square or a
    radius where it says so; a bevel under `edges: bevel`); a theme with no
    look keeps them square."""
    from runtime import ui_draw
    bg = _word(1)
    th = theme_colors("machine")
    _BLOCK[0] = None
    sq = _drawn(th, "primary")
    assert _px(sq, 0, 0) == _px(sq, 40, 0) == _word(th["value"])
    _install({"format": 2, "name": "ks", "inherits": "machine", "look": {
        "key_r": 8, "edges": "bevel",
        "app_buttons": {"calc": {"primary": {"shape": "circle"},
                                 "normal": {"shape": "square", "bevel": False},
                                 "danger": {"shape": "round", "r": 6}}}}})
    try:
        ui_draw.use_depth("ks", False)
        th = _live("ks").tokens
        cv = _drawn(th, "primary")
        assert _px(cv, 0, 0) == bg                         # a rounded corner
        assert _px(cv, 40, 12) == _word(th["value"])
        assert _px(cv, 40, 0) == _word(th["shade_hi"])     # the bevel's top
        assert _px(cv, 40, 23) == _word(th["shade_lo"])    # ...and its foot
        sq = _drawn(th, "normal", app="calc")
        assert _px(sq, 0, 0) == _word(th["shade_dark"])    # square, its edge
        circle = _drawn(th, "primary", app="calc", rect=(0, 0, 30, 24))
        assert _px(circle, 1, 12) == bg                    # 24 across, centred
        assert _px(circle, 15, 12) == _word(th["value"])
        small = _drawn(th, "danger", app="calc")
        assert _px(small, 0, 0) == bg and _px(small, 6, 0) != bg
    finally:
        ui_draw.use_depth(None, False)
        _uninstall()


# -- the marks (docs/media/look_2026-10/DESIGN.md, the marks; look step 11) ----

_ACCENTS = {"accent", "focus", "ink_accent", "hover_cue"}


def _names(spec):
    if isinstance(spec, list):
        return {x for x in spec if isinstance(x, str)}
    return {spec} if isinstance(spec, str) else set()


def test_accent_reaches_a_part_only_through_the_marks():
    """`accent` is the focus colour (and `value`'s default): a kind's style
    names it only in the `focus` kind, in `on` (which a theme with marks
    draws as its current mark, never as the ON look) and in the pointer's
    states, hover and pressed."""
    stray = sorted((kind, state)
                   for kind, states in _style()["kinds"].items()
                   for state, trip in states.items()
                   if kind != "focus" and state not in ("on", "hover", "pressed")
                   and any(_ACCENTS & _names(s) for s in trip))
    assert stray == []


def _marked(name, light, draw, size=(120, 40)):
    from runtime import ui_draw
    from runtime.host_canvas import make_system_canvas
    ui_draw.use_depth(name, light)
    live = _live(name, "light" if light else "dark")
    th = live.tokens
    cv = make_system_canvas(*size)
    cv.theme_block(live.block)
    cv.cls(th["surface"])
    draw(ui_draw, cv, th)
    return cv, th


def test_on_is_the_current_mark_and_focus_the_focus_mark():
    """Under Moy Day a row's `on` is the current plate (value at 0.14 over the
    surface), not the ON field, and its `focus` the 4 px edge bar on its
    leading edge; a theme without marks draws `focus` as it drew `on`."""
    from runtime import ui_draw
    try:
        cv, th = _marked("moy", True, lambda d, cv, th: ui_draw.row(
            cv, th, (10, 10, 100, 20), "", kind="row_list", on=True, focus=True))
        plate = _px(cv, 60, 15)
        assert plate not in (_word(th["surface"]), _word(th["value"]),
                             _word(ui_draw.state_colors(th, "row_list", "on")[0]))
        assert _px(cv, 11, 15) == _word(th["accent"])          # the edge bar
        assert _px(cv, 15, 15) == plate
        ui_draw.marks(None, True)
        cv, th = _marked("moy", True, lambda d, cv, th: ui_draw.row(
            cv, th, (10, 10, 100, 20), "", kind="row_list", focus=True))
        assert _px(cv, 60, 15) == _word(th["surface"]) and ui_draw.marks()[2] == 1
    finally:
        ui_draw.use_depth(None, False)
    for name in ("night", "win95"):
        a = _marked(name, False, lambda d, cv, th: ui_draw.row(
            cv, th, (10, 10, 100, 20), "x", kind="row_list", focus=True))[0]
        b = _marked(name, False, lambda d, cv, th: ui_draw.row(
            cv, th, (10, 10, 100, 20), "x", kind="row_list", on=True))[0]
        assert bytes(a._buf) == bytes(b._buf), name


def test_the_focus_style_follows_the_size_class():
    """Moy lifts a card (grown and raised, its rim white in Day), edge-bars a
    part and underlines a small part; Contrast rings a card and inverts a
    part, its ink flipped to black on yellow."""
    from runtime import ui_draw
    try:
        def card(d, cv, th):
            ink, box = ui_draw.focus(cv, th, (40, 30, 60, 60), size="card")
            assert ink is None and box == (35, 22, 70, 70)
            cv.rect(*box, 0)
            ui_draw.focus(cv, th, (40, 30, 60, 60), size="card", after=True)
        cv, th = _marked("moy", True, card, (140, 120))
        assert _px(cv, 34, 50) == 0xffff                      # the white rim
        cv, th = _marked("moy", True, lambda d, cv, th: ui_draw.focus(
            cv, th, (20, 20, 20, 20), size="small"))
        assert _px(cv, 30, 37) == _word(th["accent"]) and _px(cv, 30, 30) != _px(cv, 30, 37)
        cv, th = _marked("contrast", False, lambda d, cv, th: ui_draw.focus(
            cv, th, (20, 10, 60, 60), size="card"), (100, 80))
        assert _px(cv, 15, 40) == _word(th["accent"])         # ring 3 wide, 2 out
        assert _px(cv, 18, 40) == _word(th["surface"])
        got = []
        cv, th = _marked("contrast", False, lambda d, cv, th: got.append(ui_draw.focus(
            cv, th, (10, 10, 100, 20))))
        assert _px(cv, 60, 15) == _word(th["accent"]) and got[0][0] == 0
    finally:
        ui_draw.use_depth(None, False)


def test_a_window_behind_draws_no_focus_mark():
    from runtime import ui_draw
    try:
        ui_draw.marks(False, True)
        cv, th = _marked("moy", True, lambda d, cv, th: ui_draw.row(
            cv, th, (10, 10, 100, 20), "", kind="row_list", focus=True, on=True))
        assert _px(cv, 11, 15) == _px(cv, 60, 15) != _word(th["accent"])
        assert ui_draw.marks()[2] == 0
    finally:
        ui_draw.marks(True)
        ui_draw.use_depth(None, False)


def _contrast(a, b):
    from runtime import ui_draw

    def lum(v):
        w = ui_draw._C.theme_rgb(v)[0]
        out = []
        for c in ((w >> 11) * 255 // 31, (w >> 5 & 63) * 255 // 63, (w & 31) * 255 // 31):
            c /= 255
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_readability_check_holds_accent_on_the_current_plate():
    """Where a theme's current mark is a plate, its focus colour clears 3:1 on
    the plate: Moy's (Day 5.9, Night 5.2 in DESIGN.md) and Contrast's read,
    and a theme whose accent sinks into its plate is reported (the bit past
    the pairs)."""
    import moy_ui
    from runtime import ui_draw
    pairs = len(_style()["theme"]["pairs"])
    for seed in ("moy", "contrast"):
        for light in (False, True):
            row, bad, rc = moy_ui.theme(seed, light)
            assert rc == 0 and not bad >> pairs & 1, (seed, light)
    roles = dict(zip(moy_ui.theme_roles(), moy_ui.theme("moy", True)[0]))
    assert _contrast(roles["accent"], roles["surface"]) > 5
    _install({"format": 2, "name": "pl", "inherits": "moy",
              "colors": {"light": {"accent": "#e3e6ed", "focus": "#e3e6ed"}},
              "look": {"focus": {"color": "accent"},
                       "current": {"style": "plate", "color": "ink", "alpha": 0.1}}})
    try:
        assert moy_ui.theme("pl", True)[1] >> pairs & 1
    finally:
        _uninstall()
        ui_draw.look_moved()
