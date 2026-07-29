#!/usr/bin/env python3
"""Port a PICO-8 `.p8` cart to a `"runtime": "lua"` `.moy` cartridge (#11/#67).

`tools/import_p8.py` (the #36 guided-porting importer) converts the ASSETS and
leaves the code as a hand-port exercise -- written when Moybyte carts were
Python-only. With the #67 Lua cart runtime shipped, a PICO-8 cart's Lua can
instead very nearly RUN: this tool emits a complete Lua cart --

  main.lua =  [PICO-8 compat shim]  the p8 API written over the moy cart API
            + [__gff__ flag table]  fget/map-layer masks (import_p8 defers gff)
            + [full 128x64 map]     __map__ rows 0-31 PLUS the rows 32-63 that
                                    PICO-8 stores in the BOTTOM half of __gfx__
                                    (low-nibble-first bytes -- the shared-RAM
                                    quirk), normalized to big-endian hex here
            + [the cart's own Lua]  mechanically converted p8-Lua -> Lua 5.4
                                    (`!=` -> `~=`, `x += e` -> `x = x + (e)`,
                                    one-line `if (c) s` -> `if c then s end`,
                                    `_init/_update/_draw` renamed to `p8_*` so
                                    the shim can pace them at PICO-8's fixed
                                    30fps from moybyte's dt-driven loop)

plus sprites.moygfx / sounds.json via import_p8's converters and a lua-runtime
manifest. The 128x128 p8 screen renders centered in the 320x240 canvas (clip +
camera offset in the shim); p8 `sin`/`cos` turn-and-flip semantics, table verbs
(`add/del/foreach/all/count`), and flag-masked `map()` are implemented in the
shim over the ordinary cart verbs, so the port exercises the whole Lua bridge.

Usage:
    .venv/bin/python tools/p8_lua_port.py cart.p8 out_dir [--title "Name"]
                                         [--zoom [T,B]]

--zoom trades edge rows for size. A 128x128 p8 screen cannot fill 320x240 at an
integer scale (2x is 256x256, 16px too tall), so a port runs 1:1 in a letterbox
by default. Cropping 8 rows leaves a 128x120 viewport, which fits at 2x =
256x240 and fills the height. Bare --zoom crops 4 off each end; T,B places the
cut deliberately, because a p8 game's HUD lives at one edge or the other
(celeste draws its summit timer at y=4, so it survives 4 off the top, not 8).

The emitted cart is only as faithful as doubles-instead-of-16.16-fixed-point
allows (fine for most carts; Celeste Classic community ports do the same).
LICENSING NOTE: PICO-8 BBS carts default to CC BY-NC-SA 4.0 -- ported carts are
dev/test material unless the license says otherwise; keep them out of
system_carts/ and ship an attribution note next to the cart.
"""

