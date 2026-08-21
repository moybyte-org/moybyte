"""The Desk-Lab list shell's scroll window + nav, EXECUTED (`runtime/app_shell.py`).

`ListShellApp._scroll_list` / `_list_nav` are the row-list nav extracted FROM
three hand-copies (Files' trash/game/used lists, Sheets' attach picker,
Storybook's shelf and page rows). Until this file nothing executed either one:
a `sys.settrace` sweep of the whole suite saw only their `def` lines. The
arithmetic is small and entirely off-by-one shaped -- which edge scrolls, by how
much, and whether an end wraps or stops -- so it is pinned against the real
`ListShellLayout` geometry, plus one real consumer driving the same body through
its own `handle_input`.
"""

import pytest

from runtime.app_shell import ListShellApp, ListShellLayout


class _Layout(ListShellLayout):
    def __init__(self, h=240, fs=1):
        self._init_frame(320, h, fs, False)
        self._init_list(self.bar_h)


class _List(ListShellApp):
    """The four attributes the two shared verbs read, and nothing else."""

    def __init__(self, h=240):
        self.layout = _Layout(h)
        self.sel = 0
        self.top = 0
        self.opened = []

    def _tap_row(self, i):
        self.opened.append(i)


class _Inp:
    def __init__(self, *held):
        self._held = held

    def pressed(self, name):
        return name in self._held


ROWS = 11           # _Layout(240).list_rows -- asserted below, not assumed
COUNT = 20


@pytest.fixture
def lst():
    app = _List()
    assert app.layout.list_rows == ROWS
    return app


# -- the scroll window --------------------------------------------------------

def test_the_window_follows_a_selection_off_the_bottom_by_one_row(lst):
    """Stepping past the last visible row scrolls exactly one row, not a page."""
    lst.sel = ROWS
    lst._scroll_list()
    assert lst.top == 1
    lst.sel = ROWS + 1
    lst._scroll_list()
    assert lst.top == 2


def test_the_window_follows_a_selection_off_the_top_to_that_row(lst):
    lst.top = 5
    lst.sel = 3
    lst._scroll_list()
    assert lst.top == 3


def test_a_selection_inside_the_window_never_moves_it(lst):
    """Both inclusive edges: the top row and the LAST visible row are inside."""
    lst.top = 5
    for lst.sel in (5, 5 + ROWS - 1):
        lst._scroll_list()
        assert lst.top == 5


def test_a_list_shorter_than_the_window_never_scrolls(lst):
    lst.sel = 2
    lst._scroll_list()
    assert lst.top == 0


def test_an_empty_list_leaves_the_window_at_the_top(lst):
    lst._scroll_list()
    assert lst.top == 0


# -- the nav verbs ------------------------------------------------------------

def test_down_wraps_off_the_end_and_the_window_comes_back_with_it(lst):
    lst.sel, lst.top = COUNT - 1, COUNT - ROWS
    assert lst._list_nav(_Inp("down"), COUNT) is True
    assert (lst.sel, lst.top) == (0, 0)


def test_up_wraps_to_the_end_and_scrolls_the_window_onto_it(lst):
    assert lst._list_nav(_Inp("up"), COUNT) is True
    assert lst.sel == COUNT - 1
    assert lst.top == COUNT - ROWS


def test_down_inside_the_window_moves_the_selection_only(lst):
    lst._list_nav(_Inp("down"), COUNT)
    assert (lst.sel, lst.top) == (1, 0)


def test_a_opens_the_selected_row_and_moves_nothing(lst):
    lst.sel = 4
    assert lst._list_nav(_Inp("a"), COUNT) is True
    assert lst.opened == [4]
    assert (lst.sel, lst.top) == (4, 0)


def test_a_quiet_frame_is_still_claimed(lst):
    """Always True: the list mode owns the input, so the shell must not go on to
    hand the same frame to anything behind it."""
    lst.sel, lst.top = 3, 1
    assert lst._list_nav(_Inp(), COUNT) is True
    assert (lst.sel, lst.top, lst.opened) == (3, 1, [])


def test_only_one_verb_fires_per_frame(lst):
    """up/down/a are an elif ladder -- a frame holding all three is one step."""
    lst._list_nav(_Inp("up", "down", "a"), COUNT)
    assert lst.sel == COUNT - 1 and lst.opened == []


# -- a real consumer reaches this body ----------------------------------------

def test_storybooks_shelf_scrolls_through_the_shared_body(tmp_path):
    """The extraction is only worth anything while the apps still route into it,
    so drive the whole way in: `StorybookAppLayer.handle_input` -> `_list_nav`
    -> `_scroll_list`, on the app's own layout."""
    from runtime import host_app
    from runtime.storybook_app import StorybookAppLayer

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Storybook":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    app = ws.storybook_app
    assert isinstance(app, StorybookAppLayer) and app.mode == "shelf"

    rows = app.layout.list_rows
    app._stories = lambda: [{"title": "S%d" % i} for i in range(rows + 4)]
    count = rows + 5                        # the shelf's rows + its NEW row

    src = ws.input.source("kbd")
    for _ in range(count - 1):
        src.set_held("down", True)
        ws.input.begin_frame()
        app.handle_input(ws.input)
        src.set_held("down", False)
        ws.input.begin_frame()
    assert app.sel == count - 1
    assert app.top == count - rows          # the window walked with it
