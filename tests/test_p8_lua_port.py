"""tools/p8_lua_port.py (#11/#67): the p8-Lua -> Lua 5.4 converter, the
map/flag extraction (incl. PICO-8's gfx-shared map rows), and -- when lupa and
the ported cart are present -- Celeste Classic actually playing under the Lua
runtime (title -> start -> walk -> jump, crash-free).

The ported cart in ports/ is dev/test material (CC BY-NC-SA, see
ports/README.md); the integration test skips cleanly when it is absent.
"""

import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from p8_lua_port import (  # noqa: E402
    p8_lua_to_lua54, full_map_rows, gff_hex)

CELESTE = os.path.join(ROOT, "ports", "celeste.moy")


# -- the dialect converter ----------------------------------------------------

def _conv(line):
    return p8_lua_to_lua54([line]).rstrip("\n")


def test_compound_assign_expands():
    assert _conv("x+=1") == "x = x + (1)"
    assert _conv("this.spd.y*=0.5") == "this.spd.y = this.spd.y * (0.5)"
    # p8 allows a space before the operator (celeste line 383)
    assert _conv("this.dash_effect_time -=1") == \
        "this.dash_effect_time = this.dash_effect_time - (1)"


def test_compound_assign_is_statement_bounded():
    # `freeze-=1 return end` -- the RHS must stop before the next statement
    out = _conv("if freeze>0 then freeze-=1 return end")
    assert out == "if freeze>0 then freeze = freeze - (1) return end"


def test_compound_assign_ignores_comparisons_and_strings():
    assert _conv("if a <= b then c() end") == "if a <= b then c() end"
    assert _conv('s = "a+=1"') == 's = "a+=1"'
    assert _conv("-- x+=1 in a comment") == "-- x+=1 in a comment"


def test_not_equals_becomes_tilde():
    assert _conv("if a != b then c() end") == "if a ~= b then c() end"
    assert _conv('s = "a != b"') == 's = "a != b"'


def test_oneline_if_gets_then_end():
    assert _conv("if (pause_player) return") == "if pause_player then return end"
    # a parenthesised condition followed by `then` is already legal -- unchanged
    assert _conv("if (a) then b() end") == "if (a) then b() end"
    # a continued multi-line condition is not a one-liner
    assert _conv("if (a) and") == "if (a) and"


def test_lifecycle_renamed_for_the_shim():
    out = p8_lua_to_lua54(["function _init()", "function _update()",
                           "function _draw()"])
    assert "function p8_init(" in out
    assert "function p8_update(" in out
    assert "function p8_draw(" in out


# -- map + flag extraction ----------------------------------------------------

def test_full_map_merges_gfx_shared_rows():
    # map rows 0-31 copy through; row 32 comes from gfx lines 64/65 with the
    # LOW-nibble-first byte order normalized to big-endian text.
    gfx = ["0" * 128] * 128
    gfx[64] = "12" + "0" * 126          # nibbles: low=1, high=2 -> byte 0x21
    gfx[65] = "ab" + "0" * 126          # -> byte 0xba (second half of the row)
    sections = {"map": ["25" + "0" * 254], "gfx": gfx}
    rows = full_map_rows(sections)
    assert len(rows) == 64 and all(len(r) == 256 for r in rows)
    assert rows[0][:2] == "25"          # __map__ verbatim
    assert rows[32][:2] == "21"         # gfx-shared, normalized
    assert rows[32][128:130] == "ba"    # second gfx line = cells 64..127


def test_gff_hex_pads_and_cleans():
    assert gff_hex({"gff": ["04"]}) == "04" + "0" * 510
    assert len(gff_hex({})) == 512


# -- Celeste Classic under the Lua runtime (lupa + the ported cart) -----------

def _celeste_ws(tmp_path):
    from runtime import host_app
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    shutil.copytree(CELESTE, os.path.join(carts, "celeste.moy"))
    ws.launcher.items = ws.carts_store.scan(ws.carts_root)
    ws.slim_carts()
    ws.launcher.sel = next(i for i, c in enumerate(ws.launcher.items)
                           if c["title"] == "Celeste Classic")
    ws.open()
    return ws


@pytest.mark.skipif(not os.path.isdir(CELESTE),
                    reason="ports/celeste.moy not generated")
def test_celeste_plays_under_the_lua_runtime(tmp_path):
    pytest.importorskip("lupa")
    ws = _celeste_ws(tmp_path)
    assert ws.player.cart_error is None
    g = ws.player._lua._lua.globals()
    inp = ws.input
    for _ in range(90):                                  # the title screen
        ws.frame(1 / 60)
        assert ws.player.cart_error is None
    inp.set_held("a", True)                              # jump = start game
    for _ in range(6):
        ws.frame(1 / 60)
    inp.set_held("a", False)
    probe = ws.player._lua._lua.eval("""
function()
  for i = 1, #objects do
    local o = objects[i]
    if o.type == player then return o.x, o.y end
  end
end
""")
    pos = None
    for _ in range(600):                                 # the spawn animation
        ws.frame(1 / 60)
        assert ws.player.cart_error is None
        pos = probe()
        if pos is not None:
            break
    assert (g.room.x, g.room.y) == (0, 0)                # level 1 loaded
    assert pos is not None, "no player object after the spawn animation"
    x0, y0 = pos
    inp.set_held("right", True)                          # walk right
    for _ in range(60):
        ws.frame(1 / 60)
    inp.set_held("right", False)
    x1, _ = probe()
    assert x1 > x0, "holding right did not move the player"
    inp.set_held("a", True)                              # jump
    for _ in range(6):
        ws.frame(1 / 60)
    inp.set_held("a", False)
    for _ in range(8):
        ws.frame(1 / 60)
    _, y2 = probe()
    assert y2 < y0, "jumping did not lift the player"
    assert ws.player.cart_error is None
    # p8 numbers are 16.16 fixed point: carts pass FLOAT colors/coords/ids and
    # PICO-8 floors them implicitly (celeste's strawberry "1000" popup crashed
    # the device with `float & int` before the shim floored at the boundary).
    ws.player._lua._lua.execute(
        'print("1000", 10.5, -0.5, 7.5) '
        'rectfill(1.5, 2.5, 20.7, 9.2, 8.9) '
        'circfill(30.5, 30.5, 2.9, 12.3) '
        'spr(1.7, 3.5, 4.5) pal(8.2, 14.9) pal() '
        'camera(-1.5, 0.5) camera()')
    ws.frame(1 / 60)
    assert ws.player.cart_error is None
