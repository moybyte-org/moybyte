"""`FileGridView`'s page chips and its ("page", +/-1) tap protocol.

The one body behind Files, Writer, Sheets and Paint's OPEN dialog. Nothing in
the suite ever filled more than one page, so `_page_rects` and every paging
branch of `tap` had never executed -- verified 2026-08-22 by raising inside the
body and running all 2819 tests without a hit.
"""

from runtime.file_widgets import FileGridView


class _FakeWS:
    carts_store = None
    carts_root = None

    def _with_sd(self, fn):
        return fn()


def _grid(n, rect=(0, 0, 320, 200), fs=1):
    """`kind` is deliberately not "drawings": no thumbnail decode, so no store."""
    g = FileGridView(_FakeWS(), "sprites")
    g.names = tuple("f%02d" % i for i in range(n))
    g.set_rect(rect, fs)
    return g


def _one_page():
    g = _grid(1)
    return g._per_page()


def test_the_fixture_actually_pages():
    per = _one_page()
    assert _grid(per)._pages() == 1
    assert _grid(per + 1)._pages() == 2


def test_the_chips_sit_inside_the_grid_and_do_not_overlap():
    g = _grid(_one_page() * 2 + 1)
    x, y, w, h = g._rect
    prev_r, next_r = g._page_rects()
    for r in (prev_r, next_r):
        assert r[0] >= x and r[1] >= y
        assert r[0] + r[2] <= x + w and r[1] + r[3] <= y + h
    assert prev_r[0] + prev_r[2] <= next_r[0]      # < is left of >


def _centre(r):
    return (r[0] + r[2] // 2, r[1] + r[3] // 2)


def test_the_chips_page_and_wrap_in_both_directions():
    g = _grid(_one_page() * 2 + 1)          # 3 pages
    assert g._pages() == 3
    prev_r, next_r = g._page_rects()
    assert g.tap(*_centre(next_r)) == ("page", 1) and g.page == 1
    assert g.tap(*_centre(next_r)) == ("page", 1) and g.page == 2
    assert g.tap(*_centre(next_r)) == ("page", 1) and g.page == 0   # wraps
    assert g.tap(*_centre(prev_r)) == ("page", -1) and g.page == 2  # wraps back


def test_a_single_page_grid_has_no_chips_to_tap():
    """The chips are not DRAWN below two pages, so they must not be tappable
    either -- a live chip under an unpainted one is a tap that does nothing
    visible."""
    g = _grid(_one_page())
    assert g._pages() == 1
    prev_r, next_r = g._page_rects()
    for r in (prev_r, next_r):
        got = g.tap(*_centre(r))
        assert got is None or got[0] != "page"
    assert g.page == 0


def test_paging_re_indexes_the_tiles_under_the_pointer():
    """A tile tap resolves against the CURRENT page, so the same coordinates
    must name a different file after paging."""
    per = _one_page()
    g = _grid(per * 2)
    first = g.tap(*_centre(g._cell_rect(0)))
    assert first == ("sel", "f00")
    g.tap(*_centre(g._page_rects()[1]))
    assert g.page == 1
    assert g.tap(*_centre(g._cell_rect(0))) == ("sel", g.names[per])


def test_a_tap_outside_every_chip_and_tile_is_None():
    g = _grid(_one_page() * 2)
    x, y, w, h = g._rect
    assert g.tap(x + w + 50, y + h + 50) is None
