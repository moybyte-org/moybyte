"""Phase 3c: the shared thumbnail grid and the app toolbars speak `runtime/ui.py`.

`file_widgets.FileGridView` is the widget Files, Writer, Sheets and Paint's OPEN
mode all embed, so converting it pays four times; the two private button copies
this phase absorbed (`writer_app._hist_btn`, `sheets_app._icon_btn`) are the
duplication the refactor exists to end. What is pinned here is what the shell
goldens cannot see:

  * the grid's cells ARE `ui.cell` (pixel-compared against the toolkit itself,
    not against a transcription of it),
  * it registers NOTHING per cell -- the measured P4 constraint (~395 rects,
    ~9.3ms on the map tab) that `ui.cell_state` exists to keep,
  * its hover/pressed pump obeys `ui.Hits`'s four rules in index form, and
  * the private buttons are gone, with `disabled` now token-derived.

The rest -- "no pixel moved" -- is `tests/test_shell_goldens.py`'s job, and the
row/cell conversions were A/B'd against verbatim copies of the pre-conversion
code across 6 themes x 2 variants x 5 geometries before landing.
"""

import inspect
from pathlib import Path

from runtime import ui
from runtime.chrome import THEMES, THEME_VARIANTS, theme_colors
from runtime.file_widgets import FileGridView
from runtime.host_canvas import make_system_canvas as SystemCanvas


ROOT = Path(__file__).resolve().parent.parent
NAMES = ("dragon", "castle", "a name far too long for one tile", "kite")


class _Ptr:
    """The three fields the pump duck-types off widgets.Pointer."""

    def __init__(self, visible=True, down=False):
        self.visible = visible
        self.down = down


TOUCH = _Ptr(visible=False)              # touch places the pointer HIDDEN
CURSOR = _Ptr(visible=True)              # trackball / mouse


class _FakeWS:
    carts_store = None
    carts_root = None

    def _with_sd(self, fn):
        return fn()


def _grid(fs=1, rect=(0, 0, 320, 200), kind="sprites"):
    """A grid over `kind` -- deliberately NOT "drawings", so no thumbnail decode
    (and therefore no store) is reachable and every tile draws the placeholder."""
    g = FileGridView(_FakeWS(), kind)
    g.names = NAMES
    g.set_rect(rect, fs)
    return g


def _shot(w, h, fs, draw):
    cv = SystemCanvas(w, h, fs)
    draw(cv)
    return bytes(cv._buf)


# --- the cells ARE ui.cell -------------------------------------------------------

def test_every_tile_is_the_toolkit_cell_not_a_copy_of_it():
    """Pixel equality against `ui.cell` ITSELF (plus the caller's own art), for
    every theme family x variant and every selection state. A transcription
    would pass a shape test and fail this one."""
    for name, _tokens in THEMES:
        for variant in THEME_VARIANTS:
            th = theme_colors(name, variant)
            for sel in (-1, 0, 2):
                g = _grid()
                g.sel = sel

                def by_hand(cv, th=th, g=g, sel=sel):
                    for i in range(len(g.names)):
                        r = g._cell_rect(i)
                        ax, ay, _aw, _ah = ui.cell(
                            cv, th, r, g.names[i], on=(i == sel), fs=g.fs)
                        ui_art = ui.cell_art_rect(r, g.fs, 2 * g.fs, 14 * g.fs)
                        cv.rect(ax, ay, ui_art[2], ui_art[3],
                                th.get("edge", 13))

                assert _shot(320, 200, 1, lambda cv: g.draw(cv, th)) == \
                    _shot(320, 200, 1, by_hand), (name, variant, sel)


def test_the_grid_registers_nothing_per_cell():
    """The module docstring's measured rule: a grid hit-tests ARITHMETICALLY and
    feeds `ui.cell_state` two indices. One `Hits` rect per cell would be ~395
    rects / ~9.3ms on the P4's worst tab, so the widget must not even be able
    to grow one by accident."""
    src = (ROOT / "runtime" / "file_widgets.py").read_text(encoding="utf-8")
    assert "_ui.Hits" not in src and "hits=" not in src and ".add(" not in src
    assert "cell_state" in src
    assert "_index_at" in inspect.getsource(FileGridView.tap), (
        "tap() and the cue pump must share ONE arithmetic hit-test")


