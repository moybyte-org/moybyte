---@meta
-- moy console API stubs (EmmyLua annotations) -- editor support only, never
-- executed. Keep this file in your cart folder: the Lua language server
-- (VS Code "Lua" extension et al.) indexes it for autocomplete, hover docs
-- and typo squiggles on every console verb. The behavioural contract is the
-- moy spec (SPEC.md); one line here per verb, the spec is the truth.
--
-- Every verb below is CORE (runs on any conforming console) unless marked:
--   EXTENSION: <name> -- standard extension (SPEC.md 10); declare it in your
--                        manifest's "extensions" or hosts may lack it
--   DRAFT 6.1         -- provisional (SPEC.md 6.1): exists in the reference
--                        player, names/signatures still moving, NOT core 0.1
--   VENDOR            -- reference-console feature, no spec; a cart using it
--                        is non-portable by construction
--
-- Screen: 320x240, palette-indexed (64 colours, indices 0-63; 0-15 are the
-- classic base 16). Sheet: 512 8x8 tiles (16 per row). Origin top-left,
-- +x right, +y down. A cart defines up to three globals the console calls:
-- _init(), _update(dt), _draw().

---Screen width in pixels (320).
W = 320
---Screen height in pixels (240).
H = 240

-- --- clear / pixels ---------------------------------------------------------

---Clear the whole screen to colour `c` (default 0).
---@param c? integer palette index 0-63
function cls(c) end

---Set one pixel.
---@param x integer
---@param y integer
---@param c integer palette index 0-63
function pix(x, y, c) end

---Line from (x0,y0) to (x1,y1).
---@param x0 integer
---@param y0 integer
---@param x1 integer
---@param y1 integer
---@param c integer
function line(x0, y0, x1, y1, c) end

-- --- shapes -----------------------------------------------------------------

---Filled rectangle.
---@param x integer
---@param y integer
---@param w integer
---@param h integer
---@param c integer
function rect(x, y, w, h, c) end

---Rectangle border (1px).
---@param x integer
---@param y integer
---@param w integer
---@param h integer
---@param c integer
function rectb(x, y, w, h, c) end

---Filled circle.
---@param cx integer
---@param cy integer
---@param r integer
---@param c integer
function circ(cx, cy, r, c) end

---Circle outline.
---@param cx integer
---@param cy integer
---@param r integer
---@param c integer
function circb(cx, cy, r, c) end

---Filled triangle. DRAFT 6.1.
---@param x1 integer
---@param y1 integer
---@param x2 integer
---@param y2 integer
---@param x3 integer
---@param y3 integer
---@param c integer
function tri(x1, y1, x2, y2, x3, y3, c) end

---Triangle outline. DRAFT 6.1.
---@param x1 integer
---@param y1 integer
---@param x2 integer
---@param y2 integer
---@param x3 integer
---@param y3 integer
---@param c integer
function trib(x1, y1, x2, y2, x3, y3, c) end

-- rect_batch / spans were declared here and never existed in Lua: a trampoline
-- cannot marshal a table or a span buffer, so a cart that believed these stubs
-- got a runtime error. They are gone from the Python side too as of 2026-08-14
-- (plan 6.10) -- a plain `rect` loop is what both languages write now.

-- --- sprites / map ----------------------------------------------------------

---Draw sheet tile `n` (8x8) at (x,y).
---@param n integer tile id
---@param x integer
---@param y integer
---@param colorkey? integer transparent colour (-1 = none)
---@param scale? integer integer scale (default 1)
---@param flip? integer 0 none, 1 horizontal, 2 vertical, 3 both
function spr(n, x, y, colorkey, scale, flip) end

---Stretch-blit a sheet PIXEL region (sx,sy,sw,sh) to a dw x dh screen rect --
---arbitrary (non-integer) scaling; the textured-slice verb. DRAFT 6.1.
---@param sx integer sheet pixel x
---@param sy integer sheet pixel y
---@param sw integer
---@param sh integer
---@param dx integer screen x
---@param dy integer screen y
---@param dw? integer dest width (default sw)
---@param dh? integer dest height (default sh)
---@param colorkey? integer
---@param flip? integer
function sspr(sx, sy, sw, sh, dx, dy, dw, dh, colorkey, flip) end

---Blit a w x h CELL region of the tilemap (top-left cell mx,my) at screen
---(sx,sy). Tiles are the 8x8 sheet sprites; scale=2 makes 16px world tiles.
---@param mx? integer
---@param my? integer
---@param w? integer cells wide (default: whole map)
---@param h? integer cells high
---@param sx? integer
---@param sy? integer
---@param colorkey? integer
---@param scale? integer
function map(mx, my, w, h, sx, sy, colorkey, scale) end

---Read a tilemap cell.
---@param x integer
---@param y integer
---@return integer tile id, -1 outside the map
function mget(x, y) end

---Write a tilemap cell.
---@param x integer
---@param y integer
---@param tile integer
function mset(x, y, tile) end

-- --- text / draw state ------------------------------------------------------

---Print `s` at (x,y) in the 8x8 system font.
---@param s string|number
---@param x integer
---@param y integer
---@param c integer
function print(s, x, y, c) end

---Clip all drawing to a rect; clip() with no args resets.
---@param x? integer
---@param y? integer
---@param w? integer
---@param h? integer
function clip(x, y, w, h) end

---Set the camera offset (subtracted from every draw); camera() resets.
---@param x? integer
---@param y? integer
function camera(x, y) end

