"""`look_gen`: one integer every cache whose pixels read the look folds
(docs/theming_2026-09.md §6, phase 6 step 1).

An in-place edit is a re-install under the theme's own name: the name, the
variant and every dict a cache could hold stay the same, so a cache keyed on
any of them would keep the old pixels. The net here is the strongest one a
host has -- a console that took the edit live draws, byte for byte, what a
console that booted under the edited package draws -- with the per-cache
keys checked beside it, and a re-install loop that proves the caches replace
their entries instead of growing.
"""

import json

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import chrome, ui
from ws_helpers import build_desktop_ws, build_ws

THEME = "mine"
BASE = "berry"
_CLOCK = "12:34"


@pytest.fixture(autouse=True)
def _seeds_only():
    yield
    moy_ui.theme_uninstall()
    chrome.forget_themes()


def _pkg(**change):
    """A package over BERRY with `change` as its dark roles: what the theme
    editor's save installs."""
    return json.dumps({"format": 1, "name": THEME, "inherits": BASE,
                       "colors": {"dark": change}}).encode()


def _quiet(ws):
    ws.pointer.visible = False
    ws._toast_until = 0
    ws.bar_layer._clock_text = lambda: _CLOCK


def _world(tmp_path, desk):
    if desk:
        ws = build_desktop_ws(tmp_path)
    else:
        ws = build_ws(tmp_path)
    ws.look.set_theme(THEME if moy_ui.theme_installed() == THEME else BASE,
                      persist=False)
    _quiet(ws)
    if desk:
        ws.open_desk()
        assert ws.open_app(ws._apps_by_id["files"])
        ws.open_settings()               # focused; files stays behind it
    return ws


def _settle(ws, force=True):
    """Frames until two in a row are identical; the pixels."""
    cv = ws.sys_canvas
    prev = None
    for _ in range(12):
        _quiet(ws)
        if force:
            ws._dirty = True
        ws.frame(1 / 30)
        cur = bytes(cv._buf)
        if cur == prev:
            return cur
        prev = cur
    raise AssertionError("never settled")


def _keys(ws, desk):
    """The keys of the caches the step names, as this world holds them."""
    cv = ws.sys_canvas
    out = {"statics": ws.launcher_layer._statics_key(cv),
           "retained": ws.launcher_layer._retained_key(cv),
           "bar": ws.bar_layer._cart_bar_key("home")}
    if desk:
        wm = ws.wm
        out["desk"] = wm._desk_sig
        out["chrome"] = tuple(getattr(w, "_chrome_sig", None) for w in wm._wins.values())
    return out


# A role each cache's pixels show: the bar strips, the home's statics and
# its retained frame, the window chrome (focused and behind), the windows'
# content, the desk.
_CASES = [(False, "bar"), (False, "panel"), (False, "accent"), (False, "surface"),
          (True, "bar"), (True, "title_active"), (True, "title_inactive"),
          (True, "panel"), (True, "accent"), (True, "title_ink")]


@pytest.mark.parametrize("desk,role", _CASES,
                         ids=["%s-%s" % ("desk" if d else "console", r) for d, r in _CASES])
def test_an_in_place_edit_draws_what_a_cold_boot_under_it_draws(tmp_path, desk, role):
    first = chrome.theme_colors(BASE)[role]
    ws = _world(tmp_path / "live", desk)
    assert ws.look.install_theme(_pkg())[0] == 0
    ws.look.set_theme(THEME, persist=False)
    before = _settle(ws)
    keys = _keys(ws, desk)
    gen = ws.look.look_gen
    edited = _pkg(**{role: (first + 9) % 64})
    drawn = ws._frames_drawn
    assert ws.look.install_theme(edited)[0] == 0
    assert ws.look.look_gen > gen
    assert ws.look.theme_name == THEME
    assert ws._dirty                      # the edit is one Class A dirty
    after = _settle(ws, force=False)      # no forced repaint: the edit's own
    assert ws._frames_drawn > drawn
    assert after != before, "the edited role shows nowhere on this world"
    for name, key in _keys(ws, desk).items():
        assert key != keys[name], name
    if desk:                              # every window buffer, focused or behind
        assert {w.kind: w._look_gen for w in ws.wm._wins.values() if w.buf is not None} \
            == {w.kind: ws.look.look_gen for w in ws.wm._wins.values() if w.buf is not None}

    # Idle after the edit: nothing paints.
    drawn = ws._frames_drawn
    for _ in range(4):
        _quiet(ws)
        ws.frame(1 / 30)
    assert ws._frames_drawn == drawn

    moy_ui.theme_uninstall()
    chrome.forget_themes()
    assert moy_ui.theme_install(edited)[0] == 0
    cold = _world(tmp_path / "cold", desk)
    assert _settle(cold) == after


