"""A dialog opened between frames shows its card on the first frame that
draws it, as it shows it at rest (native/moy_ui/moy_ui_api_table.c,
moy_ui_api_dialog): no body height is kept for it yet, so its first frame
draws the card as tall as the surface lets it, lays its body out in that room
and moves the card to its place once the body closes. The engine is one body
under every face, so a Python app's `ui.dialog` and a Lua app's draw it the
same.

Each app draws a page of words and, from its OPEN-th frame, a dialog with a
few rows or more than the surface holds (cut). Without buttons nothing in it
is a stop, so the frame that opens it is the frame at rest, pixel for pixel,
and the one before it shows no card. With buttons, focus opens on the first
of them on the frame after (as a popover's does), so the opening frame
differs from the frame at rest by that button's focus mark alone.
"""

import json
import os

import pytest

from runtime import host_app
from test_ui_linked import _frame
from ws_helpers import open_cart

OPEN = 5

PY = '''F = [0]


def _draw():
    ui.page("Dlg")
    F[0] += 1
    for i in range(6):
        ui.label("The page's line %d" % i)
    if F[0] >= OPEN:
        if ui.dialog("Sure?", BUTTONS) >= 0:
            F[0] = 0
        for i in range(ROWS):
            ui.label("Row %d of the card" % i)
        ui.dialog()
'''

LUA = '''F = 0
function _draw()
  ui.page("Dlg")
  F = F + 1
  for i = 0, 5 do ui.label("The page's line " .. i) end
  if F >= OPEN then
    if ui.dialog("Sure?", BUTTONS) > 0 then F = 0 end
    for i = 0, ROWS - 1 do ui.label("Row " .. i .. " of the card") end
    ui.dialog()
  end
end
'''


def _cart(root, face, rows, buttons):
    name = "dlg_%s.moy" % face
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    main = "main.py" if face == "python" else "main.lua"
    if face == "python":
        src, bt = PY, repr(tuple(buttons))
    else:
        src, bt = LUA, "{%s}" % ", ".join('"%s"' % b for b in buttons)
    src = src.replace("OPEN", str(OPEN)).replace("ROWS", str(rows)).replace("BUTTONS", bt)
    with open(os.path.join(d, main), "w") as f:
        f.write(src)
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"format": "moy-1", "title": "Dlg " + face, "runtime": face, "main": main,
                   "canvas": "320x240", "extensions": ["moybyte.ui"],
                   "moybyte": {"type": "app", "permissions": []}}, f)
    with open(os.path.join(d, "config.json"), "w") as f:
        f.write("{}")


def _count(ws, face):
    p = ws.run.player
    return p.ns["F"][0] if face == "python" else p.lua.get_global("F")


def _differ(a, b, w):
    """The box (x0, y0, x1, y1) of the pixels where frames a and b differ."""
    xs, ys = [], []
    for i in range(0, len(a), 2):
        if a[i:i + 2] != b[i:i + 2]:
            ys.append(i // 2 // w)
            xs.append(i // 2 % w)
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1) if xs else None


@pytest.mark.parametrize("buttons", ((), ("No", "Yes")), ids=("bare", "buttons"))
@pytest.mark.parametrize("rows", (3, 30), ids=("whole", "cut"))
@pytest.mark.parametrize("face", ("python", "lua"))
def test_a_dialog_shows_its_card_on_its_first_frame(tmp_path, face, rows, buttons):
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    _cart(root, face, rows, buttons)
    ws = host_app.build_workstation(root)
    ws.pointer.visible = False
    open_cart(ws, "Dlg " + face)
    drv = host_app.ConsoleDriver(ws)
    shots = {}
    for _ in range(OPEN + 6):
        _frame(drv)
        assert ws.run.player.cart_error is None, ws.run.player.cart_error
        shots.setdefault(_count(ws, face), bytes(ws.run.canvas._buf))
    w, h = ws.run.canvas.w, ws.run.canvas.h
    ws.spine.exit()
    assert sorted(shots)[:OPEN + 1] == list(range(1, OPEN + 2)), sorted(shots)
    rest = shots[max(shots)]
    assert shots[OPEN - 1] != shots[OPEN], "the frame that opens it draws the card"
    assert shots[OPEN + 1] == rest
    d = _differ(shots[OPEN], rest, w)
    if not buttons:
        assert d is None, "the card's first frame is the frame at rest, but at %r" % (d,)
    else:
        # the first button's focus mark: one button's box and its ring, at
        # the card's foot, right of the surface's middle
        assert d is not None, "focus opens on its first button on the frame after"
        x0, y0, x1, y1 = d
        assert y1 - y0 <= 32 and x1 - x0 <= 80 and x0 > w // 2 and y0 > h // 2, d