---Remap palette index c0 -> c1 for subsequent draws; pal() resets all.
---@param c0? integer
---@param c1? integer
function pal(c0, c1) end

---Mark colour `c` transparent (on=true) for sprite blits; palt() resets.
---@param c? integer
---@param on? boolean
function palt(c, on) end

---Resolve a colour name ("red", "sky", ...) or index to a palette index.
---VENDOR: names describe the DEFAULT table only (a cart-supplied palette
---renames every slot), so this stays out of the spec -- use plain indices.
---@param name_or_index string|integer
---@return integer
function col(name_or_index) end

-- --- input ------------------------------------------------------------------

---Is a button held this frame? Names: "left" "right" "up" "down" "a" "b",
---plus "run" on hosts that have it (SPEC.md 7.3).
---@param name string
---@param player? integer extra controller slot (default 0 = the console)
---@return boolean
function btn(name, player) end

---Was the button PRESSED this frame (the up->down edge)?
---@param name string
---@param player? integer
---@return boolean
function btnp(name, player) end

---Connected player count (>= 1).
---@return integer
function players() end

---Touch state: x, y, tapped (press edge), held -- or nil without a pointer.
---@return integer? x, integer? y, boolean? tapped, boolean? held
function touch() end

---Mouse state, TIC-80-shaped: x, y, left, middle, right, scrollx, scrolly.
---VENDOR: use touch() for portable pointer input.
---@return integer x, integer y, boolean left, boolean middle, boolean right, integer sx, integer sy
function mouse() end

---Last typed key's ASCII code (0 = none), or test a specific code:
---key(string.byte("a")).
---@param code? integer
---@return integer|boolean
function key(code) end

---Key PRESSED this frame (the 0->code edge); same shape as key().
---@param code? integer
---@return integer|boolean
function keyp(code) end

---Switch the keyboard to TEXT input (clean typeable ASCII incl. autorepeat
---delete) or back to game mode. A textmode(true) cart MUST provide its own
---exit via quit().
---@param on? boolean
function textmode(on) end

-- --- system -----------------------------------------------------------------

---Milliseconds since the cart started.
---@return integer
function time() end

---A random float in [0, n) (default n = 1.0).
---@param n? number
---@return number
function rnd(n) end

---Floor to an integer.
---@param x number
---@return integer
function flr(x) end

---Read a config value from the cart's config.json (the player-tunable knobs).
---@param k string
---@param default? any
---@return any
function cfg(k, default) end

---Persistent memory: pmem(i) reads slot i, pmem(i, v) writes it. Survives
---restarts (host permitting).
---@param i integer slot index
---@param v? integer
---@return integer
function pmem(i, v) end

---End this cart and return to whoever launched it. The ONLY exit for a
---textmode(true) cart.
function quit() end

---Declare a logical viewport: the console scales the centered w x h region of
---the canvas to the screen (a 128x128 game fills the display). view() resets.
---EXTENSION: viewport.
---@param w? integer
---@param h? integer
function view(w, h) end

-- --- audio ------------------------------------------------------------------

---Play sound effect `n` (from the cart's sound bank).
---@param n integer
---@param chan? integer channel 0-3 (default: auto)
function sfx(n, chan) end

---A simple beep.
---@param freq number Hz
---@param dur? number seconds (default 0.15)
function beep(freq, dur) end

---Start music track `track`.
---@param track integer
---@param loop? boolean default true
function music(track, loop) end

---Stop the music.
function music_stop() end

---Stop sound on `chan` (or all channels).
---@param chan? integer
function sound_stop(chan) end

---Master volume 0.0-1.0.
---@param level number
function volume(level) end

-- --- layers / images --------------------------------------------------------

---@class MoyLayer
---@field W integer
---@field H integer
local MoyLayer = {}
---Draw into the layer with the same verbs (l:spr(...), l:cls(...)).
---@param img integer|MoyImage tile id or image handle
---@param x? integer
---@param y? integer
---@param colorkey? integer
---@param scale? integer
---@param flip? integer
function MoyLayer:spr(img, x, y, colorkey, scale, flip) end
---@param c? integer
function MoyLayer:cls(c) end

---An off-screen canvas (w x h, may be wider than the screen): pre-render a
---level ONCE, then window-copy per frame with draw_layer -- the 60fps
---scroller pattern. EXTENSION: layers.
---@param w integer
---@param h integer
---@return MoyLayer
function make_layer(w, h) end

---Blit the visible window of `layer` at camera offset (cam_x, cam_y).
---EXTENSION: layers.
---@param layer MoyLayer
---@param cam_x? integer
---@param cam_y? integer
function draw_layer(layer, cam_x, cam_y) end

---Declare a backdrop the console repaints automatically each frame -- a
---colour index, or a layer to pin behind everything. EXTENSION: layers.
---@param x integer|MoyLayer
function background(x) end

---@class MoyImage
local MoyImage = {}

---The cart's painted image asset images/<name>.moyimg as a drawable handle
---(place with a layer's l:spr(img, x, y)), or nil if absent. VENDOR.
---@param name string
---@return MoyImage?
function image(name) end

-- --- data interop ------------------------------------------------------------

---Rows of the cart's tables/<name>.moysheet (numbers as numbers, text as
---strings, blank cells ""). Missing -> {}. NB: `table` stays Lua's table
---library; this verb rides it as a call: table("scores"). VENDOR.
---@param name string
---@return table
---@overload fun(name: string): table

---Lines of the cart's docs/<name>.moytext. Missing -> {}. VENDOR.
---@param name string
---@return string[]
function text(name) end