def test_quiet_grid_is_byte_identical_to_no_cue_at_all():
    """The cues default OFF (-1, which is also how `sel` spells 'nothing'), so
    a grid nobody has pointed at renders exactly the frozen pixels."""
    th = theme_colors("night")
    g = _grid()
    quiet = _shot(320, 200, 1, lambda cv: g.draw(cv, th))
    assert g.hover == -1 and g.pressed == -1
    g.hover = g.pressed = -1
    assert _shot(320, 200, 1, lambda cv: g.draw(cv, th)) == quiet


# --- the pump --------------------------------------------------------------------

def _center(g, i):
    x, y, w, h = g._cell_rect(i)
    return x + w // 2, y + h // 2


def test_touch_presses_but_never_hovers():
    """Touch places the pointer HIDDEN, so it has no hover by nature -- and
    `pressed` is the only cue a finger ever gets."""
    g = _grid()
    px, py = _center(g, 1)
    assert g.pointer_frame(px, py, TOUCH) is False        # a hidden, up pointer
    assert (g.hover, g.pressed) == (-1, -1)
    assert g.pointer_frame(px, py, _Ptr(visible=False, down=True)) is True
    assert (g.hover, g.pressed) == (-1, 1)


def test_a_pointing_cursor_hovers_and_a_down_one_does_not():
    g = _grid()
    px, py = _center(g, 2)
    assert g.pointer_frame(px, py, CURSOR) is True
    assert (g.hover, g.pressed) == (2, -1)
    assert g.pointer_frame(px, py, _Ptr(visible=True, down=True)) is True
    assert (g.hover, g.pressed) == (-1, 2)                # down never hovers


def test_the_press_edge_picks_the_target_and_sliding_off_drops_the_cue():
    """`Hits`'s rule, index form: the cue belongs to the tile the finger went
    DOWN on, and it clears while the finger is outside that tile."""
    g = _grid()
    down = _Ptr(visible=False, down=True)
    g.pointer_frame(*_center(g, 0), pointer=down)
    assert g.pressed == 0
    g.pointer_frame(*_center(g, 1), pointer=down)         # slid onto another
    assert g.pressed == -1
    g.pointer_frame(*_center(g, 0), pointer=down)         # slid back
    assert g.pressed == 0
    g.pointer_frame(*_center(g, 0), pointer=TOUCH)        # released
    assert g.pressed == -1


def test_leave_drops_both_cues_and_the_press_history():
    g = _grid()
    g.pointer_frame(*_center(g, 1), pointer=CURSOR)
    assert g.pointer_leave() is True
    assert (g.hover, g.pressed) == (-1, -1)
    assert g.pointer_leave() is False                     # already quiet: free
    # No pointer past: the next sample is a fresh press EDGE, which is what a
    # touch tier needs (the first sample a focused surface sees IS the finger).
    g.pointer_frame(*_center(g, 1), pointer=_Ptr(visible=False, down=True))
    assert g.pressed == 1


def test_a_relist_drops_stale_cue_indices():
    """Newest-first reorders, so an index-keyed cue would point at a different
    file. (The SELECTION is keyed by name and deliberately survives.)"""
    g = _grid()
    g.pointer_frame(*_center(g, 1), pointer=CURSOR)
    g.refresh()                                            # empty store: no names
    assert (g.hover, g.pressed) == (-1, -1)


