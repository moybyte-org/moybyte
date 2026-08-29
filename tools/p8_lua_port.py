#!/usr/bin/env python3
"""Port a PICO-8 `.p8` cart to a `"runtime": "lua"` `.moy` cartridge.

`p8_import.py` beside this file converts the ASSETS. Because a moy cart is Lua
(SPEC.md 4), a PICO-8 cart's own code can very nearly RUN too, so this tool
emits a complete Lua cart --

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
                                    30fps from moy's dt-driven loop)

plus sprites.moygfx / sounds.json via import_p8's converters and a lua-runtime
manifest that declares `"canvas": "128x128"` (SPEC.md 1/3.1) -- the cart draws
REAL p8 pixels 1:1 on its own raster and the HOST owns all scaling (the old
draw-2x-yourself shim is gone; a native-res cart fills a quarter of the pixels).
Text is the PICO-8 system font itself (3x5, 4px advance -- Lexaloffle, CC0),
drawn by the shim through pix(), because SPEC.md 6's 8px `print` glyphs are
twice the size p8 meant on a 128px raster. p8 `sin`/`cos` turn-and-flip
semantics, table verbs (`add/del/foreach/all/count`), and flag-masked `map()`
are implemented in the shim over the ordinary cart verbs, so the port exercises
the whole Lua bridge.

Usage:
    python3 p8_lua_port.py cart.p8 out_dir [--title "Name"]

The emitted cart is only as faithful as doubles-instead-of-16.16-fixed-point
allows (fine for most carts; Celeste Classic community ports do the same).
LICENSING NOTE: PICO-8 BBS carts default to CC BY-NC-SA 4.0 -- ported carts are
dev/test material unless the license says otherwise; keep them out of
system_carts/ and ship an attribution note next to the cart. The manifest says
so too (`safe_to_share: false`), so a host's share paths never have to infer it.

THIS FILE RUNS ON MICROPYTHON, and that is a CONSTRAINT rather than an
observation: a console can port a dropped cart in the browser (or on a board) by
running THIS code inside its own VM, which is the only arrangement in which the
desktop and the console cannot disagree about what a `.p8` becomes. So: no
`os.path`, no `os.makedirs`, no f-strings, no `json` `indent=`, no `str.isalnum`,
and NOTHING in `re` beyond MicroPython's subset -- no lookarounds, no `(?:...)`,
no inline `(?m)` flags, which its engine rejects at COMPILE time with "regex too
complex". `port_sections()` exists for the same reason: a caller that was handed
the bytes should not have to find them on a filesystem again.
"""

import json
import re
import os
import sys

from p8_import import (  # the asset converters, vendored beside this file
    read_p8, _title_from, gfx_to_kgfx, icon_tile, sfx_music_to_sounds,
    music_start_map)


# --------------------------------------------------------------------------
# p8-Lua -> Lua 5.4 (the mechanical dialect transforms)
# --------------------------------------------------------------------------

_ASSIGN_OPS = ("+", "-", "*", "/", "%")
_LIFECYCLE = ("_init", "_update", "_update60", "_draw")


def _isword(ch):
    """`ch.isalnum()`, spelled for a stdlib that does not have it.

    MicroPython's `str` carries isalpha/isdigit and no isalnum, and this file
    runs there (see the module header). Identifier chars are the only thing
    isalnum was ever asked here, so the two agree on every input this sees."""
    return ch.isalpha() or ch.isdigit()


