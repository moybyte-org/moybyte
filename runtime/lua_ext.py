"""The Lua-side glue for moybyte's OBJECT-valued cart verbs -- one definition.

Three verbs in the moybyte cart API return objects: `make_layer` (a Layer),
`image` (a paint image), and the pair `draw_layer` consumes. No Lua runtime
here marshals objects across its boundary -- moy_lua passes scalars, moycore
passes scalars and tuples, and the host's ctypes binding passes ints and one
string -- so all of them solve it the same way: an int-handle registry on the
Python side, and Lua wrappers that hide the handles from the cart.

This module is that solution, once. It used to live in moy_lua_glue.py, which
made it reachable from the two DEVICE runtimes and invisible to the host's --
and the host's consequently registered the raw closures, whose Layer return
marshalled to nil, so a cart's `lay:spr(...)` died on "index a nil value". A
copy would have fixed that day and drifted the next.

Canonical here in runtime/ like every other shared console module; the boards
and the web runner stage it by name. Pure source and closures: it imports
nothing, so it costs a frozen module and no runtime dependency.
"""

# libmoy's `moy_button` order (moy.h, SPEC.md 7.3). This is an ABI, not a
# preference: the snapshot hands moycore ONE integer per player and its h_btn
# does `(mask >> b) & 1` with `b` the enum value, so bit i means button i of
# THIS tuple and nothing else.
#
# It lives here, beside the rest of the runtime glue, because on 2026-08-13 it
# was "de-duplicated" into InputState.BUTTONS -- and there are two InputState
# classes whose BUTTONS differ in ORDER as well as in length. The host's happens
# to start with libmoy's seven; the boards' starts up/down/left/right. So every
# Lua cart on both boards ran with its d-pad rotated a quarter turn and `run`
# wired to nothing, for a day, silently: no crash, no failing test, and a
# controller permutation is not something a frame hash or an fps number can see.
#
# The lesson in the shape of the fix: a bit order is a property of the PROTOCOL,
# so it cannot be derived from whatever list a particular input class happens to
# keep its names in. InputState.button_masks() takes this tuple as an argument
# and has no opinion about ordering; tests/test_moy_button_order.py parses the
# enum out of moy.h and asserts the two still agree.
MOY_BUTTONS = ("left", "right", "up", "down", "a", "b", "run")

# -- what NOT to register on top of libmoy's table ---------------------------
#
# Every runtime registers the cart's api namespace on top of libmoy's verb
# table, minus these two sets. They lived TWICE until 2026-08-15 -- in
# moycore_glue (staged to both boards and the wasm head) and in lua_host --
# with 46 names agreeing by hand and nothing in the tree comparing them. That
# is the same shape as MOY_BUTTONS above, and it fails the same silent way: a
# name present in one copy and missing in the other does not raise, it just
# registers a Python trampoline OVER libmoy's C for that verb. The cart runs.
# Either the host stops testing what the board runs, or the board quietly takes
# a per-verb slowdown with nothing pointing at a cause -- which is exactly the
# shape of the three moycore regressions CLAUDE.md records.

#
# NOT a tripwire, which is the distinction that decides it. The staging
# closure's HOST_ONLY / NEVER_ON_A_BOARD tables are deliberately kept as twins
# of board.toml (tests/test_staging_closure.py) because they exist to go RED
# when somebody removes a denial -- derive those from the denials and removing
# one removes its own assertion. Nothing asserts anything about the names
# below; they are operational data both runtimes READ, and their failure mode
# is divergence, which is the thing one definition removes.

