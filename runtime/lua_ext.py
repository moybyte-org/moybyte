"""The Lua-side glue for moybyte's OBJECT-valued cart verbs -- one definition.

Several families of the moybyte cart API return objects: `make_layer` (a
Layer), `image` (a paint image), the placement verbs of #85/#109 (`scene`,
`actors` and the actor they hand out), and `open_editor` (#112, a text editor
over one document). No Lua runtime here marshals objects across its boundary --
moy_lua passes scalars, moycore passes scalars and tuples, and the host's
ctypes binding passes ints and strings -- so all of them solve it the same way:
an int-handle registry on the Python side, and Lua wrappers that hide the
handles from the cart. A verb that answers a PAIR encodes it as one string and
splits it in Lua, for the same reason the scene rows do.

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
    # fillp, oval/ovalb, sget/sset, fget/fset (SPEC.md 6, 7.1). The Python tier
    # has its own twins of all of them now, which is exactly why these are
    # denied: registering one would shadow libmoy's C with that trampoline.
    "fillp", "oval", "ovalb", "sget", "sset", "fget", "fset",
    # 7 input, 8 audio, 9 misc
    "btn", "btnp", "players", "time", "pmem", "cfg", "rnd", "srand", "flr", "quit",
    "sfx", "music", "beep", "music_stop", "sound_stop", "volume",
    "touch", "key", "keyp", "textmode",
    # core since the layers promotion
    "view", "background",
))

# Names moybyte owns that still must NOT be registered, each for its own reason.
#
# make_layer/draw_layer/image/Image are object-valued, and objects do not cross
# any of these bindings -- moy_lua passed scalars, moycore passes scalars and
# tuples, the host's ctypes binding passes ints and strings. They ride
# install_handles + PRELUDE_HANDLES below instead, which is also why a moybyte
# layer can take an Image (`lay:spr(image("bg"), ...)`) where libmoy's
# sheet-tile pair cannot.
#
# The placement verbs (#214) are denied for the same reason and ride the same
# route: scene()/load_scene()/actors() answer with a LIST of Actor rows, and
# touching/move_actor/move_actor_to/remove_actor take one. draw_scene stays
# registered -- no arguments, no result, nothing to marshal.
#
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
    "scene", "load_scene", "actors",       # rows of actors: prelude + handles
    "touching", "move_actor", "move_actor_to", "remove_actor",
    "open_editor",                         # an editor handle: prelude + handles
))

# The prelude in two chunks, because moycore takes only one of them.
#
# Under moy_lua every verb is a registered Python trampoline, so both
# apply. Under moycore the SPEC verbs are libmoy's own C functions, and
# rnd/flr are among them -- shadowing a lua_CFunction with a Lua one there
# would be a pessimisation AND a semantic change (libmoy's rnd draws from the
# console's rng, which is the thing the spec pins). So PRELUDE_FASTMATH is
# moy_lua's alone; PRELUDE_HANDLES is shared, and shared as SOURCE rather than
# as a second copy, so a fix to the layer wrappers cannot land on one runtime
# and miss the other.
PRELUDE_HANDLES = """
do
  local layer_new, layer_spr_img = __layer_new, __layer_spr_img
  local layer_spr, layer_cls = __layer_spr, __layer_cls
  local layer_map = __layer_map
  local draw_layer_h, image_h = __draw_layer, __image_handle
  __layer_new, __layer_spr_img, __layer_spr = nil, nil, nil
  __layer_cls, __draw_layer, __image_handle = nil, nil, nil
  __layer_map = nil
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
    -- The tile route into a layer, which is how a scroller actually fills one:
    -- a level is a map, and without this a Lua cart had to spr() every cell.
    -- The tile counts default to the layer's own size rather than crossing a
    -- nil, because the trampoline speaks scalars.
    l.map = function(self, mx, my, tw, th, sx, sy)
      layer_map(self.__id, mx or 0, my or 0, tw or (self.W // 8),
                th or (self.H // 8), sx or 0, sy or 0)
    end
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

do
  -- The placement API (#85/#109) for Lua carts, #214. A scene row is a plain
  -- table -- a.tag / a.tile / a.x / a.y / a.flip / a.flags, the same names the
  -- Python rows carry -- and `__id` is its handle back to the Actor, exactly
  -- as a layer's `__id` is.
  --
  -- A WHOLE SCENE CROSSES AS ONE STRING, decoded here. The per-field
  -- alternative is six upcalls a row, and a scene is up to a few hundred rows;
  -- the encoder is the Python half of this same file, so the two ends cannot
  -- drift apart across the two runtimes.
  --
  -- The live world is then MIRRORED here and the mirror is authoritative:
  -- actors()/touching()/move_actor()/remove_actor() are pure Lua over it, at
  -- zero upcalls a frame, and draw_scene() -- the only verb that reads the
  -- actors back on the Python side -- pushes down what actually changed first.
  local scene_rows, scene_load, world_rows = __scene_rows, __scene_load, __world_rows
  local actor_set, actor_tag = __actor_set, __actor_tag
  local actor_flag, actor_drop = __actor_flag, __actor_remove
  local draw_scene_h = draw_scene
  __scene_rows, __scene_load, __world_rows = nil, nil, nil
  __actor_set, __actor_tag, __actor_flag, __actor_remove = nil, nil, nil, nil
  local find, sub, gsub = string.find, string.sub, string.gsub
  local floor, tremove, tonum = math.floor, table.remove, tonumber
  local UNESC = { c = ",", ["\\\\"] = "\\\\" }
  local rows = {}                 -- __id -> the ONE table that actor ever gets

  local function rd(s, p)         -- one comma-terminated field, unescaped
    local e = find(s, ",", p, true)
    local v = sub(s, p, e - 1)
    if find(v, "\\\\", 1, true) then v = gsub(v, "\\\\(.)", UNESC) end
    return v, e + 1
  end

  local function decode(blob)
    local out, p, n = {}, 1, nil
    n, p = rd(blob, p)
    for i = 1, tonum(n) do
      local id, tile, x, y, flip, tag, nf, k, kind, v
      id, p = rd(blob, p)
      tile, p = rd(blob, p)
      x, p = rd(blob, p)
      y, p = rd(blob, p)
      flip, p = rd(blob, p)
      tag, p = rd(blob, p)
      nf, p = rd(blob, p)
      id = tonum(id)
      local row = rows[id]
      if row == nil then
        row = { __id = id, flags = {} }
        rows[id] = row
      end
      row.tag, row.tile = tag, tonum(tile)
      row.x, row.y, row.flip = tonum(x), tonum(y), tonum(flip)
      local fl = row.flags
      for _ = 1, tonum(nf) do
        k, p = rd(blob, p)
        kind, p = rd(blob, p)
        v, p = rd(blob, p)
        if kind == "n" then fl[k] = tonum(v)
        elseif kind == "b" then fl[k] = (v == "1")
        else fl[k] = v end
      end
      out[i] = row
    end
    return out
  end

  local function snapshot(src, tag)
    local out, n = {}, 0
    for i = 1, #src do
      local r = src[i]
      if tag == nil or r.tag == tag then
        n = n + 1
        out[n] = r
      end
    end
    return out
  end

  -- A fresh sequence over the SAME rows every call, which is what the Python
  -- side does (`list(self._parse(n))`): a cart may reorder its own copy, and a
  -- for-each may remove_actor() mid-loop, without either reaching the cache.
  local parsed = {}
  function scene(name)
    local key = name or ""
    local got = parsed[key]
    if got == nil then
      got = decode(scene_rows(key))
      parsed[key] = got
    end
    return snapshot(got)
  end

  function load_scene(name)
    local got = decode(scene_load(name or ""))
    parsed = {}                   -- the ACTIVE scene moved
    return snapshot(got)
  end

  local live, gone = nil, nil
  local sx, sy, st, sf, sg, sfl = {}, {}, {}, {}, {}, {}

  local function world()
    if live == nil then
      live = decode(world_rows())
      for i = 1, #live do
        local r = live[i]
        local h = r.__id
        sx[h], sy[h], st[h], sf[h], sg[h] = r.x, r.y, r.tile, r.flip, r.tag
        -- The shadow starts at what Python HOLDS, not empty: a flag the cart
        -- clears before the first draw_scene would otherwise be in neither
        -- table and never be sent down.
        local shadow = nil
        for k, v in pairs(r.flags) do
          shadow = shadow or {}
          shadow[k] = v
        end
        sfl[h] = shadow
      end
    end
    return live
  end

  function actors(tag)
    return snapshot(world(), tag)
  end

  local function overlap(a, b)
    return a.x < b.x + 8 and b.x < a.x + 8 and a.y < b.y + 8 and b.y < a.y + 8
  end

  function touching(a, b)
    if a == nil then return false end
    if type(b) == "table" then return overlap(a, b) end
    local w = world()
    for i = 1, #w do
      local o = w[i]
      if o ~= a and o.tag == b and overlap(a, o) then return true end
    end
    return false
  end

  -- int() truncates toward zero, and the seam carries integers only, so the
  -- rounding happens HERE rather than differing between the two tiers.
  local function itrunc(v)
    if v >= 0 then return floor(v) end
    return -floor(-v)
  end

  function move_actor(a, dx, dy)
    if a == nil then return end
    a.x, a.y = itrunc(a.x + dx), itrunc(a.y + dy)
  end

  function move_actor_to(a, x, y)
    if a == nil then return end
    a.x, a.y = itrunc(x), itrunc(y)
  end

  function remove_actor(a)
    if a == nil then return end
    local w = world()
    for i = 1, #w do
      if w[i] == a then
        tremove(w, i)
        gone = gone or {}
        gone[#gone + 1] = a.__id
        return
      end
    end
  end

  function draw_scene()
    if live ~= nil then
      if gone ~= nil then
        for i = 1, #gone do actor_drop(gone[i]) end
        gone = nil
      end
      for i = 1, #live do
        local r = live[i]
        local h = r.__id
        local x, y, tile, flip = itrunc(r.x), itrunc(r.y), r.tile, r.flip
        if x ~= sx[h] or y ~= sy[h] or tile ~= st[h] or flip ~= sf[h] then
          sx[h], sy[h], st[h], sf[h] = x, y, tile, flip
          actor_set(h, x, y, tile, flip)
        end
        if r.tag ~= sg[h] then
          sg[h] = r.tag
          actor_tag(h, r.tag)
        end
        local fl, shadow = r.flags, sfl[h]
        if next(fl) ~= nil or shadow ~= nil then
          if shadow == nil then shadow = {}; sfl[h] = shadow end
          for k, v in pairs(fl) do
            if shadow[k] ~= v then shadow[k] = v; actor_flag(h, k, v) end
          end
          for k in pairs(shadow) do
            if fl[k] == nil then shadow[k] = nil; actor_flag(h, k) end
          end
        end
      end
    end
    if draw_scene_h ~= nil then draw_scene_h() end
  end
end
"""

PRELUDE_EDITOR = """
do
  -- The EDITOR HANDLE (#112, docs/text_editing_2026-09.md) for Lua carts. Same
  -- route as a layer: an int handle Python-side, a wrapper table here, and only
  -- numbers and strings across the boundary. The two verbs that answer a PAIR
  -- -- tap and save -- encode it as one comma-joined string and split it here,
  -- because a tuple crosses moycore and does not cross the other two bindings.
  --
  -- Defined ONLY when the cart earned it. `open_editor` rides the `files`
  -- permission; without it there is no trampoline and therefore no global, so a
  -- Lua cart that reaches for it dies on a nil call -- the same answer a Python
  -- cart gets from a NameError, which is the whole point of the gate.
  local ed_open, ed_draw, ed_tap = __ed_open, __ed_draw, __ed_tap
  local ed_focus, ed_key, ed_do = __ed_focus, __ed_key, __ed_do
  local ed_save, ed_scroll, ed_settext = __ed_save, __ed_scroll, __ed_settext
  __ed_open, __ed_draw, __ed_tap = nil, nil, nil
  __ed_focus, __ed_key, __ed_do = nil, nil, nil
  __ed_save, __ed_scroll, __ed_settext = nil, nil, nil
  local find, sub, tonum = string.find, string.sub, tonumber

  local function pair(s)
    local e = find(s, ",", 1, true)
    if e == nil then return s, nil end
    return sub(s, 1, e - 1), sub(s, e + 1)
  end

  local function flag(v)
    if v == nil or v then return 1 end
    return 0
  end

  if ed_open ~= nil then
    function open_editor(name, mode)
      local id = ed_open(name or "", mode or "")
      if id < 0 then return nil end
      local e = { __id = id }
      function e:draw(x, y, w, h, scale)
        ed_draw(self.__id, x, y, w, h, scale or 1)
      end
      function e:tap(x, y, click)
        local got = ed_tap(self.__id, x, y, flag(click))
        if got == "" then return nil end
        local verb, arg = pair(got)
        if verb == "check" then return verb, tonum(arg) end
        if verb == "caret" then return verb, nil end
        return verb, arg
      end
      function e:focus(on) return ed_focus(self.__id, flag(on)) == 1 end
      function e:key(code) return ed_key(self.__id, code or 0) == 1 end
      function e:save(soft)
        local ok, why = pair(ed_save(self.__id, soft and 1 or 0))
        return ok == "1", why
      end
      function e:caret()
        local r, c = pair(ed_do(self.__id, "caret"))
        return tonum(r), tonum(c)
      end
      function e:scroll(rows, cols)
        ed_scroll(self.__id, rows or 0, cols or 0)
      end
      function e:set_text(body) ed_settext(self.__id, body or "") end
      for _, v in ipairs({"focused", "can_undo", "can_redo", "undo", "redo",
                          "select_all", "copy", "cut", "paste", "dirty"}) do
        e[v] = function(self) return ed_do(self.__id, v) == 1 end
      end
      for _, v in ipairs({"text", "badge", "name", "mode"}) do
        e[v] = function(self) return ed_do(self.__id, v) end
      end
      function e:close() ed_do(self.__id, "close") end
      return e
    end
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

# One name for everything the handle registry backs, because every runtime
# feeds `PRELUDE_HANDLES` to its VM alongside one `install_handles` call and a
# second name would be a second thing to remember to send.
PRELUDE_HANDLES = PRELUDE_HANDLES + PRELUDE_EDITOR

_LUA_PRELUDE = PRELUDE_HANDLES + PRELUDE_FASTMATH


def _esc(s):
    """One blob field. `,` separates fields, so `,` and `\\` are escaped."""
    return str(s).replace("\\", "\\\\").replace(",", "\\c")


def _flag_field(v):
    """A scene flag as (kind, text), or None for a value Lua has no shape for."""
    if v is True or v is False:
        return "b", "1" if v else "0"
    if isinstance(v, int) or isinstance(v, float):
        return "n", str(v)
    if isinstance(v, str):
        return "s", v
    return None


def rows_blob(rows, ident):
    """Actor rows as the ONE string the prelude's `decode` reads.

    `count,` then per row `id,tile,x,y,flip,tag,nflags,` and `key,kind,value,`
    per flag. Every field is comma-terminated and escaped, so a tag holding a
    comma (or a backslash) survives and the decoder never has to count bytes --
    which is what keeps it correct for a non-ASCII tag under Lua's byte
    strings. Dependency-free and MicroPython-safe: both boards run this.
    """
    parts = [str(len(rows))]
    for a in rows:
        parts.append(str(ident(a)))
        parts.append(str(a.tile))
        parts.append(str(a.x))
        parts.append(str(a.y))
        parts.append(str(a.flip))
        parts.append(_esc(a.tag))
        flags = []
        for k in (a.flags or {}):
            kv = _flag_field(a.flags[k])
            if kv is not None:
                flags.append(_esc(k))
                flags.append(kv[0])
                flags.append(_esc(kv[1]))
        parts.append(str(len(flags) // 3))
        parts.extend(flags)
    return ",".join(parts) + ","


def install_handles(ns, reg):
    """Register the int-handle half of PRELUDE_HANDLES; return the registries.

    The object-valued API entries (layers, paint images, the placement rows of
    #85/#109) stay Python-side and the prelude's Lua wrappers speak int handles
    to them, because objects have never crossed either VM boundary -- moy_lua
    marshals scalars, and moycore marshals scalars and tuples. The returned
    lists also PIN the objects for the run's lifetime; drop them at close and
    the layers go with them.

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

    def _layer_map(lid, mx, my, tw, th, sx, sy):
        layers[int(lid)].map(int(mx), int(my), int(tw), int(th),
                             int(sx), int(sy))

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
    reg("__layer_map", _layer_map)
    reg("__draw_layer", _draw_layer)
    reg("__image_handle", _image_handle)

    # The placement half (#214). `scene` and friends are absent from a
    # make_layer/probe namespace, so every one of these degrades to "no actors"
    # rather than to a nil global the cart dies on.
    scene = ns.get("scene")
    load_scene = ns.get("load_scene")
    world_actors = ns.get("actors")
    drop_actor = ns.get("remove_actor")
    actors_by_id = []
    id_of = {}

    def _ident(a):
        h = id_of.get(a)
        if h is None:
            h = len(actors_by_id)
            actors_by_id.append(a)
            id_of[a] = h
        return h

    def _scene_rows(name):
        if scene is None:
            return "0,"
        return rows_blob(scene(name) if name else scene(), _ident)

    def _scene_load(name):
        if load_scene is None:
            return "0,"
        return rows_blob(load_scene(name), _ident)

    def _world_rows():
        if world_actors is None:
            return "0,"
        return rows_blob(world_actors(), _ident)

    def _actor_set(h, x, y, tile, flip):
        a = actors_by_id[int(h)]
        a.x, a.y, a.tile, a.flip = int(x), int(y), int(tile), int(flip)

    def _actor_tag(h, tag):
        actors_by_id[int(h)].tag = str(tag)

    def _actor_flag(h, key, value=None):
        flags = actors_by_id[int(h)].flags
        if value is None:
            flags.pop(key, None)
        else:
            flags[key] = value

    def _actor_remove(h):
        if drop_actor is not None:
            drop_actor(actors_by_id[int(h)])

    reg("__scene_rows", _scene_rows)
    reg("__scene_load", _scene_load)
    reg("__world_rows", _world_rows)
    reg("__actor_set", _actor_set)
    reg("__actor_tag", _actor_tag)
    reg("__actor_flag", _actor_flag)
    reg("__actor_remove", _actor_remove)
    _install_editor(ns, reg, layers)
    return layers, images


# The verbs the prelude reaches through ONE `__ed_do` trampoline: no arguments,
# and an answer that is already a scalar. Split by what they answer, because
# Lua compares the booleans against 1 and takes the strings as they are.
_ED_FLAGS = ("focused", "can_undo", "can_redo", "undo", "redo", "select_all",
             "copy", "cut", "paste", "dirty")
_ED_TEXTS = ("text", "badge", "name", "mode")


def _install_editor(ns, reg, pins):
    """The editor handle's int-handle half (#112).

    Registered only when the cart's manifest earned `open_editor`, so a Lua
    cart without the grant has no global rather than one that answers nil --
    see PRELUDE_EDITOR. `pins` is the same list the layers ride: it keeps the
    handles alive for the run and drops them with it."""
    open_editor = ns.get("open_editor")
    if open_editor is None:
        return
    editors = []

    def _ed_open(name, mode):
        # "" is the PARAMETERLESS form -- a document has a name, so the empty
        # string cannot collide with one, and the trampoline speaks scalars.
        ed = open_editor(str(name) or None, str(mode) or None)
        if ed is None:
            return -1
        editors.append(ed)
        pins.append(ed)
        return len(editors) - 1

    def _ed_draw(h, x, y, w, ht, scale):
        editors[int(h)].draw(int(x), int(y), int(w), int(ht), int(scale))

    def _ed_tap(h, x, y, click):
        got = editors[int(h)].tap(int(x), int(y), bool(int(click)))
        if got is None:
            return ""
        return got[0] + "," + ("" if got[1] is None else str(got[1]))

    def _ed_focus(h, on):
        return 1 if editors[int(h)].focus(bool(int(on))) else 0

    def _ed_key(h, code):
        return 1 if editors[int(h)].key(int(code)) else 0

    def _ed_do(h, verb):
        ed = editors[int(h)]
        if verb == "caret":
            row, col = ed.caret()
            return str(row) + "," + str(col)
        if verb in _ED_TEXTS:
            return str(getattr(ed, verb)())
        if verb in _ED_FLAGS:
            return 1 if getattr(ed, verb)() else 0
        if verb == "close":
            ed.close()
        return 0

    def _ed_save(h, soft):
        ok, why = editors[int(h)].save(soft=bool(int(soft)))
        return ("1," if ok else "0,") + str(why)

    def _ed_scroll(h, rows, cols):
        editors[int(h)].scroll(int(rows), int(cols))

    def _ed_settext(h, body):
        editors[int(h)].set_text(str(body))

    reg("__ed_open", _ed_open)
    reg("__ed_draw", _ed_draw)
    reg("__ed_tap", _ed_tap)
    reg("__ed_focus", _ed_focus)
    reg("__ed_key", _ed_key)
    reg("__ed_do", _ed_do)
    reg("__ed_save", _ed_save)
    reg("__ed_scroll", _ed_scroll)
    reg("__ed_settext", _ed_settext)
