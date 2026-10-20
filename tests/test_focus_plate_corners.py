"""The focus plate on a card's first or last row takes the card's corners,
and its corners are the same colour as the rest of it.

A row's plate is a tint of the mark's colour over the card's surface; on the
card's last row `moy_ui_group_end` redraws the two bottom corner squares with
the plate as a SOLID rounded rect that stands for that tint (MOY_UI_MARK_FOOT).
The solid colour was a mix that truncated where the tint rounds, so in Moy
Night a square one step darker sat in the plate's bottom-right corner (Settings,
Show FPS focused). Built on the shell the goldens build
(`tests/test_shell_goldens.py`'s configs and pinning)."""

import collections

import pytest

import test_shell_goldens as g

HANDHELD = "tdeck_320x240_fs1_dark"


@pytest.fixture(autouse=True)
def _uninstall():
    yield
    import moy_ui
    from runtime import iconsets
    moy_ui.theme_uninstall()
    iconsets.reset()


def _focused(tmp_path, mode, key):
    import moy_ui
    moy_ui.theme_uninstall()
    cfg = dict(g.CONFIGS[HANDHELD], variant=mode)
    if "layout" not in cfg:
        cfg["theme"] = "moy"
    ws = g._build(cfg, tmp_path / "carts")
    ws.open_settings()
    sl = ws.settings_layer
    sl.show_row(key)
    for _ in range(3):
        g._quiesce(ws)
        ws._dirty = True
        ws.frame(g._DT)
    return ws, sl._host.box(sl._focused)


def _pixels(ws):
    cv = ws.glass_canvas
    buf = bytes(cv._buf)
    return lambda x, y: buf[2 * (y * cv.w + x)] << 8 | buf[2 * (y * cv.w + x) + 1]


def _blocks_past_the_plate(px, box):
    """[(y, colour, run)] of the runs of two or more pixels of one colour
    that follow the plate's own run along a row of the plate's lower
    quarter: past the plate a row holds only the corner's antialiasing, each
    pixel its own step, until the ground."""
    x0, y0, w, h = box
    plate = collections.Counter(px(x, y) for y in range(y0, y0 + h)
                                for x in range(x0, x0 + w)).most_common(1)[0][0]
    out = []
    for y in range(y0 + h * 3 // 4, y0 + h):
        xs = range(x0 + w // 2, x0 + w + 4)
        row = [px(x, y) for x in xs]
        if row[0] != plate:
            continue                 # the card's frame under the plate
        k = 0
        while k < len(row) and row[k] == plate:
            k += 1
        rest = row[k:]
        ground = rest[-1] if rest else None
        run = 1
        for a, b in zip(rest, rest[1:]):
            run = run + 1 if a == b else 1
            if run == 2 and a != ground:
                out.append((y, hex(a), xs[k]))
    return out


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_the_last_rows_plate_is_one_colour_into_its_corner(tmp_path, mode):
    ws, box = _focused(tmp_path, mode, "show_fps")
    assert _blocks_past_the_plate(_pixels(ws), box) == []