# The names libmoy's own binding installs -- SPEC.md's verb table. Registering
# any of them would SHADOW a C function with a trampoline, the opposite of the
# point of moycore.
#
# This is a DENY list, not an allow list, and the inversion is deliberate: what
# is stable and enumerable is the set of names libmoy OWNS (SPEC.md's table,
# versioned by spec revision). moybyte's own side is open -- a new cart verb, a
# test harness's `trace`, an app-specific hook -- and an allow list silently
# drops whatever nobody remembered to add to it. It WAS an allow list until an
# extra verb went missing from it. Erring toward registering is also the safe
# direction: an extra global a cart never calls costs one closure, where a
# missing one is a nil-call crash.
#
# Note what is NOT "moybyte's superset" here: SPEC.md 10 defines `layers`
# (make_layer/draw_layer/background) and `viewport` (view) as STANDARD
# extensions, and 6 made view + background core -- so libmoy installs both and
# ours must not shadow them (view costs nothing now: libmoy records it and
# moycore.view() reads it back; background is a clear libmoy does itself).
LIBMOY_VERBS = frozenset((
    # SPEC.md 6 draw + state
    "cls", "pix", "line", "rect", "rectb", "circ", "circb", "print",
    "camera", "clip", "pal", "palt", "tri", "trib", "sspr", "tline",
    "spr", "map", "mget", "mset",
    # 7 input, 8 audio, 9 misc
    "btn", "btnp", "players", "time", "pmem", "cfg", "rnd", "flr", "quit",
    "sfx", "music", "beep", "music_stop", "sound_stop", "volume",
    "touch", "key", "keyp", "textmode",
    # core since the layers promotion
    "view", "background",
))

# Names moybyte owns that still must NOT be registered, each for its own reason.
#
# make_layer/draw_layer/image/Image are object-valued, and objects do not cross
# any of these bindings -- moy_lua passed scalars, moycore passes scalars and
# tuples, the host's ctypes binding passes ints and one string. They ride
# install_handles + PRELUDE_HANDLES below instead, which is also why a moybyte
# layer can take an Image (`lay:spr(image("bg"), ...)`) where libmoy's
# sheet-tile pair cannot.
#
# `table` is the #164 case: registering that name would set the GLOBAL `table`
# and clobber Lua's library, which celeste's p8 shim needs for table.remove. It
# goes in as `moy_table_verb` and PRELUDE_TABLE grafts it onto the library as a
# metatable __call.
# libmoy installs make_layer/draw_layer as CORE since moy-spec b9dbba1
# (2026-08-19): they stopped being SPEC.md 10 extensions because a verb that
# degrades truthfully belongs in core. Its versions return nil when the host
# supplies no Display seam -- verified on the unix build. Moybyte's prelude
# REPLACES them (it runs through moycore.exec before the cart loads), because
# ours are object-valued and actually composite. That override is deliberate,
# not an oversight: a moybyte cart never sees the degrading form.
NOT_REGISTRABLE = frozenset((
    "make_layer", "draw_layer", "image",   # object-valued: prelude + handles
    "Image",                               # a constructor, likewise
    "table",                               # goes in as moy_table_verb (#164)
))

# The prelude in three chunks, because moycore takes only two of them.
#
# Under moy_lua every verb is a registered Python trampoline, so all three
# apply. Under moycore the SPEC verbs are libmoy's own C functions, and
# rnd/flr are among them -- shadowing a lua_CFunction with a Lua one there
# would be a pessimisation AND a semantic change (libmoy's rnd draws from the
# console's rng, which is the thing the spec pins). So PRELUDE_FASTMATH is
# moy_lua's alone; the other two are shared, and shared as SOURCE rather than
# as a second copy, so a fix to the layer wrappers cannot land on one runtime
# and miss the other.
PRELUDE_TABLE = """
do
  -- #164: `table` stays the Lua LIBRARY (celeste's p8 shim needs
  -- table.remove); the #78 cart verb rides it as a metatable __call.
  if moy_table_verb ~= nil then
    local tv = moy_table_verb
    setmetatable(table, { __call = function(_, name) return tv(name) end })
    moy_table_verb = nil
  end
end
"""

