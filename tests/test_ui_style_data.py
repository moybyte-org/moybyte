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


def test_the_style_file_is_the_twins_tables():
    d = _style()
    for k in ADDED_KINDS:
        del d["kinds"][k]
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
        "row": "ROW_PAD ROW_ICON ROW_GLYPH ROW_GAP ROW_EDGE",
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
    assert set(consts) == set(d["metrics"])
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
    from runtime import chrome, ui
    d = _style()
    assert tuple(d["button_kinds"]) == tuple(d["theme"]["look"]["button_kinds"]) == KINDS
    assert ui.BUTTON_KINDS == KINDS
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
    from runtime import chrome, ui
    ui.theme_uninstall()
    ui.use_shape(None)
    chrome.forget_themes()


_BLOCK = [None]


def _live(name, variant="dark"):
    from runtime import chrome, ui
    live = chrome.live_colors(name, variant)
    ui._C.theme_block(live.block, live.used)
    ui.use_buttons(live)
    _BLOCK[0] = live.block
    return live


def _field(th, kind, app=None, state=None):
    """The field and ink a button of `kind` draws at rest, read off its pixels:
    the corner inside the edge, and the most common other colour."""
    from runtime import ui
    from runtime.host_canvas import make_system_canvas
    cv = make_system_canvas(80, 24)
    if _BLOCK[0] is not None:
        cv.theme_block(_BLOCK[0])
    cv.cls(1)
    ui.button(cv, th, (0, 0, 80, 24), "MMMM", kind=kind, app=app, state=state)
    i = (3 * cv.w + 3) * 2
    return cv._buf[i] << 8 | cv._buf[i + 1]          # the wire order


def _word(v):
    """A colour's RGB565 word as the canvas holds it (a slot's from the live
    block)."""
    from runtime import ui
    return ui._C.theme_rgb(v)[0]


def test_each_kind_resolves_from_the_base_roles():
    """With no `buttons` in the theme each kind is DESIGN.md's derivation:
    primary the value fill and value_ink with no border, tonal the normal
    field with the value ink, danger with the danger ink, toggle a cell --
    where a derived ink misses the floor, the kind's floor form."""
    from runtime import ui
    for name in ("machine", "night", "moy"):
        for variant in THEME_VARIANTS:
            th = _live(name, variant).tokens
            look = ui._look(th)

            def style(kind, state=ui.REST):
                kid = ui._kind_id(ui._button_kind(kind))
                return ui._C.button_style(look, kid, ui._STATE_ID[state], -1)[:3]

            assert style("primary") == (th["value"], th["value_ink"], None)
            assert style("normal") == (th["surface"], th["ink"], th["shade_dark"])
            tonal, danger = style("tonal"), style("danger")
            assert tonal[2] == danger[2] == th["shade_dark"]
            if (name, variant) == ("moy", "light"):    # its buttons.tonal.fill
                assert tonal[:2] == ([v for a, k, v in _live(name, variant).buttons
                                      if (a, k) == (None, "tonal")][0][0], th["value"])
            elif ui._C.theme_reads(th["surface"], th["value"]):
                assert tonal[:2] == (th["surface"], th["value"])
            else:
                assert tonal[:2] == (th["surface"], th["ink"])
            if ui._C.theme_reads(th["surface"], th["danger"]):
                assert danger[:2] == (th["surface"], th["danger"])
            else:
                assert danger[0] == th["danger"]
            assert style("toggle") == ui.state_colors(th, "cell", ui.REST)
            for kind in KINDS:          # the shared marks stay the style's
                assert style(kind, ui.PRESSED)[:2] == ui.state_colors(
                    th, ui._button_kind(kind), ui.PRESSED)[:2]
            assert _field(th, "primary") == _word(th["value"])
    _uninstall()


def test_a_theme_styles_each_kind_and_an_app_its_own():
    """`buttons[kind]` restyles a kind everywhere; every kind but primary
    takes normal's fill; `app_buttons[app][kind]` restyles it for that app
    only (THEMES.md, "buttons")."""
    from runtime import ui
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
        look = ui._look(th)
        kid = ui._kind_id(ui._button_kind("normal"))
        row = ui._app_row("calc")
        assert ui._C.button_style(look, kid, ui._STATE_ID[ui.REST], row)[2] is None
        assert ui._C.button_style(look, kid, ui._STATE_ID[ui.REST], -1)[2] == th["shade_dark"]
        assert ui.button_unreadable(th, ("calc",)) == []
    finally:
        _uninstall()


def test_an_unreadable_kind_is_reported_and_never_refused():
    from runtime import ui
    _install({"format": 2, "name": "ku", "inherits": "machine", "look": {
        "buttons": {"danger": {"fill": "#ffffff", "ink": "#fefefe"}},
        "app_buttons": {"calc": {"tonal": {"ink": "#ffffff", "fill": "#f0f0f0"}}}}})
    try:
        th = _live("ku").tokens
        assert ui.button_unreadable(th, ("calc", "files")) == [
            (None, "danger"), ("calc", "tonal"), ("calc", "danger"),
            ("files", "danger")]
        assert _field(th, "danger") == _word(_live("ku").buttons[3][2][0])
    finally:
        _uninstall()


def test_one_primary_per_surface_is_warned_of(capsys):
    """A second primary on one canvas in one frame is said once; the same
    button drawn twice, another canvas, or the next frame is not."""
    from runtime import ui
    from runtime.host_canvas import make_system_canvas
    th = theme_colors("machine")
    a, b = make_system_canvas(80, 60), make_system_canvas(80, 60)
    del ui.primary_warnings[:]
    ui._PRIMARY_WARNED.clear()
    ui.begin_frame()
    ui.button(a, th, (0, 0, 40, 20), "GO", kind="primary")
    ui.button(a, th, (0, 0, 40, 20), "GO", kind="primary")
    ui.button(b, th, (0, 0, 40, 20), "OK", kind="primary")
    ui.button(a, th, (0, 30, 40, 20), "NO", kind="tonal")
    assert ui.primary_warnings == []
    ui.button(a, th, (0, 30, 40, 20), "YES", kind="primary")
    ui.button(a, th, (0, 30, 40, 20), "YES", kind="primary")
    assert ui.primary_warnings == [("GO", "YES")]
    ui.begin_frame()
    ui.button(a, th, (0, 30, 40, 20), "YES", kind="primary")
    assert ui.primary_warnings == [("GO", "YES")]
    assert capsys.readouterr().out.count("second primary") == 1
    del ui.primary_warnings[:]
    ui._PRIMARY_WARNED.clear()
    ui.begin_frame()


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
    assert out.stdout.splitlines() == host["out"], out.stderr[-400:]