def _ident_char(ch):
    return ch == "_" or _isword(ch)


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
        elif depth == 0 and not (_isword(ch) or ch in "._"):
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
            while j < n and _ident_char(code[j]):
                j += 1
            word = code[i:j]
            prev = code[i - 1] if i > 0 else " "
            if word in ("return", "end", "else", "elseif", "then") \
                    and not (_isword(prev) or prev in "._"):
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
-- NATIVE RES: the manifest declares "canvas": "128x128" (SPEC.md 1/3.1), so
-- this cart draws REAL p8 pixels 1:1 and the HOST owns all scaling. The old
-- shim drew 2x itself, because asking the host to composite made the zoom
-- appear only on the one tier that implemented `view` -- with the canvas in
-- the manifest every conforming host sizes the raster instead, and the cart
-- fills a QUARTER of the pixels it used to (the biggest speed lever a port
-- has on an interpreter-bound board).
-- ZOOM (--zoom): nothing is cropped from the raster any more -- all 128 rows
-- draw, and pset/pget/camera stay p8-true. The flag became the `view` hint
-- below: a host with room composites the CENTERED 128x120 at the biggest
-- integer scale that fits (2x fills a 4:3 screen's 240px height exactly; 5x
-- fills 600px), and one that presents pixel-for-pixel draws it unscaled. view
-- is core (SPEC.md 6) and cannot mislead a cart, so no guard -- lossy only at
-- PRESENTATION.
local P8_VH = __P8_VH__
local P8_DT = 1 / 30               -- PICO-8 _update runs at a fixed 30fps
if P8_VH < 128 then view(128, P8_VH) end
do
  local m_spr, m_btn, m_btnp = spr, btn, btnp
  local m_camera = camera
  local m_rect, m_rectb = rect, rectb
  local m_circ, m_circb = circ, circb
  local m_sfx = sfx
  local m_music, m_music_stop = music, music_stop
  -- The data tables (emitted ABOVE the shim) and the stdlib verbs, captured
  -- once as upvalues: fget hits __p8_gff on every collision probe and map()
  -- on every tile, where a global read is a hash lookup in _ENV per call --
  -- on a 240MHz interpreter those lookups were measurable frame time.
  local gff, mm = __p8_gff, __music_map
  local msin, mcos, matan, mrandom = math.sin, math.cos, math.atan, math.random
  local tremove = table.remove

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

  function camera(cx, cy) m_camera(fl(cx), fl(cy)) end
  -- p8 math over the sandboxed Lua math lib (the moy api only registers
  -- rnd/flr; a python cart gets abs/min/max from python builtins, a lua cart
  -- gets them here). p8 angles are TURNS (0..1) and sin is flipped (+y down).
  function sin(t) return -msin((t or 0) * 6.283185307179586) end
  function cos(t) return mcos((t or 0) * 6.283185307179586) end
  flr = math.floor
  abs = math.abs
  min = math.min
  max = math.max
  sqrt = math.sqrt
  function atan2(dx, dy) return matan(-(dy or 0), dx or 0) / 6.283185307179586 % 1 end

  function spr(n, x, y, w, h, fx, fy)
    local flip = (fx and 1 or 0) + (fy and 2 or 0)
    n = fl(n)
    x = fl(x)
    y = fl(y)
    w = w or 1
    h = h or 1
    if w == 1 and h == 1 then
      m_spr(n, x, y, 0, 1, flip)               -- p8 color 0 is transparent
    else
      for ty = 0, h - 1 do
        for tx = 0, w - 1 do
          local cx = fx and (w - 1 - tx) or tx
          local cy = fy and (h - 1 - ty) or ty
          m_spr(n + cx + cy * 16, x + tx * 8, y + ty * 8, 0, 1, flip)
        end
      end
    end
  end

  -- p8 rect/circ are OUTLINES and rectangles take the far corner
  function rectfill(x0, y0, x1, y1, c)
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1, fl(c))
  end
  function rect(x0, y0, x1, y1, c)
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rectb(x0, y0, x1 - x0 + 1, y1 - y0 + 1, fl(c))
  end
  function circfill(x, y, r, c) m_circ(fl(x), fl(y), fl(r), fl(c)) end
  function circ(x, y, r, c) m_circb(fl(x), fl(y), fl(r), fl(c)) end
  -- SPEC.md 6 gives `print` fixed 8px glyphs -- TWICE the size p8 meant on a
  -- native 128px raster, and celeste's memorial letters its text at the p8
  -- 4px advance (8px glyphs smear into each other). So the port carries the
  -- PICO-8 system font ITSELF (Lexaloffle, released CC0; bitmaps read from
  -- fake-08's 0x5600 memory dump): chars 32..127 as 15-bit 3x5 glyphs at the
  -- true 4px advance / 6px line height, drawn through pix() -- pure spec
  -- verbs, portable to any conforming host.
  local m_pix = pix
  local P8_GLYPHS = {}
  do
    local hex = "00002092002d5f7d2f9f52a57adb000a224a292255d505d0140001c020001494"
             .. "7b6f749373e779a749ed79cf7bc949277bef49ef0410141044540e38151121a7"
             .. "636a5f787ad872783b5872f812f87a785f6874b834b85ae872485bf85b583b70"
             .. "1f7867505778387024b86b682f687f685aa879e87338324b44916926002a7000"
             .. "00225bef7aef624e7b6b72cf12cf7a4e5bed749734975aed72495b7f5b6b3b6e"
             .. "13ef676a5aef39ce24976b6d2f6d7f6d5aad79ed72a764d62492359303e07b50"
    for i = 0, 95 do
      P8_GLYPHS[i + 32] = tonumber(string.sub(hex, i * 4 + 1, i * 4 + 4), 16)
    end
  end
  local sbyte = string.byte
  function print(s, x, y, c)
    s = tostring(s)
    c = c == nil and 7 or fl(c)
    local lx = fl(x)
    local cx, cy = lx, fl(y)
    for i = 1, #s do
      local b = sbyte(s, i)
      if b == 10 then
        cx, cy = lx, cy + 6
      else
        local g = P8_GLYPHS[b]
        if g and g ~= 0 then
          for p = 0, 14 do
            if (g >> p) & 1 == 1 then m_pix(cx + p % 3, cy + p // 3, c) end
          end
        end
        cx = cx + 4
      end
    end
  end

  local m_pal = pal
  function pal(a, b)
    if a == nil then m_pal() else m_pal(fl(a), fl(b)) end
  end
  function pset(x, y, c) m_pix(fl(x), fl(y), fl(c)) end
  function pget(x, y) return m_pix(fl(x), fl(y)) end
  local m_line = line
  function line(x0, y0, x1, y1, c)
    m_line(fl(x0), fl(y0), fl(x1), fl(y1), fl(c))
  end

  function sfx(n) if n and n >= 0 then m_sfx(fl(n)) end end
  function music(n)
    -- p8 music(n) takes a PATTERN index; __music_map (baked per cart) maps the
    -- song starts to Moybyte track ids, nearest-lower for a mid-song index.
    if n == -1 then m_music_stop()
    elseif n then
      local t = mm and mm[n]
      if t == nil and mm then
        for k = n, 0, -1 do
          if mm[k] ~= nil then t = mm[k] break end
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
      if t[i] == v then tremove(t, i) return end
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
  function rnd(n) return mrandom() * (n or 1) end

  -- map + flags: the map DATA now ships as map.moymap (the console's own
  -- format -- editable, native-map()-able); build the fast Lua-side lookup
  -- from it ONCE at start via the console mget (captured before the p8 mget
  -- shadows it). __gff__ stays baked below the shim (flags have no moy home).
  local m_mget = mget
  local p8map = {}
  __p8_map = p8map                     -- the global name stays for tooling
  for y = 0, 63 do
    local base = y * 128
    for x = 0, 127 do
      local v = m_mget(x, y)
      p8map[base + x + 1] = (v and v >= 0) and v or 0
    end
  end
  function mget(x, y)
    x = mfloor(x or 0)
    y = mfloor(y or 0)
    if x < 0 or x > 127 or y < 0 or y > 63 then return 0 end
    return p8map[y * 128 + x + 1]
  end
  function fget(n, f)
    local v = gff[mfloor(n or 0)] or 0
    if f == nil then return v end
    return (v >> f) & 1 == 1
  end
  -- Flag-masked map: ONE native call when the host offers the C walk
  -- (moybyte's __moy_map_masked, #66 M0 -- the flags crossed once in the
  -- __gff__ block above; the quads ride the same batch the spr fast path
  -- stamps), else the Lua cell loop. The loop was 4.5ms of celeste's render
  -- on the interpreter-bound boards, measured by difference. Args are
  -- floored up front (p8 floors every API arg), so both lanes agree.
  local native_map = __moy_map_masked
  function map(celx, cely, sx, sy, cw, ch, mask)
    celx = mfloor(celx or 0)
    cely = mfloor(cely or 0)
    sx = mfloor(sx or 0)
    sy = mfloor(sy or 0)
    cw = mfloor(cw or 16)
    ch = mfloor(ch or 16)
    mask = mask or 0
    if native_map ~= nil
        and native_map(celx, cely, sx, sy, cw, ch, mask) then
      return
    end
    for cy = 0, ch - 1 do
      local rowb = (cely + cy) * 128
      for cx = 0, cw - 1 do
        local tile = p8map[rowb + celx + cx + 1] or 0
        if tile > 0 and (mask == 0
                         or ((gff[tile] or 0) & mask) ~= 0) then
          m_spr(tile, sx + cx * 8, sy + cy * 8, 0, 1, 0)
        end
      end
    end
  end

  -- moybyte lifecycle -> the p8 one, paced at PICO-8's fixed 30fps
  local ticked = true
  function _init()
    if p8_init then p8_init() end
  end
  -- WALL-CLOCK cadence: p8_update runs 30x per real second whatever rate the
  -- host calls _update at, and a host too slow to draw that often loses DRAWS,
  -- not game speed. That is SPEC.md 5's one sanctioned degradation ("skip
  -- _draw while continuing to call _update at the full rate"), applied from
  -- inside the cart because the shim cannot verify the host is doing it.
  --
  -- On a host that does pace to 30 (both reference players do), dt is 1/30 and
  -- this ticks exactly once per call -- the same frame-for-frame behaviour a
  -- quantized cadence gave, reached without assuming the pacing.
  --
  -- The cost, and it is real: where the host rate is not a multiple of 30, the
  -- ticks land on ITS frame grid, so their spacing alternates (at 45fps, gaps
  -- of 22 and 44ms). That is arithmetic, not a scheme to tune away -- a 45fps
  -- host cannot place 30 evenly spaced ticks per second. Even spacing at the
  -- wrong rate is what this replaced: it ran a cart at 75% speed there, and
  -- 53% at 32fps.
  local EPS = P8_DT * 0.02      -- absorbs an integer-ms host period (33 vs 33.33)
  local MAX_CATCHUP = 4         -- past this the board genuinely cannot keep up
  local acc = 0
  function _update(dt)
    for i = 0, 5 do                              -- latch edges EVERY frame
      if m_btnp(BTN[i]) then pending[i] = true end
    end
    dt = dt or P8_DT
    if dt > 0.25 then dt = 0.25 end              -- a stall is a pause, not debt
    acc = acc + dt
    local n = 0
    while acc >= P8_DT - EPS and n < MAX_CATCHUP do
      acc = acc - P8_DT
      n = n + 1
      if p8_update then p8_update() end
      for i = 0, 5 do pending[i] = false end     -- the tick consumed the edges
      ticked = true
    end
    if n >= MAX_CATCHUP then acc = 0 end         -- write off what cannot be paid
  end
  function _draw()
    if ticked and p8_draw then
      -- the console resets camera/clip/pal after every cart frame; re-park the
      -- p8 camera so a cart that trusts persistent camera state draws at origin
      camera()
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
             "  -- hosts with the native masked-map walk take the flags ONCE",
             "  -- (moybyte's __moy_map_flags, #66 M0); the Lua table above",
             "  -- stays -- fget reads it either way.",
             "  if __moy_map_flags ~= nil then __moy_map_flags(gff) end",
             "end",
              ""]
    return "\n".join(lines)


# Every global the shim defines for the game code. Order is cosmetic; the
# emitted block groups them a line at a time.
P8_API = ("btn btnp camera sin cos flr abs min max sqrt atan2 spr rectfill "
          "rect circfill circ print pal pset pget line sfx music menuitem "
          "add del all foreach count sub tostr sgn mid rnd mget fget map").split()


def _defines_function(body, name):
    """`function NAME(` anywhere in `body`, at a keyword/identifier boundary."""
    i = 0
    n = len(body)
    while True:
        j = body.find("function", i)
        if j < 0:
            return False
        i = j + 8
        if j > 0 and (_ident_char(body[j - 1]) or body[j - 1] in ".:"):
            continue                      # the tail of a longer word
        k = j + 8
        while k < n and body[k] in " \t\r\n":
            k += 1
        if k == j + 8:                    # `function` and the name must part
            continue
        if not body.startswith(name, k):
            continue
        k += len(name)
        if k < n and _ident_char(body[k]):
            continue                      # a LONGER name that starts with ours
        while k < n and body[k] in " \t\r\n":
            k += 1
        if body[k:k + 1] == "(":
            return True


def _assigns_global(body, name):
    """`NAME =` (not `==`) or `NAME,` where NAME opens a line."""
    n = len(body)
    ln = len(name)
    i = 0
    while True:
        j = body.find(name, i)
        if j < 0:
            return False
        i = j + ln
        k = j
        while k > 0 and body[k - 1] in " \t":
            k -= 1
        if k != 0 and body[k - 1] != "\n":
            continue                      # something else opened this line
        e = j + ln
        if e < n and _ident_char(body[e]):
            continue                      # a LONGER name that starts with ours
        while e < n and body[e] in " \t\r\n":
            e += 1
        if body[e:e + 1] == ",":
            return True
        if body[e:e + 1] == "=" and body[e + 1:e + 2] != "=":
            return True


def localization_lua(body):
    """`local NAME = NAME` aliases at file scope, between shim and game code.

    Game functions compiled after this block bind the p8 API as UPVALUES (a
    slot read) instead of _ENV lookups (a table hash on EVERY call) -- on the
    interpreter-bound boards the hashes were measurable frame time (#67).
    A name the cart itself reassigns at global scope stays global: an alias
    would freeze the pre-assignment value for every later caller. The scan is
    conservative -- a false positive only loses that one name's speedup.

    The two predicates are hand-walked rather than regexes because both of the
    regexes they replaced needed constructs MicroPython's `re` refuses to
    COMPILE -- a lookbehind, a non-capturing group, an inline `(?m)` and a
    negative lookahead -- and this file has to run there (module header)."""
    keep = []
    for name in P8_API:
        if _defines_function(body, name):
            continue                      # cart defines its own
        if _assigns_global(body, name):
            continue                      # cart assigns the global
        keep.append(name)
    if not keep:
        return ""
    lines = ["-- Localized p8 API (generated -- see localization_lua): the game",
             "-- code below binds these as upvalues, not per-call _ENV lookups."]
    for i in range(0, len(keep), 8):
        chunk = ", ".join(keep[i:i + 8])
        lines.append("local %s = %s" % (chunk, chunk))
    return "\n".join(lines) + "\n"


def build_manifest(title, icon=None):
    # The spec manifest (SPEC.md 3.1). fps 30 because that IS PICO-8's rate --
    # the shim paces the p8 lifecycle at a fixed 1/30 dt. "ported_from" is an
    # unrecognised field; the spec requires hosts to ignore it (3.1).
    man = {
        "format": "moy-1",
        "title": title,
        "version": 1,
        "main": "main.lua",
        "fps": 30,
        # SPEC.md 1/3.1: the p8 screen IS the raster. The cart draws native
        # 128x128 pixels and the host scales/letterboxes -- a quarter of the
        # fill the old draw-2x-yourself shim paid.
        "canvas": "128x128",
        "input": ["buttons"],
        # A ported cart is SOMEBODY ELSE'S cart. PICO-8 BBS carts default to
        # CC BY-NC-SA 4.0 (module header), so playing and studying one is fine
        # and republishing it is not -- stated in the manifest so a host's share
        # paths read the answer instead of inferring it from `ported_from`.
        "safe_to_share": False,
        "ported_from": "pico-8",
    }
    if icon is not None:
        # SPEC.md 3.4: the tiles a launcher shows the cart by. p8 has no icon
        # field, so this is the sheet's first non-blank tile (see icon_tile) --
        # without it a ported cart falls back to tile 0, which the p8 convention
        # leaves EMPTY, i.e. every ported cart would show a blank square.
        man["icon"] = [icon, 1, 1]
    return man


# The order a manifest's fields are WRITTEN in. Declared rather than taken from
# the dict because MicroPython's dicts are not insertion-ordered: without this
# the same cart ported on two tiers differs by field order alone, which is both
# an unreadable diff and the end of any byte-for-byte check between them.
MANIFEST_KEYS = ("format", "title", "version", "main", "fps", "canvas",
                 "input", "safe_to_share", "ported_from", "icon")


def manifest_text(man):
    """`json.dump(man, indent=2)` for a `json` with no `indent=` (the header).

    A LAYOUT, not an encoder: every value still goes through `json.dumps`. The
    manifest is one flat object of scalars and scalar lists, which is the only
    shape this lays out -- anything nested would need a real pretty-printer, and
    the answer to that is to not put it in a manifest."""
    out = []
    for key in MANIFEST_KEYS:
        if key not in man:
            continue
        v = man[key]
        if isinstance(v, list):
            body = ("[\n" + ",\n".join("    " + json.dumps(x) for x in v)
                    + "\n  ]")
        else:
            body = json.dumps(v)
        out.append("  " + json.dumps(key) + ": " + body)
    return "{\n" + ",\n".join(out) + "\n}\n"


def _mkdirs(path):
    """`os.makedirs(path, exist_ok=True)` for an `os` that stops at mkdir."""
    at = "/" if path.startswith("/") else ""
    for seg in path.replace("\\", "/").split("/"):
        if not seg:
            continue
        at = at + seg if at in ("", "/") else at + "/" + seg
        try:
            os.mkdir(at)
        except OSError:
            pass                 # exists, or a parent we cannot make -- the
                                 # write below is what actually reports


def _write(out_dir, name, text):
    # `encoding=` is load-bearing on CPython (a non-UTF-8 locale would mangle an
    # accented title) and simply ignored by MicroPython's `open`.
    f = open(out_dir + "/" + name, "w", encoding="utf-8")
    try:
        f.write(text)
    finally:
        f.close()


def port_sections(sections, out_dir, title, crop=(0, 0)):
    """Already-parsed `sections` -> a `.moy` folder at `out_dir`.

    Returns the facts only the writer knows: `{"files": [...], "sfx": n,
    "music": m}`. Takes SECTIONS rather than a path because a console was handed
    the dropped bytes and has already had to parse them to decide the file was a
    cart at all -- reading it a second time here is 40ms of a `.p8.png` inflate
    spent to learn nothing."""
    _mkdirs(out_dir)
    written = []

    body = p8_lua_to_lua54(sections.get("lua", []))
    header = ("-- %s -- ported from PICO-8 by tools/p8_lua_port.py (#11/#67).\n"
              "-- The data tables + shim are generated; the game code below them\n"
              "-- is the original cart's Lua, mechanically converted to Lua 5.4.\n"
              % title)
    vh = 128 - int(crop[0]) - int(crop[1])
    if vh not in (120, 128):
        # The host's view crop shows the CENTERED 128x120 (SPEC.md 6) -- the
        # only crop a native-res port can ask for is 8 rows or none.
        raise SystemExit("--zoom: the view crop is 8 rows (128x120) or nothing"
                         " -- T+B must be 8 or 0, got %d,%d" % tuple(crop))
    shim = SHIM.replace("__P8_VH__", str(vh))
    # Data tables BEFORE the shim, so the shim captures them as upvalues.
    main_lua = (header + data_tables_lua(sections) + "\n" + shim + "\n"
                + localization_lua(body) + body)
    _write(out_dir, "main.lua", main_lua)
    written.append("main.lua")

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
        _write(out_dir, "map.moymap", "128 64\n" + "\n".join(out_rows) + "\n")
        written.append("map.moymap")

    kgfx = gfx_to_kgfx(sections.get("gfx", []))
    if kgfx:
        _write(out_dir, "sprites.moygfx", kgfx)
        written.append("sprites.moygfx")

    sounds, n_sfx, n_music = sfx_music_to_sounds(
        sections.get("sfx", []), sections.get("music", []))
    if sounds:
        _write(out_dir, "sounds.json", json.dumps(sounds))
        written.append("sounds.json")
    else:
        n_sfx = n_music = 0          # nothing was written, so nothing counted

    _write(out_dir, "manifest.json",
           manifest_text(build_manifest(title, icon_tile(kgfx))))
    written.append("manifest.json")
    return {"files": sorted(written), "sfx": n_sfx, "music": n_music}


def port(p8_path, out_dir, title=None, crop=(0, 0)):
    sections = read_p8(p8_path)      # text .p8 OR the BBS .p8.png
    title = title or _title_from(sections, p8_path)
    port_sections(sections, out_dir, title, crop)
    return out_dir


def parse_zoom(argv):
    """--zoom [T,B] -> (top, bottom) p8 rows the port can spare, or (0, 0).

    Bare --zoom means 4,4: the 8-row concession that lets a 4:3 host fill its
    height (view(128, 120) composites at 2x = 256x240 there). Nothing is
    cropped from the RASTER any more -- the rows only leave the picture on
    hosts that exploit the SPEC.md 6 view hint, and that crop is CENTERED,
    so T,B survives as CLI shape only; 8,0-style edge protection no longer
    maps and port() refuses any split that isn't 8 rows total (or none)."""
    if "--zoom" not in argv:
        return (0, 0)
    i = argv.index("--zoom")
    nxt = argv[i + 1] if i + 1 < len(argv) else ""
    # Only a literal T,B counts as the spec. Anything else after --zoom is
    # somebody else's argument -- `port --zoom cart.p8` puts the cart there --
    # and consuming it because it happened to follow the flag is how a stray
    # token ends up interpreted as something it is not.
    if not re.match(r"^\d+,\d+$", nxt):
        return (4, 4)
    t, b = nxt.split(",", 1)
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
    print("  canvas: 128x128 (native p8 pixels -- the host scales)")
    if crop != (0, 0):
        print("  zoom: view(128, %d) -- a host with room shows the centered"
              " rows at its best integer scale (2x fills 240px, 5x fills"
              " 600px); one that presents pixel-for-pixel draws them"
              " unscaled" % vh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
