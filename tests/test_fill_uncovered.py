"""ui.fill_uncovered must paint exactly what the two full fills painted.

The "clear the body, then clear the panel on top of it" idiom wrote ~94% of a
450K-pixel rect twice in the same colour on the P4's editor tabs. Replacing the
second fill with only its uncovered sliver is a pure win ONLY if it is
pixel-identical, and it is not obviously so: `panel` is not contained in
`body_fill` -- its top edge sits 2*fs px above, under the bar -- so the fill
cannot simply be dropped.

These compare the new path against the old one on a real Canvas.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import ui as _ui                     # noqa: E402
from runtime.host_canvas import make_canvas as Canvas   # noqa: E402


def _both(inner, outer, w=200, h=120, col=7):
    """(old two-fill buffer, new fill_uncovered buffer)."""
    a = Canvas(w, h)
    a.cls(0)
    a.rect(*(tuple(outer) + (col,)))
    a.rect(*(tuple(inner) + (col,)))              # the redundant full re-fill
    b = Canvas(w, h)
    b.cls(0)
    b.rect(*(tuple(outer) + (col,)))
    _ui.fill_uncovered(b, inner, outer, col)
    return bytes(a._buf), bytes(b._buf)


def test_matches_the_two_fill_version_for_the_real_editor_geometry():
    # The shape the editor tabs actually use: the panel overhangs the body by
    # 2*fs at the top (panel y = bar_h - 2fs, body y = bar_h) and is inset
    # horizontally, so exactly one thin strip is uncovered.
    for fs in (1, 2, 3):
        bar_h = 18 * fs
        w, h = 200 * fs, 120 * fs
        body = (0, bar_h, w, h - bar_h)
        panel = (8 * fs, bar_h - 2 * fs, w - 16 * fs,
                 h - (bar_h - 2 * fs) - 20 * fs)
        old, new = _both(panel, body, w, h)
        assert old == new, "fs=%d" % fs


def test_inner_fully_inside_outer_emits_nothing():
    """The Scene pane's layout: panel is inset inside body on all four sides, so
    the whole second fill is redundant."""
    body = (10, 10, 150, 90)
    panel = (12, 12, 146, 86)
    old, new = _both(panel, body)
    assert old == new
    calls = []
    _ui.fill_uncovered(type("C", (), {"rect": lambda s, *a: calls.append(a)})(),
                       panel, body, 7)
    assert calls == [], "a fully covered rect must not draw at all"


def test_every_overhang_direction_and_the_disjoint_case():
    body = (40, 40, 60, 40)
    for panel in (
        (30, 30, 80, 60),      # overhangs on all four sides
        (30, 45, 40, 20),      # left only
        (80, 45, 40, 20),      # right only
        (45, 20, 20, 40),      # top only
        (45, 60, 20, 40),      # bottom only
        (10, 10, 20, 20),      # disjoint -> the whole rect is uncovered
        (40, 40, 60, 40),      # identical -> nothing
    ):
        old, new = _both(panel, body)
        assert old == new, panel