import json
import re
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
for p in (_THIS_DIR, _REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from import_p8 import (  # noqa: E402  -- the #36 importer's converters, reused
    read_p8, _title_from, gfx_to_kgfx, icon_tile, sfx_music_to_sounds,
    music_start_map)


# --------------------------------------------------------------------------
# p8-Lua -> Lua 5.4 (the mechanical dialect transforms)
# --------------------------------------------------------------------------

_ASSIGN_OPS = ("+", "-", "*", "/", "%")
_LIFECYCLE = ("_init", "_update", "_update60", "_draw")


def _split_comment(code):
    """Split one line into (code, comment) honoring '--' only outside strings."""
    q = None
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "-" and i + 1 < n and code[i + 1] == "-":
            return code[:i], code[i:]
        i += 1
    return code, ""


def _find_outside_strings(code, sub, start=0):
    q = None
    i = start
    n = len(code)
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif code.startswith(sub, i):
            return i
        i += 1
    return -1


def _lvalue_start(code, opi):
    """Walk back from the operator over an lvalue: identifier chars, dots and
    balanced [...] index chains (`this.dash_target.y`, `got_fruit[1+n]`).
    p8 allows space before the operator (`this.dash_effect_time -=1`)."""
    j = opi
    while j > 0 and code[j - 1] in " \t":
        j -= 1
    depth = 0
    while j > 0:
        ch = code[j - 1]
        if ch == "]":
            depth += 1
        elif ch == "[":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and not (ch.isalnum() or ch in "._"):
            break
        j -= 1
    return j


def _rhs_end(code, start):
    """The statement boundary after a compound-assign RHS: end of code, or a
    depth-0 keyword that starts the NEXT statement (`freeze-=1 return end`)."""
    q = None
    depth = 0
    i = start
    n = len(code)
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch.isalpha():
            j = i
            while j < n and (code[j].isalnum() or code[j] == "_"):
                j += 1
            word = code[i:j]
            prev = code[i - 1] if i > 0 else " "
            if word in ("return", "end", "else", "elseif", "then") \
                    and not (prev.isalnum() or prev in "._"):
                return i
            i = j
            continue
        i += 1
    return n


def _expand_compound(code):
    """`X op= RHS` -> `X = X op (RHS)`, statement-bounded, outside strings."""
    for _ in range(8):                       # a line holds few of these
        found = None
        for op in _ASSIGN_OPS:
            i = _find_outside_strings(code, op + "=")
            # skip ==, <=, >=, ~= lookalikes: the char after must not be '='
            while i >= 0 and i + 2 <= len(code) and code[i + 2:i + 3] == "=":
                i = _find_outside_strings(code, op + "=", i + 2)
            if i >= 0 and (found is None or i < found[0]):
                found = (i, op)
        if found is None:
            return code
        i, op = found
        lo = _lvalue_start(code, i)
        lhs = code[lo:i].strip()
        if not lhs:                          # not actually an assignment
            return code
        re_ = _rhs_end(code, i + 2)
        rhs = code[i + 2:re_].strip()
        code = (code[:lo] + lhs + " = " + lhs + " " + op + " (" + rhs + ")"
                + (" " if re_ < len(code) else "") + code[re_:])
    return code


def _expand_oneline_if(code):
    """p8's `if (cond) stmt` one-liner -> `if cond then stmt end`."""
    stripped = code.lstrip()
    if not stripped.startswith("if"):
        return code
    after_if = stripped[2:]
    if not after_if.lstrip().startswith("("):
        return code
    indent = code[:len(code) - len(stripped)]
    p = code.index("(")
    depth = 0
    q = None
    i = p
    while i < len(code):
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        return code
    cond = code[p + 1:i]
    rest = code[i + 1:].strip()
    if not rest or rest.split(None, 1)[0] in ("then", "and", "or", "not") \
            or rest[0] in "+-*/%<>=~.([{":
        return code                          # a normal if / a continued condition
    return indent + "if " + cond + " then " + rest + " end"


def _rename_lifecycle(code):
    for name in _LIFECYCLE:
        code = code.replace("function " + name + "(", "function p8" + name + "(")
    return code


_EMPTY_MUSIC_STUB = re.compile(r"^\s*function\s+music\s*\([^)]*\)\s*end\s*$")


def p8_lua_to_lua54(lines):
    out = []
    for line in lines:
        line = line.replace("\t", "  ").rstrip()
        code, comment = _split_comment(line)
        if _EMPTY_MUSIC_STUB.match(code):
            # The cart silenced ITSELF with an empty music() override (the
            # celeste-maker mirror ships one). The port imports __music__ as
            # real tracks, so drop the stub and let the shim's music() play
            # them. A NON-empty override is real cart behaviour and is kept.
            out.append("-- [port] dropped the cart's empty music() stub "
                       "(imported __music__ plays instead)")
            continue
        i = _find_outside_strings(code, "!=")
        while i >= 0:
            code = code[:i] + "~=" + code[i + 2:]
            i = _find_outside_strings(code, "!=")
        code = _expand_oneline_if(code)
        code = _expand_compound(code)
        code = _rename_lifecycle(code)
        out.append(code + comment)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# __map__ + the __gfx__-shared bottom half -> 64 rows of big-endian hex
# --------------------------------------------------------------------------

def full_map_rows(sections):
    """The full 128x64 cell map as 64 strings of 256 hex chars (big-endian
    cell bytes). Rows 0-31 come from __map__ verbatim; rows 32-63 live in the
    BOTTOM half of __gfx__ (PICO-8 shared RAM), where the byte order is
    low-nibble-first -- normalized here so the Lua reader has ONE format."""
    map_lines = [ln.strip().lower() for ln in sections.get("map", [])]
    gfx_lines = [ln.strip().lower() for ln in sections.get("gfx", [])]
    rows = []
    for y in range(32):
        row = map_lines[y] if y < len(map_lines) else ""
        row = "".join(ch if ch in "0123456789abcdef" else "0" for ch in row)
        rows.append((row + "0" * 256)[:256])
    for k in range(32):
        cells = []
        for half in range(2):                # two 128-nibble gfx lines per map row
            gi = 64 + 2 * k + half
            g = gfx_lines[gi] if gi < len(gfx_lines) else ""
            g = "".join(ch if ch in "0123456789abcdef" else "0" for ch in g)
            g = (g + "0" * 128)[:128]
            for i in range(64):
                lo, hi = g[2 * i], g[2 * i + 1]   # low nibble is the LEFT pixel
                cells.append(hi + lo)             # -> big-endian text byte
        rows.append("".join(cells))
    return rows


def gff_hex(sections):
    """The 256 per-sprite flag bytes as one 512-char hex string."""
    text = "".join(ln.strip().lower() for ln in sections.get("gff", []))
    text = "".join(ch if ch in "0123456789abcdef" else "0" for ch in text)
    return (text + "0" * 512)[:512]


# --------------------------------------------------------------------------
# the PICO-8 compat shim (pure Lua over the ordinary moy cart verbs)
# --------------------------------------------------------------------------

SHIM = r'''-- ============================================================
-- PICO-8 compatibility shim (generated by tools/p8_lua_port.py)
-- The p8 API written over the moy cart API; the ported cart below
-- calls PICO-8 verbs and never knows it moved.
-- ============================================================
-- ZOOM (--zoom T,B): p8 rows are CROPPED off the top and bottom, shrinking the
-- logical viewport so a BIGGER integer scale fits. 128x128 cannot fill 320x240
-- at an integer scale (2x is 256x256, 16px too tall), so an uncropped port runs
-- 1:1 in a letterbox; drop 8 rows and 2x = 256x240 fills the height exactly.
-- Cropping is LOSSY and per-cart -- a p8 game drawing HUD at the very top or
-- bottom loses it -- so it is opt-in, and 0,0 (no crop) by default.
local P8_CROP_T, P8_CROP_B = __P8_CROP_T__, __P8_CROP_B__
local P8_VH = 128 - P8_CROP_T - P8_CROP_B
-- The scale is applied BY THIS CART, in the draw verbs below -- not asked of the
-- host. A cart that draws 2x is just a cart: it looks the same on the web
-- runner, an S3 and a P4, with no extension and no hardware scaler (the S3 has
-- no PPA). Asking the host to composite instead made the zoom appear only on
-- the one tier that implements `view`, which is how it shipped looking cropped
-- but not zoomed. The cost is fill rate -- 4x the pixels -- and that is the
-- accepted trade.
local P8_S = __P8_SCALE__
local P8_W, P8_H = 128 * P8_S, P8_VH * P8_S   -- the drawn size, in canvas pixels
local P8_OX, P8_OY = (320 - P8_W) // 2, (240 - P8_H) // 2
local P8_DY = P8_OY - P8_CROP_T * P8_S        -- where p8 row 0 lands (may be off-screen)
local P8_DT = 1 / 30               -- PICO-8 _update runs at a fixed 30fps
-- `view` is now only a LETTERBOX hint: the pixels are already the right size, so
-- a host that implements it just drops the side bars. Guarded, and nothing about
-- the cart's appearance depends on it any more.
if view ~= nil then view(P8_W, P8_H) end
do
  local m_spr, m_btn, m_btnp = spr, btn, btnp
  local m_camera = camera
  local m_rect, m_rectb = rect, rectb
  local m_circ, m_circb = circ, circb
  local m_print, m_sfx = print, sfx
  local m_music, m_music_stop = music, music_stop

  local BTN = {[0] = "left", [1] = "right", [2] = "up", [3] = "down",
               [4] = "a", [5] = "b"}
  -- btnp edges are latched across console frames (see _update below): a game
  -- tick runs only ~every other console frame and an engine press edge lasts
  -- ONE console frame, so an unlatched port would eat ~half of all presses.
  local pending = {}
  function btn(i) return m_btn(BTN[i] or "a") end
  function btnp(i) return pending[i] or m_btnp(BTN[i] or "a") end

  -- PICO-8 numbers are 16.16 fixed point and every API arg is implicitly
  -- FLOORED (p8 carts pass float colors/coords everywhere -- celeste's "1000"
  -- popup draws with color 7+flash%2). The moy engine takes integer indices,
  -- so this shim floors at the boundary.
  local mfloor = math.floor
  local function fl(v) return mfloor(v or 0) end

  local S = P8_S
  local function sc(v) return fl(v) * S end   -- p8 floors at the API edge,
                                              -- THEN each p8 pixel becomes SxS
  function camera(cx, cy) m_camera(sc(cx) - P8_OX, sc(cy) - P8_DY) end
  -- p8 math over the sandboxed Lua math lib (the moy api only registers
  -- rnd/flr; a python cart gets abs/min/max from python builtins, a lua cart
  -- gets them here). p8 angles are TURNS (0..1) and sin is flipped (+y down).
  function sin(t) return -math.sin((t or 0) * 6.283185307179586) end
  function cos(t) return math.cos((t or 0) * 6.283185307179586) end
  flr = math.floor
  abs = math.abs
  min = math.min
  max = math.max
  sqrt = math.sqrt
  function atan2(dx, dy) return math.atan(-(dy or 0), dx or 0) / 6.283185307179586 % 1 end

  function spr(n, x, y, w, h, fx, fy)
    local flip = (fx and 1 or 0) + (fy and 2 or 0)
    n = fl(n)
    x = fl(x)
    y = fl(y)
    w = w or 1
    h = h or 1
    if w == 1 and h == 1 then
      m_spr(n, x * S, y * S, 0, S, flip)       -- p8 color 0 is transparent
    else
      for ty = 0, h - 1 do
        for tx = 0, w - 1 do
          local cx = fx and (w - 1 - tx) or tx
          local cy = fy and (h - 1 - ty) or ty
          m_spr(n + cx + cy * 16, (x + tx * 8) * S, (y + ty * 8) * S, 0, S, flip)
        end
      end
    end
  end

  -- p8 rect/circ are OUTLINES and rectangles take the far corner
  function rectfill(x0, y0, x1, y1, c)
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rect(x0 * S, y0 * S, (x1 - x0 + 1) * S, (y1 - y0 + 1) * S, fl(c))
  end
  function rect(x0, y0, x1, y1, c)
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rectb(x0 * S, y0 * S, (x1 - x0 + 1) * S, (y1 - y0 + 1) * S, fl(c))
  end
  function circfill(x, y, r, c) m_circ(sc(x), sc(y), sc(r), fl(c)) end
  function circ(x, y, r, c) m_circb(sc(x), sc(y), sc(r), fl(c)) end
  -- SPEC.md 6: `print` has no scale, text is ALWAYS 8px. So a zoomed port
  -- positions its text at scale but draws the glyphs at 1x -- the one thing
  -- a 2x port cannot double.
  function print(s, x, y, c) m_print(s, sc(x), sc(y), c == nil and 7 or fl(c)) end

  local m_pal = pal
  function pal(a, b)
    if a == nil then m_pal() else m_pal(fl(a), fl(b)) end
  end
  local m_pix = pix
  function pset(x, y, c)                       -- one p8 pixel = an SxS block
    if S == 1 then m_pix(fl(x), fl(y), fl(c))
    else m_rect(sc(x), sc(y), S, S, fl(c)) end
  end
  function pget(x, y) return m_pix(sc(x), sc(y)) end
  local m_line = line
  function line(x0, y0, x1, y1, c)
    m_line(sc(x0), sc(y0), sc(x1), sc(y1), fl(c))   -- 1px thick at any scale
  end

  function sfx(n) if n and n >= 0 then m_sfx(fl(n)) end end
  function music(n)
    -- p8 music(n) takes a PATTERN index; __music_map (baked per cart) maps the
    -- song starts to Moybyte track ids, nearest-lower for a mid-song index.
    if n == -1 then m_music_stop()
    elseif n then
      local t = __music_map and __music_map[n]
      if t == nil and __music_map then
        for k = n, 0, -1 do
          if __music_map[k] ~= nil then t = __music_map[k] break end
        end
      end
      m_music(t or 0)
    end
  end
  function menuitem() end                      -- p8 pause menu: nothing to add to

  -- p8 table verbs. all() tolerates deleting the CURRENT item mid-loop
  -- (celeste's foreach(objects, ...) destroys objects while iterating).
  function add(t, v) t[#t + 1] = v return v end
  function del(t, v)
    for i = 1, #t do
      if t[i] == v then table.remove(t, i) return end
    end
  end
  function all(t)
    local i = 0
    local v
    return function()
      if t[i] == v then i = i + 1 end
      v = t[i]
      return v
    end
  end
  function foreach(t, f) for v in all(t) do f(v) end end
  function count(t) return #t end
  sub = string.sub
  tostr = tostring
  function sgn(x) if (x or 0) < 0 then return -1 end return 1 end
  function mid(a, b, c) return max(min(a, b), min(max(a, b), c)) end
  function rnd(n) return math.random() * (n or 1) end

  -- map + flags: the map DATA now ships as map.moymap (the console's own
  -- format -- editable, native-map()-able); build the fast Lua-side lookup
  -- from it ONCE at start via the console mget (captured before the p8 mget
  -- shadows it). __gff__ stays baked below the shim (flags have no moy home).
  local m_mget = mget
  __p8_map = {}
  for y = 0, 63 do
    local base = y * 128
    for x = 0, 127 do
      local v = m_mget(x, y)
      __p8_map[base + x + 1] = (v and v >= 0) and v or 0
    end
  end
  function mget(x, y)
    x = math.floor(x or 0)
    y = math.floor(y or 0)
    if x < 0 or x > 127 or y < 0 or y > 63 then return 0 end
    return __p8_map[y * 128 + x + 1]
  end
  function fget(n, f)
    local v = __p8_gff[math.floor(n or 0)] or 0
    if f == nil then return v end
    return (v >> f) & 1 == 1
  end
  function map(celx, cely, sx, sy, cw, ch, mask)
    celx = math.floor(celx or 0)
    cely = math.floor(cely or 0)
    sx = sx or 0
    sy = sy or 0
    for cy = 0, (ch or 16) - 1 do
      local rowb = (cely + cy) * 128
      for cx = 0, (cw or 16) - 1 do
        local tile = __p8_map[rowb + celx + cx + 1] or 0
        if tile > 0 and (mask == nil or mask == 0
                         or ((__p8_gff[tile] or 0) & mask) ~= 0) then
          m_spr(tile, (sx + cx * 8) * S, (sy + cy * 8) * S, 0, S, 0)
        end
      end
    end
  end

  -- moybyte lifecycle -> the p8 one, paced at PICO-8's fixed 30fps
  local m_clip = clip
  local ticked, phase = true, 0
  function _init()
    if p8_init then p8_init() end
  end
  local sdt = P8_DT / 2                          -- smoothed frame time
  function _update(dt)
    for i = 0, 5 do                              -- latch edges EVERY frame
      if m_btnp(BTN[i]) then pending[i] = true end
    end
    dt = dt or sdt
    if dt > 0.1 then dt = 0.1 end
    sdt = sdt + (dt - sdt) * 0.12
    -- FRAME-QUANTIZED cadence: tick every ceil(P8_DT/sdt) frames -- every
    -- frame on a governed ~30fps loop, every 2nd on a ~60fps loop. The game
    -- rate slaves to the loop rate (a few % slow at worst, NEVER fast) with
    -- perfectly even spacing. Real PICO-8 hosts run integer-locked loops;
    -- every wall-clock drift-correction scheme tried here instead produced
    -- the artifact it was meant to fix (double-ticks reading as speed-ups,
    -- alternating ticks reading as slow-mo). A transient loop dip just slows
    -- the game briefly, exactly like PICO-8 on weak hardware. The 0.94
    -- tolerance absorbs the governor's integer-ms period (33ms vs 33.33).
    local n = math.ceil(P8_DT * 0.94 / sdt)
    if n < 1 then n = 1 end
    phase = phase + 1
    if phase >= n then
      phase = 0
      if p8_update then p8_update() end
      for i = 0, 5 do pending[i] = false end     -- the tick consumed the edges
      ticked = true
    end
  end
  function _draw()
    if ticked and p8_draw then
      -- the console resets camera/clip/pal after every cart frame (so its own
      -- overlays are never offset) -- re-park the 128x128 window each draw
      camera()
      m_clip(P8_OX, P8_OY, P8_W, P8_H)
      p8_draw()
      ticked = false
    end
  end
end
-- ============================== end shim =============================
'''


def music_map_lua(sections):
    """The baked {p8_pattern_start: moy_track} table the shim's music() reads."""
    starts = music_start_map(sections.get("music", []))
    if not starts:
        return "__music_map = nil"
    pairs = ", ".join("[%d]=%d" % (k, v) for k, v in sorted(starts.items()))
    return "__music_map = {%s}" % pairs


def data_tables_lua(sections):
    lines = [music_map_lua(sections),
             "-- __gff__ (the map itself ships as map.moymap)",
             "__p8_gff = {}",
             "do",
             '  local gff = "' + gff_hex(sections) + '"',
             "  for i = 0, 255 do",
             "    __p8_gff[i] = tonumber(string.sub(gff, i * 2 + 1, i * 2 + 2), 16)",
             "  end",
             "end",
              ""]
    return "\n".join(lines)


def build_manifest(title, icon=None):
    man = {
        "format": "moy-1",
        "version": 1,
        "title": title,
        "type": "game",
        "runtime": "lua",
        "main": "main.lua",
        "fps": 30,
        "canvas": {"width": 320, "height": 240, "palette": "moy64"},
        "permissions": ["graphics", "input", "audio"],
        "input": ["buttons"],
        "config": {},
        "edit": [],
        "ported_from": "pico-8",
    }
    if icon is not None:
        # SPEC.md 3.4: the tiles a launcher shows the cart by. p8 has no icon
        # field, so this is the sheet's first non-blank tile (see icon_tile) --
        # without it a ported cart falls back to tile 0, which the p8 convention
        # leaves EMPTY, i.e. every ported cart would show a blank square.
        man["icon"] = [icon, 1, 1]
    return man


def port(p8_path, out_dir, title=None, crop=(0, 0)):
    sections = read_p8(p8_path)      # text .p8 OR the BBS .p8.png
    title = title or _title_from(sections, p8_path)
    os.makedirs(out_dir, exist_ok=True)

    body = p8_lua_to_lua54(sections.get("lua", []))
    header = ("-- %s -- ported from PICO-8 by tools/p8_lua_port.py (#11/#67).\n"
              "-- The shim + data tables are generated; the game code below them\n"
              "-- is the original cart's Lua, mechanically converted to Lua 5.4.\n"
              % title)
    vh = 128 - int(crop[0]) - int(crop[1])
    scale = max(1, min(320 // 128, 240 // vh)) if vh > 0 else 1
    shim = (SHIM.replace("__P8_CROP_T__", str(int(crop[0])))
                .replace("__P8_CROP_B__", str(int(crop[1])))
                .replace("__P8_SCALE__", str(scale)))
    main_lua = header + shim + "\n" + data_tables_lua(sections) + "\n" + body
    with open(os.path.join(out_dir, "main.lua"), "w", encoding="utf-8") as f:
        f.write(main_lua)

    # map.moymap -- the console's own tilemap format (cells store tile+1,
    # 0 = empty), so the map is REAL data other tools/editors/native map()
    # consume; the shim rebuilds its fast Lua table from it at start. The p8
    # convention maps exactly: cell 0 ("sprite 0", empty by convention) -> 0,
    # id N -> N+1. Id 255 can't be stored (+1 overflows the byte) -> empty.
    rows = full_map_rows(sections)
    if any(c != "0" for r in rows for c in r):
        out_rows = []
        for r in rows:
            cells = []
            for i in range(0, len(r), 2):
                v = int(r[i:i + 2], 16)
                cells.append("%02x" % (0 if v == 255 else (v + 1) & 0xFF if v else 0))
            out_rows.append("".join(cells))
        with open(os.path.join(out_dir, "map.moymap"), "w", encoding="utf-8") as f:
            f.write("128 64\n" + "\n".join(out_rows) + "\n")

    kgfx = gfx_to_kgfx(sections.get("gfx", []))
    if kgfx:
        with open(os.path.join(out_dir, "sprites.moygfx"), "w", encoding="utf-8") as f:
            f.write(kgfx)

    sounds, _n_sfx, _n_music = sfx_music_to_sounds(
        sections.get("sfx", []), sections.get("music", []))
    if sounds:
        with open(os.path.join(out_dir, "sounds.json"), "w", encoding="utf-8") as f:
            json.dump(sounds, f)

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(build_manifest(title, icon_tile(kgfx)), f, indent=2)
        f.write("\n")
    return out_dir


def parse_zoom(argv):
    """--zoom [T,B] -> (top, bottom) p8 rows to crop, or (0, 0) when absent.

    Bare --zoom means 4,4: the symmetric crop that leaves a 128x120 viewport,
    the largest that still fits 320x240 at 2x (256x240, filling the height).
    An explicit T,B lets a cart protect the edge its HUD lives on -- celeste
    draws its summit timer at y=4, so it survives 4 off the top but not 8."""
    if "--zoom" not in argv:
        return (0, 0)
    i = argv.index("--zoom")
    spec = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else ""
    if "," not in spec:
        return (4, 4)
    t, b = spec.split(",", 1)
    return (max(0, int(t)), max(0, int(b)))


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    title = None
    if "--title" in argv:
        title = argv[argv.index("--title") + 1]
        args = [a for a in args if a != title]
    crop = parse_zoom(argv)
    if crop != (0, 0):
        args = [a for a in args if "," not in a or not a.replace(",", "").isdigit()]
    if len(args) != 2:
        print("usage: p8_lua_port.py cart.p8 out_dir [--title NAME] [--zoom [T,B]]")
        return 2
    out = port(args[0], args[1], title, crop)
    vh = 128 - crop[0] - crop[1]
    print("ported ->", out)
    if crop != (0, 0):
        scale = max(1, min(320 // 128, 240 // vh)) if vh else 1
        print("  zoom: cropped %d/%d rows -> the cart DRAWS at %dx = %dx%d "
              "(text stays 8px -- SPEC.md 6)"
              % (crop[0], crop[1], scale, 128 * scale, vh * scale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
