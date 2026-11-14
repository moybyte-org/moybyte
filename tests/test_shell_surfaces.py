"""The shell surfaces' guard (docs/refactor_2026-11.md, phase 7): the bar's
strip is drawn by the frame, Settings is one page stack, a file opens one
way, no surface keeps a scroll of its own, the process-end sweeps run in
their stated order, and the Make tab marks a cart the crash guard turned off."""

import re
from pathlib import Path

import pytest

from runtime import bar_layer, files_app, host_app, intents, moy_carts, ui_draw
from ws_helpers import boot_on_fixture, build_ws

ROOT = Path(__file__).resolve().parents[1]
DT = 1.0 / 30


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    boot_on_fixture(monkeypatch)
    intents.INTENTS.reset()
    yield
    intents.INTENTS.reset()
    ui_draw.input_kind("keys")


def _frames(drv, n=3):
    for _ in range(n):
        drv.frame(DT)


# -- the strip -----------------------------------------------------------------

def test_the_strip_is_drawn_by_the_frame_but_where_strip_self_says_why():
    """Every file that calls the strip itself is one `STRIP_SELF` names with
    its reason, and each one it names still does."""
    call = re.compile(r"\._draw_status_strip\(")
    callers = set()
    for top in ("runtime", "device"):
        for p in (ROOT / top).rglob("*.py"):
            if p.name != "bar_layer.py" and call.search(p.read_text()):
                callers.add(p.name)
    assert callers == set(bar_layer.STRIP_SELF)
    assert not set(bar_layer.STRIP_OF) & {"cards", "blocks", "code"}


def test_settings_strip_is_the_frames_and_drawn_once(tmp_path):
    ws = build_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    _frames(drv)
    got = []
    real = ws.bar_layer._draw_status_strip
    ws.bar_layer._draw_status_strip = lambda where: (got.append(where), real(where))
    ws.mark_dirty()
    drv.frame(DT)
    assert got == ["settings"]


# -- Settings: one page stack ------------------------------------------------------

def test_settings_keeps_no_per_page_flag_and_back_pops(tmp_path):
    ws = build_ws(tmp_path)
    ws.open_settings()
    sl = ws.settings_layer
    assert [k for k, v in vars(sl).items() if k.endswith("_view") and isinstance(v, bool)] == []
    assert "page" not in vars(sl)
    sl.open_page("tz")
    sl.open_page("licences")
    assert sl.page == "licences" and sl.showing("tz")
    assert sl.back() and sl.page == "tz"
    assert sl.back() and sl.page is None
    assert sl.back() is False
    sl.open_page("achievements")
    sl.open_page("log")
    assert sl.page == "log" and not sl.showing("achievements"), "About's pages are one kind"


# -- a file opens one way ----------------------------------------------------------

def _files(ws, drv, place, name):
    ws.home.go_home()
    ws.open(next(c for c in ws.carts.all if c.get("title") == "Files"))
    _frames(drv)
    app = ws.files_app
    app.open_place(place, keep=name)
    return app


@pytest.mark.parametrize("kind,place,name,blob", [
    ("docs", files_app.DOCUMENTS, "todo", "# milk\n"),
    ("drawings", files_app.PICTURES, "dragon",
     moy_carts.encode_moyimg(8, 8, bytes((14,)) * 64)),
])
def test_open_and_a_single_candidate_open_with_land_in_one_app(tmp_path, kind, place,
                                                               name, blob):
    ws = build_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _frames(drv)
    ws.carts_store.save_file(kind, name, blob, ws.carts_root)
    mime = intents.mime_of(intents.item_of(kind, name))
    assert len(intents.INTENTS.candidates(mime)) == 1
    _files(ws, drv, place, name)._act("OPEN")
    _frames(drv)
    opened = ws.wm.top_kind(), ws.cart and ws.cart.get("title")
    _files(ws, drv, place, name)._act("WITH")
    _frames(drv)
    assert ws.confirm.open and len(ws.confirm.rows) == 1
    drv.press("a")
    _frames(drv)
    assert (ws.wm.top_kind(), ws.cart and ws.cart.get("title")) == opened


# -- no surface keeps a scroll of its own ------------------------------------------

# The surfaces that keep their own scroll, and why.
OWN_SCROLL = {
    "editors_code.py": "the code editor's buffer, caret and scroll (refactor §8)",
    "code_layer.py": "the code editor's view of that buffer",
    "editor_handle.py": "the cart editor handle's view of a CodeEditor buffer",
    "about_ui.py": "About's licence page (S12, after the sprint)",
}


def test_no_surface_keeps_a_scroll_offset_of_its_own():
    own = re.compile(r"self\.(_?scroll|_?scroll_[xy]|_?top|_?offset|_?yoff)\s*=(?!=)")
    keep = set()
    for p in sorted((ROOT / "runtime").rglob("*.py")):
        if p.parent.name not in ("system", "apps", "editors"):
            continue
        text = p.read_text()
        # (a top read back from the engine's text view is the engine's scroll)
        names = {n for n in own.findall(text)
                 if not re.search(r"self\.%s\s*=\s*_ui\.text_view\(" % n, text)}
        if names:
            keep.add(p.name)
    assert keep == set(OWN_SCROLL)


# -- the process-end sweeps ---------------------------------------------------------

def test_ends_runs_in_its_stated_order():
    import moybyte
    import aio
    import dev_handles
    import devices
    import leases
    devices._face("midi")
    devices._face("http")
    order = moybyte.END_ORDER
    at = [moybyte._END_AT[i] for i in range(len(moybyte._END_AT))]
    assert at == sorted(at)
    ends = moybyte.ENDS
    first = [dev_handles.sweep, devices._FACES["midi"].sweep, moybyte._snd_end,
             leases.LEASES.sweep, devices._FACES["http"].sweep]
    assert [ends.index(f) for f in first] == sorted(ends.index(f) for f in first)
    assert order.index("handles") < order.index("leases") < order.index("tasks")
    if aio._end in ends:
        assert ends.index(aio._end) > ends.index(leases.LEASES.sweep)


# -- the broken-cart mark ------------------------------------------------------------

BOOM = """
def _init():
    raise ValueError("always broken")


def _draw():
    cls(0)
"""


def test_a_cart_the_guard_turned_off_is_marked_on_make_and_one_it_forgave_is_not(tmp_path):
    import json
    d = tmp_path / "carts" / "boomy.moy"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"title": "Boomy", "canvas": "320x240", "moybyte": {"type": "app"}}))
    (d / "main.py").write_text(BOOM)
    ws = build_ws(tmp_path)
    boomy = next(c for c in ws.carts.all if c["title"] == "Boomy")
    for _ in range(ws.app_guard.STRIKES):
        ws.home.go_home()
        ws.open(boomy)
    ws.home.go_home()
    ws.home.set_home_tab("make")
    g = ws.launcher
    card = next(c for c in g.items if c.get("title") == "Boomy")
    assert g.is_broken(card)
    ws.home.set_home_tab("apps")
    assert not ws.launcher.is_broken(card), "the mark is the Make tab's"
    ws.spine.forgive_app(boomy)
    ws.home.set_home_tab("make")
    assert not ws.launcher.is_broken(card)
