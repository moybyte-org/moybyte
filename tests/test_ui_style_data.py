"""The toolkit's style and strings are DATA (docs/kernel_toolkit_2026-10.md
§2): native/moy_ui/style.json holds what the twin's tables hold, the strings
C draws are native/moy_ui/strings/en.json's, and every theme key a style spec
reads is in the token vocabulary the C resolution reads from.
"""

import json
import os
import re

import ui_twin as twin
from runtime.chrome import THEME_VARIANTS, THEMES, theme_colors

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLE = os.path.join(ROOT, "native", "moy_ui", "style.json")
STRINGS = os.path.join(ROOT, "native", "moy_ui", "strings", "en.json")
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


def test_the_style_file_is_the_twins_tables():
    d = _style()
    assert tuple(d["states"]) == twin.STATES
    assert {k: tuple(v) for k, v in d["aliases"].items()} == twin._STATE_ALIAS
    assert d["button_kinds"] == twin._BUTTON_KIND_KEY
    assert _kinds(d["kinds"]) == twin.DEFAULT_SPECS
    assert _metrics(d["metrics"]) == twin.DEFAULT_METRICS
    assert d["quirks"] == dict(twin.NON_DATA_QUIRKS)
    assert tuple(d["skin_order"]) == twin.ORDER
    for name, (specs, metrics) in twin.SKINS.items():
        sk = d["skins"][name]
        assert _kinds(sk.get("kinds", {})) == (specs or {})
        assert _metrics(sk.get("metrics", {})) == (metrics or {})


def test_the_metric_slots_are_named_once_per_kind():
    d = _style()
    for name, sk in d["skins"].items():
        for kind, slots in sk.get("metrics", {}).items():
            assert list(slots) == list(d["metrics"][kind]), (
                "skin %s names %s's slots differently" % (name, kind))
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
    tables = [twin.DEFAULT_SPECS] + [s for s, _m in twin.SKINS.values() if s]
    for table in tables:
        for states in table.values():
            for triple in states.values():
                for spec in triple:
                    _roles_read(spec, read)
    vocab = _vocabulary()
    held = set()
    for name, _t in THEMES:
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


def test_every_string_c_draws_has_its_entry():
    lits = _c_literals()
    for sid, text in _strings().items():
        assert re.match(r"^[a-z_]+\.[a-z_]+$", sid), sid
        # A board-defined name the preprocessor pastes in front of a
        # literal ("MOY_KERNEL_BUTTON_NAME" ": NEXT ...") is the entry's %s.
        bare = text[2:] if text.startswith("%s") else text
        hit = any(bare in lit for lit in lits)
        assert hit, "%s: %r is no literal in %s" % (sid, text, C_DRAWS)


def test_each_entry_keeps_its_conversions_in_order():
    for sid, text in _strings().items():
        convs = CONVERSION.findall(text)
        assert all(c in ("%s", "%u", "%d", "%08x") for c in convs), (sid, convs)