def test_the_cue_paints_only_the_edge_at_rest_geometry():
    """ADDITIVE by construction (ui.state_colors): a hovered tile changes paint,
    never geometry -- so the label and the art land on the same pixels."""
    th = theme_colors("night")
    g = _grid()
    quiet = _shot(320, 200, 1, lambda cv: g.draw(cv, th))
    g.hover = 1
    hovered = _shot(320, 200, 1, lambda cv: g.draw(cv, th))
    assert hovered != quiet
    x, y, w, h = g._cell_rect(1)
    for i in range(len(quiet)):
        if quiet[i] == hovered[i]:
            continue
        px, py = (i // 2) % 320, (i // 2) // 320
        on_border = (px in (x, x + w - 1) and y <= py < y + h) or \
                    (py in (y, y + h - 1) and x <= px < x + w)
        assert on_border, "hover moved a pixel at %d,%d, off the cell edge" % (
            px, py)


# --- the embedders pump it -------------------------------------------------------

def test_every_grid_embedder_pumps_on_non_click_samples():
    """A press cue that only appeared on the CLICK frame would never be seen:
    the pump has to run before each app's `if not click: return`."""
    for mod, fn in (("files_app", "FilesAppLayer"),
                    ("writer_app", "WriterAppLayer"),
                    ("sheets_app", "SheetsAppLayer")):
        src = (ROOT / "runtime" / (mod + ".py")).read_text(encoding="utf-8")
        body = src.split("def handle_pointer(", 1)[1]
        head = body.split("if not click", 1)[0]
        assert "grid.pointer_frame(" in head, mod


# --- the private button copies ---------------------------------------------------

def test_the_two_private_button_copies_are_gone():
    """`writer_app._hist_btn` and `sheets_app._icon_btn` were two hand-rolled
    copies of `ui.chip` that disagreed with each other about what an enabled
    icon button looks like. Both are absorbed; `disabled` is the toolkit's."""
    for mod, gone in (("writer_app", "_hist_btn"), ("sheets_app", "_icon_btn")):
        src = (ROOT / "runtime" / (mod + ".py")).read_text(encoding="utf-8")
        assert ("def " + gone) not in src, mod
        assert gone not in src.replace("`" + gone + "`", ""), mod
        assert "disabled=not enabled" in src, mod


def test_the_disabled_history_chip_dims_through_the_theme_role():
    """The affordance is the INK (there is no dimmed sprite), and it now comes
    from the theme's own `ink_dim` role rather than a hardcoded palette index --
    so it stays legible in all 12 family x variant sets."""
    from runtime import writer_app

    for name, _tokens in THEMES:
        for variant in THEME_VARIANTS:
            th = theme_colors(name, variant)
            seen = []

            class _WS:
                theme_colors = th

                @staticmethod
                def _glyph(kind, rect, color, cv):
                    seen.append(color)

            app = writer_app.WriterAppLayer.__new__(writer_app.WriterAppLayer)
            app.ws = _WS()
            app.names = {}
            app.layout = writer_app.WriterLayout(320, 240, 1)
            cv = SystemCanvas(320, 240, 1)
            app._button(cv, "undo", (4, 4, 18, 18), glyph="undo", enabled=True)
            app._button(cv, "redo", (26, 4, 18, 18), glyph="redo", enabled=False)
            assert seen == [th["title_ink"],
                            ui.state_token(th, "disabled_ink", 6)], (name, variant)
            assert seen[0] != seen[1], (name, variant)


# --- the row conversions ---------------------------------------------------------

def test_the_converted_row_draws_go_through_the_toolkit():
    """The three list surfaces in this file group draw rows with `ui.row`; the
    two whose pixels are frozen OFF-token pass `colors=`, which is what that
    escape hatch is for."""
    from runtime import files_app, sheets_app, storybook_app

    assert "_ui.row(" in inspect.getsource(files_app.FilesAppLayer._draw_rows)
    attach = inspect.getsource(sheets_app.SheetsAppLayer._draw_attach)
    assert "_ui.row(" in attach and "colors=" in attach
    deck = inspect.getsource(storybook_app.StorybookAppLayer._draw_rows)
    assert "_ui.row(" in deck and "colors=" in deck
    for src in (inspect.getsource(files_app.FilesAppLayer._draw_rows), attach,
                deck):
        assert "cv.rectb(" not in src and "cv.rect(" not in src
