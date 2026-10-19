"""A coverless cart's generated cover (design review D-13, owner 2026-10-19):
its title's initial on a colour block that stays the cart's."""

from runtime import ui_draw
from runtime.host_canvas import make_system_canvas


def test_a_titles_block_is_the_same_every_time_and_titles_spread():
    assert ui_draw.initial_block("Coin Quest") == ui_draw.initial_block("Coin Quest")
    blocks = {ui_draw.initial_block(t) for t in (
        "Coin Quest", "Pin Light", "Calc", "Files", "Paint", "Notes", "Storybook", "Bench")}
    assert len(blocks) >= 4, "eight titles share too few blocks"
    for block, ink in ui_draw.INITIAL_BLOCKS:
        assert 0 <= block < 64 and 0 <= ink < 64 and block != ink   # MOY64 indices


def test_the_cover_is_the_block_with_the_letter_on_it():
    cv = make_system_canvas(128, 128)
    cv.rect(0, 0, 128, 128, 0)
    ui_draw.initial_cover(cv, (0, 0, 128, 128), 0, "Pin Light")
    block, ink = ui_draw.initial_block("Pin Light")

    def px(x, y):
        i = (y * cv._stride + x) * 2
        return cv._buf[i] | (cv._buf[i + 1] << 8)
    assert px(2, 2) == cv._col(block)                  # the block, edge to edge
    cells = {px(x, y) for x in range(32, 96) for y in range(32, 96)}
    assert cv._col(ink) in cells                       # the letter on it
    ui_draw.initial_cover(cv, (0, 0, 128, 128), 0, "")  # no title: the block alone
    assert {px(x, y) for x in range(0, 128, 8) for y in range(0, 128, 8)} == {
        cv._col(ui_draw.initial_block("")[0])}


def test_the_letter_centres_on_its_own_ink():
    """The initial stands in the middle of its block by the pixels it
    lights, not by its 8-column cell (design review R4-20): on the hero's
    128 and a neighbour's 64 its ink is as far from either side, to a pixel."""
    for side in (128, 64):
        for title in ("Pin Light", "Moy", "Ivy"):
            cv = make_system_canvas(side, side)
            ui_draw.initial_cover(cv, (0, 0, side, side), 0, title)
            ink = cv._col(ui_draw.initial_block(title)[1])

            def px(x, y):
                i = (y * cv._stride + x) * 2
                return cv._buf[i] | (cv._buf[i + 1] << 8)
            xs = [x for x in range(side) for y in range(side) if px(x, y) == ink]
            ys = [y for x in range(side) for y in range(side) if px(x, y) == ink]
            assert abs(min(xs) - (side - 1 - max(xs))) <= 1, (side, title)
            assert abs(min(ys) - (side - 1 - max(ys))) <= 1, (side, title)