PRELUDE_HANDLES = """
do
  local layer_new, layer_spr_img = __layer_new, __layer_spr_img
  local layer_spr, layer_cls = __layer_spr, __layer_cls
  local draw_layer_h, image_h = __draw_layer, __image_handle
  __layer_new, __layer_spr_img, __layer_spr = nil, nil, nil
  __layer_cls, __draw_layer, __image_handle = nil, nil, nil
  function make_layer(w, h)
    local l = { __id = layer_new(w, h), W = w, H = h }
    l.spr = function(self, img, x, y, ck, sc, fl)
      if type(img) == "table" then
        layer_spr_img(self.__id, img.__img, x or 0, y or 0)
      else
        layer_spr(self.__id, img, x or 0, y or 0, ck or -1, sc or 1, fl or 0)
      end
    end
    l.cls = function(self, c) layer_cls(self.__id, c or 0) end
    return l
  end
  function draw_layer(l, cx, cy)
    draw_layer_h(l.__id, cx or 0, cy or 0)
  end
  local cache = {}
  function image(name)
    local t = cache[name]
    if t ~= nil then
      if t == false then return nil end
      return t
    end
    local h = image_h(name)
    if h < 0 then
      cache[name] = false
      return nil
    end
    t = { __img = h }
    cache[name] = t
    return t
  end
end
"""

PRELUDE_FASTMATH = """
do
  -- #66 M0: rnd/flr as pure Lua. The registered trampolines cost a full
  -- upcall for what is one arithmetic op; shadowing them here (after the
  -- register loop, before the cart) makes them VM-local. rnd's PRNG changes
  -- from Python's to Lua's -- rnd is random, no cart may depend on the
  -- stream. time() deliberately STAYS a trampoline: it reads live input
  -- state (cart_start_ms) in MicroPython's 30-bit ticks domain, and no cart
  -- calls it hot enough to pay for a second clock.
  local mrandom, mfloor = math.random, math.floor
  function rnd(n) return mrandom() * (n or 1.0) end
  function flr(x) return mfloor(x) end
end
"""

_LUA_PRELUDE = PRELUDE_TABLE + PRELUDE_HANDLES + PRELUDE_FASTMATH


def install_handles(ns, reg):
    """Register the int-handle half of PRELUDE_HANDLES; return the registries.

    The object-valued API entries (layers, paint images) stay Python-side and
    the prelude's Lua wrappers speak int handles to them, because objects have
    never crossed either VM boundary -- moy_lua marshals scalars, and moycore
    marshals scalars and tuples. The returned lists also PIN the objects for
    the run's lifetime; drop them at close and the layers go with them.

    `reg` is the runtime's own register verb (`moy_lua.register` or
    `moycore.register`), which is the only thing that differs between the two.
    """
    layers = []
    images = []
    make_layer = ns.get("make_layer")
    draw_layer = ns.get("draw_layer")
    image = ns.get("image")

    def _layer_new(w, h):
        layers.append(make_layer(int(w), int(h)))
        return len(layers) - 1

    def _layer_spr_img(lid, ih, x, y):
        layers[int(lid)].spr(images[int(ih)], int(x), int(y))

    def _layer_spr(lid, tile, x, y, ck, sc, fl):
        layers[int(lid)].spr(int(tile), int(x), int(y), int(ck),
                             int(sc), int(fl))

    def _layer_cls(lid, c):
        layers[int(lid)].cls(int(c))

    def _draw_layer(lid, cx, cy):
        draw_layer(layers[int(lid)], cx, cy)

    def _image_handle(name):
        img = image(name) if image is not None else None
        if img is None:
            return -1
        images.append(img)
        return len(images) - 1

    reg("__layer_new", _layer_new)
    reg("__layer_spr_img", _layer_spr_img)
    reg("__layer_spr", _layer_spr)
    reg("__layer_cls", _layer_cls)
    reg("__draw_layer", _draw_layer)
    reg("__image_handle", _image_handle)
    return layers, images