def test_the_toolkit_ring_re_resolves_a_dict_it_holds(tmp_path):
    """The look ring keys on (dict, look_gen): the same dict object re-binds
    in its own slot after a publish, never a second entry."""
    ws = build_ws(tmp_path)
    th = ws.theme_colors
    ui.state_colors(th, "button", ui.REST)
    ring = [e for e in ui._RING if e[0] is th]
    assert len(ring) == 1
    slot, gen = ring[0][1], ring[0][3]
    ws.look.publish()
    ui.state_colors(th, "button", ui.REST)
    ring = [e for e in ui._RING if e[0] is th]
    assert len(ring) == 1 and ring[0][1] == slot and ring[0][3] != gen


@pytest.mark.parametrize("desk", [False, True], ids=["console", "desk"])
def test_a_change_through_the_verb_draws_what_a_boot_of_custom_draws(tmp_path, desk):
    """Phase 6 step 5: `Appearance.change` is the same in-place install, from
    bytes at the next frame -- the caches move with `look_gen`, idle paints
    nothing after it, and a boot of the `custom` it wrote draws the same. The
    desk holds Settings alone: a Files window would list the package the
    change wrote, which is the store's news, not the look's."""
    def boot():
        ws = (build_desktop_ws if desk else build_ws)(tmp_path)
        _quiet(ws)
        if desk:
            ws.open_desk()
            ws.open_settings()
        return ws

    ws = boot()
    before = _settle(ws)
    keys = _keys(ws, desk)
    gen = ws.look.look_gen
    accent = next(a for _n, a, _d in moy_ui.theme_accents()
                  if a != ws.theme_colors["accent"])
    assert ws.look.change("accent", accent) == 0
    drawn = ws._frames_drawn
    after = _settle(ws, force=False)      # the change's own repaint
    assert ws.look.theme_name == "custom" and ws.look.look_gen > gen
    assert ws._frames_drawn > drawn and after != before
    for name, key in _keys(ws, desk).items():
        assert key != keys[name], name
    drawn = ws._frames_drawn
    for _ in range(4):
        _quiet(ws)
        ws.frame(1 / 30)
    assert ws._frames_drawn == drawn
    ws.look.commit()

    moy_ui.theme_uninstall()
    chrome.forget_themes()
    cold = boot()
    assert cold.look.theme_name == "custom"
    assert _settle(cold) == after


@pytest.mark.parametrize("desk", [False, True], ids=["console", "desk"])
def test_re_installing_replaces_every_cache_entry(tmp_path, desk):
    """N re-installs under one name: each cache keeps what one look needs."""
    ws = _world(tmp_path, desk)

    def sizes():
        strips = ws.bar_layer._bar_strips
        return (len(ui._RING), len(chrome._THEME_CACHE), len(chrome._CONTENT_CACHE),
                len(ws.look._layouts), len(strips),
                max((len(v) for v in strips.values()), default=0),
                ws.wm._backdrop is not None if desk else None)

    for i in range(3):
        assert ws.look.install_theme(_pkg(accent=i))[0] == 0
        ws.look.set_theme(THEME, persist=False)
        _settle(ws, force=False)
    want = sizes()
    for i in range(12):
        assert ws.look.install_theme(_pkg(accent=3 + i, panel=i))[0] == 0
        _settle(ws, force=False)
        assert sizes() == want, i
    assert len(ui._RING) <= ui._RING_MAX


def test_a_refused_install_moves_nothing(tmp_path):
    ws = build_ws(tmp_path)
    gen = ws.look.look_gen
    rc, _notes = ws.look.install_theme(b"{")
    assert rc != 0 and ws.look.look_gen == gen and not moy_ui.theme_installed()


def test_an_icon_sheet_moves_the_look(tmp_path):
    """The bar strips and the desk read the icon sheet: adopting one is a
    publish, so their keys carry it through `look_gen`."""
    ws = build_ws(tmp_path)
    key = ws.bar_layer._cart_bar_key("home")
    ws.look.set_icon_sheet(ws.look.icon_sheet)
    assert ws.bar_layer._cart_bar_key("home") != key
