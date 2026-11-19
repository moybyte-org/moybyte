"""A popover opened between frames shows its card on the first frame that
draws it, as it shows it at rest (native/moy_ui/moy_ui_api_pop.c,
moy_ui_api_popover): no card is kept for it yet, so its first frame draws it
as tall as the surface lets it, lays its body out in that room and moves it
to its place once the body closes. The engine is one body under every
face, so a Python app's `ui.popover` and a Lua app's draw it the same.

Each app draws a page of words and, from its OPEN-th frame, a popover hung
from a rect near the page's top (the card hangs below it) or its foot (above
it), with a few rows or more than the room holds (cut, a fade at its foot).
Nothing on the page or in the card is a stop, so no focus mark moves in on
the frame after: the frame that opens it is the frame at rest, pixel for
pixel, and the one before it shows no card.
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
    x, y, w, h = ui.page("Pop")
    F[0] += 1
    for i in range(6):
        ui.label("The page's line %d" % i)
    if F[0] >= OPEN:
        ui.at(x + 24, y + AT, 120, 18)
        if not ui.popover("pop", 170):
            F[0] = 0
        for i in range(ROWS):
            ui.label("Row %d of the card" % i)
        ui.popover()
'''

LUA = '''F = 0
function _draw()
  local x, y, w, h = ui.page("Pop")
  F = F + 1
  for i = 0, 5 do ui.label("The page's line " .. i) end
  if F >= OPEN then
    ui.at(x + 24, y + AT, 120, 18)
    if not ui.popover("pop", 170) then F = 0 end
    for i = 0, ROWS - 1 do ui.label("Row " .. i .. " of the card") end
    ui.popover()
  end
end
'''


def _cart(root, face, at, rows):
    name = "pop_%s.moy" % face
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    main = "main.py" if face == "python" else "main.lua"
    src = (PY if face == "python" else LUA).replace("OPEN", str(OPEN)).replace(
        "ROWS", str(rows)).replace("AT", str(at))
    with open(os.path.join(d, main), "w") as f:
        f.write(src)
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"format": "moy-1", "title": "Pop " + face, "runtime": face, "main": main,
                   "canvas": "320x240", "extensions": ["moybyte.ui"],
                   "moybyte": {"type": "app", "permissions": []}}, f)
    with open(os.path.join(d, "config.json"), "w") as f:
        f.write("{}")


def _count(ws, face):
    p = ws.run.player
    return p.ns["F"][0] if face == "python" else p.lua.get_global("F")


@pytest.mark.parametrize("rows", (3, 30), ids=("whole", "cut"))
@pytest.mark.parametrize("at", (10, 150), ids=("below", "above"))
@pytest.mark.parametrize("face", ("python", "lua"))
def test_a_popover_shows_its_card_on_its_first_frame(tmp_path, face, at, rows):
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    _cart(root, face, at, rows)
    ws = host_app.build_workstation(root)
    ws.pointer.visible = False
    open_cart(ws, "Pop " + face)
    drv = host_app.ConsoleDriver(ws)
    shots = {}
    for _ in range(OPEN + 6):
        _frame(drv)
        assert ws.run.player.cart_error is None, ws.run.player.cart_error
        shots.setdefault(_count(ws, face), bytes(ws.run.canvas._buf))
    ws.spine.exit()
    assert sorted(shots)[:OPEN + 1] == list(range(1, OPEN + 2)), sorted(shots)
    rest = shots[max(shots)]
    drew = shots[OPEN - 1] != shots[OPEN]
    first = shots[OPEN] == rest
    second = shots[OPEN + 1] == rest
    assert drew, "the frame that opens it draws the card"
    assert first, "the card's first frame is the frame at rest"
    assert second
