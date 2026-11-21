"""The keys between two pages on the C engine, each in a context of its
own (`moy_ui_api_edge`, `moy_ui_api_enter`): a step that finds no stop past
the focused one answers its way and leaves focus where it was; the page
the keys come into lands focus on its stop nearest the side they come in
by, in the frame that draws it. Its ring and its hints follow: the glass of
two C pages is the glass the reference engine recorded for two of its own
pages, frame by frame (tests/uitree_reference/)."""

import sys
from pathlib import Path

import pytest

import uitree_reference as R

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

DT = 1 / 30


class Inp:
    typed = 0
    mods = 0

    def __init__(self, k):
        self.k = k

    def pressed(self, n):
        return n == self.k


class Damage:
    def all(self):
        pass


@pytest.fixture(scope="module")
def glass(tmp_path_factory):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path_factory.mktemp("edge")), sys_size=(320, 240), font_scale=1)
    return ws.sys_canvas, ws.theme_colors


def _session(glass, across, script):
    """Two pages on the C engine, side by side where
    `across`, else one over the other; `script` the keys, each to the page
    holding them, an edge handing them on. Answers (frames, [(page holding
    the keys, each page's focus)] a step)."""
    import ui
    import ui_draw
    import uitree as S
    import uitree_app
    import uitree_c
    import uitree_two_pages as F
    cv, th = glass
    W, H = cv.w, cv.h
    boxes = ([(0, 0, W // 2, H), (W // 2, 0, W - W // 2, H)] if across
             else [(0, 0, W, H // 2), (0, H // 2, W, H - H // 2)])
    pages = []
    for build in (F.first, F.second):
        e = uitree_c.CEngine(S.decl_of(F.__name__), build, ui_draw.AppHost(), None, Damage())
        e.open()
        pages.append(e)
    owner = [0]
    frames, seen = [], []

    def frame():
        cv.rect(0, 0, W, H, 0)
        for i, (e, box) in enumerate(zip(pages, boxes)):
            e._ui.begin(cv, th, box, elsewhere=i != owner[0])
            e.paint(DT, cv, th, box, False)
            ui.end()
        frames.append(bytes(cv._buf))
        seen.append((owner[0], pages[0].focus_key, pages[1].focus_key))

    frame()
    frame()
    for k in script:
        pages[owner[0]].keys(Inp(k))
        frame()
        way = pages[owner[0]].edge()
        nxt = owner[0] + (1 if way & (uitree_app.EDGE_DOWN | uitree_app.EDGE_RIGHT) else -1)
        if way and 0 <= nxt < 2:
            owner[0] = nxt
            pages[nxt].enter(way)
            frame()
    return frames, seen


DOWN_UP = ["down", "down", "down", "down", "up", "up", "up"]
RIGHT_LEFT = ["down", "right", "down", "left", "right"]


@pytest.mark.parametrize("across", (False, True), ids=("stacked", "side_by_side"))
def test_the_keys_cross_between_two_pages_as_the_reference_did(glass, across):
    script = RIGHT_LEFT if across else DOWN_UP
    got, seen = _session(glass, across, script)
    R.held(__file__, "two_refs/" + ("side_by_side" if across else "stacked"), [got, seen])


def test_an_edge_hands_the_keys_on_and_the_page_entered_lands_by_its_side(glass):
    _f, seen = _session(glass, False, DOWN_UP)
    owners = [s[0] for s in seen]
    assert 1 in owners and owners[-1] == 0, seen
    # down past "three" enters the second page at its first stop; up past "four" the first at its last
    i = owners.index(1)
    assert seen[i][2] == "four" and seen[i - 1][1] == "three", seen
    j = owners.index(0, i)
    assert seen[j][1] == "three", seen
    _f, seen = _session(glass, True, RIGHT_LEFT)
    i = [s[0] for s in seen].index(1)
    assert seen[i][2] == "four", seen         # right from "two": the second page's leftmost, topmost
    j = [s[0] for s in seen].index(0, i)
    assert seen[j][1] == "one", seen          # left from "five": the first page's rightmost, topmost
