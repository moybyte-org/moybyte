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
