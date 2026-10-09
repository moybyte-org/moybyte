# Map (grep -n a name to jump there):
#   cart_chunks                       a cart's Lua scripts as chunks
#   -- the frame seam, once for both Lua tiers  snap_shared, sync_view, drain_audio
#   snap_shared                       player two, the pointer and the clock into the cart
#   sync_view                         apply the cart's view() to the console
#   drain_audio                       play the queued audio on the run's session
#   -- what NOT to register on top of libmoy's table  put_scenes, install_handles
#   put_scenes                        hand the cart's scene texts to the run
#   install_handles                   register the int-handle half of the prelude
"""What both Lua tiers share -- the object-verb glue, and the frame seam.

Several families of the moybyte cart API return objects: `make_layer` (a
Layer), `image` (a paint image), the placement verbs of #85/#109 (`scene`,
`actors` and the actor they hand out), and `open_editor` (#112, a text editor
over one document). No Lua runtime here marshals objects across its boundary --
moycore passes scalars and tuples, and the host's ctypes binding passes ints
and strings -- so all of them solve it the same way: Lua wrappers in the
prelude that hide a handle from the cart. The placement rows are plain Lua
tables over C (native/moycore/moycore_scene.h); the editor is an int handle
into a Python registry, and a verb of it that answers a PAIR encodes it as one
string and splits it in Lua.

This module is that solution, once, for EVERY runtime: a second copy is how a
host runtime once registered the raw closures, whose Layer return marshalled to
nil, so a cart's `lay:spr(...)` died on "index a nil value".

The FRAME SEAM is the other half and the same argument: the snapshot slots, the
view declaration and the audio drain are what a Lua runtime does around every
tick, and they were written once per tier down to the guard comments. See
`snap_shared` below.

Canonical here in runtime/ like every other shared console module; the boards
and the web runner stage it by name. Pure source and closures: it imports
nothing, so it costs a frozen module and no runtime dependency.
"""

# SPEC.md 4: the cart's scripts as the (src, chunkname) pairs both Lua tiers
# hand to `load()`, in load order. ONE definition, because the ORDER is the
# whole of it -- a tier that ran a port's shim AFTER its game would fail inside
# the author's own code, which is the report that sends the reader furthest
# wrong.
#
# The Player puts the scripts either side of main on the namespace as
# `_moy_pre` / `_moy_post`, each a list of (filename, text); `src` is main's
# and travels on its own because the Editor edits it and the crash panel maps
# its lines. Hence main's chunk name stays "@cart" -- player._lua_cart_line
# parses exactly that to mark the bad line (#24) -- while the others are named
# after their file, so a fault in a generated shim reads `p8.lua:412:` and
# never lands on the kid's line.
def cart_chunks(ns, src):
    out = []
    for name, text in ns.get("_moy_pre") or ():
        out.append((text, "@" + name))
    out.append((src, "@cart"))
    for name, text in ns.get("_moy_post") or ():
        out.append((text, "@" + name))
    return out


# libmoy's `moy_button` order (moy.h, SPEC.md 7.3). This is an ABI, not a
# preference: the snapshot hands moycore ONE integer per player and its h_btn
# does `(mask >> b) & 1` with `b` the enum value, so bit i means button i of
# THIS tuple and nothing else.
#
# It lives here, beside the rest of the runtime glue, because on 2026-08-13 it
# was "de-duplicated" into InputState.BUTTONS -- and the two tiers' BUTTONS then
# differed in ORDER as well as in length. The host's happened to start with
# libmoy's seven; the boards' started up/down/left/right. So every
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


# -- the frame seam, once for both Lua tiers ---------------------------------
#
# The glue (device/moycore_glue.py, every tier's Lua cart and a board's
# compiled one) and the host's compiled-cart runtime (runtime/wasm_host.py) do
# the same three things around every tick the kernel's Player does not run:
# fill the snapshot slots libmoy reads, apply the cart's view() declaration,
# and drain the audio queue through the api closures. They differ only in
# WHERE the ABI constants come from -- the `moycore` module, `wasm_binding` on
# the host -- so each resolves its own indices once and the bodies are these.
#
# That is the same argument MOY_BUTTONS above records, applied one level up: a
# seam written twice diverges in the half nobody runs, and the d-pad incident
# is what a silent divergence in this file's subject matter looks like.
#
# `pointer_state` and `since_ms` are passed IN rather than imported, so this
# module keeps its one property: it imports nothing, costs a frozen module and
# drags no dependency onto a board or the wasm head.

SNAP_SLOTS = ("SNAP_BTN_P1", "SNAP_BTNP_P1", "SNAP_PLAYERS",
              "SNAP_TOUCH_X", "SNAP_TOUCH_Y", "SNAP_TOUCH_DOWN",
              "SNAP_TOUCH_MS", "SNAP_TIME_MS")

AQ_OPS = ("AQ_SFX", "AQ_MUSIC", "AQ_BEEP", "AQ_MUSIC_STOP", "AQ_SOUND_STOP",
          "AQ_VOLUME")


def snap_slots(mod):
    """The indices `snap_shared` writes, read off this tier's ABI module."""
    return tuple(getattr(mod, name) for name in SNAP_SLOTS)


def audio_ops(mod):
    """The op codes `drain_audio` tests, read off this tier's ABI module."""
    return tuple(getattr(mod, name) for name in AQ_OPS)


def snap_shared(s, inp, idx, pointer_state, out, since_ms):
    """PLAYER TWO, THE POINTER and THE CLOCK, into the snapshot the tick will
    read.

    PLAYER TWO (#65). These slots exist in the C ABI and nothing filled them, so
    libmoy's `players()` answered 1 forever and a Lua cart could not have a
    second player at all -- the Python twin of the same cart fielded two tanks
    and the Lua one fielded one. The count is read through the router because a
    transport slot (a radio peer) lives there, not on the InputState; the fast
    path costs one dict test.

    THE POINTER, in the cart's own coordinates (moy_input.pointer_state). Same
    omission and the same consequence: the slot is in the C ABI, libmoy's
    touch() reads it, and nothing on either Lua tier ever wrote it -- so
    `touch()` answered nil for every Lua cart everywhere while the Python twin
    of the same cart had a pointer. The slot carries P_LIVE/P_HELD/P_CLICK as
    FLAGS, not a boolean: it is the only slot h_touch has, and touch() has to
    answer "is there one", "is it down" and "did it go down this frame" out of
    it. 0 is no pointer, which is what SPEC.md 7.3 means by nil.

    THE CLOCK: milliseconds since the Player stamped `cart_start_ms`
    (`ticks._since_ms`), the base libmoy's time() adds the milliseconds inside
    the tick to -- the same clock a Python cart's time() reads. An input with
    no stamp leaves the slot as it was.
    """
    n = 1
    pr = getattr(inp, "players", None)
    if pr is not None:
        n = pr.count()
        if n > 1:
            h1, p1 = pr.button_masks(MOY_BUTTONS, 1)
            s[idx[0]] = h1
            s[idx[1]] = p1
    s[idx[2]] = n
    try:
        x, y, st, ms = pointer_state(inp, out)
        s[idx[3]] = int(x)
        s[idx[4]] = int(y)
        s[idx[5]] = int(st)
        s[idx[6]] = int(ms)
    except Exception:  # noqa: BLE001 -- no pointer this frame, not a dead cart
        s[idx[5]] = 0
    start = getattr(inp, "cart_start_ms", None)
    if start is not None:
        s[idx[7]] = since_ms(start)


def cfg_blob(cfg):
    """A config dict as the "key\\0value\\0" table the cart's cfg() reads
    (native/moycore/moycore_run.c): a string without its quotes, a boolean as
    1/0, a number as config.json spells it; a list, a dict or None is no
    value. One table, built here for every tier."""
    out = bytearray()
    for k, v in sorted((cfg or {}).items()):
        if isinstance(v, bool):
            text = "1" if v else "0"
        elif isinstance(v, int):
            text = "%d" % v
        elif isinstance(v, float):
            text = "%.7g" % v
        elif isinstance(v, str):
            text = v
        else:
            continue                   # a list/dict/None is not a value
        out += str(k).encode() + b"\0" + text.encode() + b"\0"
    return bytes(out)


def sync_view(ws, view, last):
    """Apply the cart's view() to the console; returns the view now in force.

    libmoy owns the verb (SPEC.md 6 core) and records the declaration; the
    console still has to ACT on it -- ws.input.game_view is what the WM
    composites from. So this reads the recording instead of the cart crossing
    into Python to set it, which is the whole point of the verb moving into
    core. Checked per frame because the spec allows a cart to change its region
    at runtime, and skipped when unchanged, so a cart that declares once pays
    one comparison.
    """
    if view == last:
        return last
    try:
        ws.input.game_view = view
    except Exception:  # noqa: BLE001 -- a console without the field is fine
        pass
    return view


def drain_audio(au, ops, queue):
    """Play the queued audio commands on the run's session `au` (an
    audio_session.AudioSession, or None: no audio), one kernel call per
    command, in the queue's order. `queue` yields rows indexable as (op, a, b).
    """
    if au is None:
        return
    sfx, music, beep, music_stop, sound_stop, volume = ops
    for row in queue:
        op, a, b = row[0], row[1], row[2]
        try:
            if op == sfx:
                au.sfx(a, None if b < 0 else b)
            elif op == music:
                au.music(a, bool(b))
            elif op == beep:
                au.beep(a, b / 1000.0)
            elif op == music_stop:
                au.music_stop()
            elif op == sound_stop:
                au.sound_stop(None if a < 0 else a)
            elif op == volume:
                au.volume(a)
        except Exception:  # noqa: BLE001 -- one bad command is not the frame
            pass

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
# The placement verbs (#214) are the prelude's Lua over the run's C
# (moycore_scene.h): scene()/load_scene()/actors() answer a LIST of row
# tables, and draw_scene draws them. A trampoline registered over any of them
# would replace the prelude's.
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
    "scene", "load_scene", "actors",       # rows of actors: prelude + C
    "touching", "move_actor", "move_actor_to", "remove_actor", "draw_scene",
    "open_editor",                         # an editor handle: prelude + handles
))

# The superset names a Lua run answers in C (native/moycore/moycore_superset.h),
# so a frame that calls them makes no crossing: registering the namespace's
# Python over them would shadow the C with a trampoline.
NATIVE_NAMES = frozenset(("col", "mouse"))


def layer_restore(bind, canvas, run):
    """Hand a Lua run the screen canvas's layer restore state (device_canvas's
    `_lrs`) through the run's `bind`: its native draw_layer then shares the
    canvas's prediction, which the canvas's sync_back kicks while `_lrs_run`
    names the run. A canvas with no state leaves the run on libmoy's copy."""
    lr = getattr(canvas, "_lrs", None)
    bind(lr)
    if lr is not None:
        canvas._lrs_run = run


def layer_restore_end(canvas, run):
    """The run is over: its canvas stops kicking for it."""
    if getattr(canvas, "_lrs_run", None) is run:
        canvas._lrs_run = None


# What a Lua cart's layer answers: every drawing verb, and the draw STATE that
# scopes it -- libmoy's own LAYER_VERBS (native/moycore/libmoy/moy_lua.c), the
# set moy-spec's players give a layer, and tests/test_lua_layers.py holds the
# two lists equal. Each method is the SCREEN's verb, libmoy's C, run against
# the layer's canvas (native/moycore/moycore_layers.h), so a layer's camera,
# clip and palettes are its own and nothing per call crosses into Python.
# `spr` also places a paint image, which is the console's object and the one
# layer draw that goes through Python.
LAYER_VERBS = (
    "cls", "pix", "line", "rect", "rectb", "circ", "circb", "oval", "ovalb",
    "print", "camera", "clip", "pal", "palt", "fillp", "spr", "map",
    "tri", "trib", "sspr", "tline",
)

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
  local layer_canvas, layer_verb = __layer_canvas, __layer_verb
  local layer_blit, image_h = __layer_blit, __image_handle
  __layer_new, __layer_spr_img, __layer_canvas = nil, nil, nil
  __layer_verb, __layer_blit, __image_handle = nil, nil, nil
  local setmt, type = setmetatable, type
  -- The methods every layer shares (LAYER_VERBS in runtime/lua_ext.py): the
  -- screen's own verbs, run against the layer's canvas `__c`. Each one sets
  -- `__e`, which tells draw_layer the pixels moved since it last looked.
  local Layer = {}
  Layer.__index = Layer
  for _, name in ipairs({@LAYER_VERBS@}) do
    Layer[name] = layer_verb(_ENV[name])
  end
  local spr_tile = Layer.spr
  function Layer.spr(self, img, x, y, ...)
    if type(img) == "table" then
      self.__e = true
      layer_spr_img(self.__id, img.__img, x or 0, y or 0)
    else
      return spr_tile(self, img, x, y, ...)
    end
  end
  function make_layer(w, h)
    local id = layer_new(w, h)
    return setmt({ __id = id, __c = layer_canvas(), W = w, H = h }, Layer)
  end
  function draw_layer(l, cx, cy)
    layer_blit(l.__c, cx or 0, cy or 0, l.__e)
    l.__e = nil
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
  -- Python rows carry. The scene texts are the run's, parsed in C
  -- (native/moycore/moycore_scene.h's __scene_rows), and the live world is
  -- these tables: actors()/touching()/move_actor()/remove_actor() are Lua over
  -- it and draw_scene() is C over it, so no row crosses into Python.
  --
  -- The rules are widgets.Scenes' and SceneWorld's: scene(name) answers a
  -- named scene's rows without switching the active one, each scene parsed
  -- once a run and its rows shared (a fresh sequence each call); the world is
  -- copies of the ACTIVE scene's rows, made at its first use. A run holds at
  -- most 256 live actors (docs/kernel_cartpath_2026-10.md §9 decision 5).
  local scene_names, scene_rows, draw_rows = __scene_names, __scene_rows, __draw_scene
  __scene_names, __scene_rows, __draw_scene = nil, nil, nil
  local floor, tremove, type, pairs = math.floor, table.remove, type, pairs
  local names = scene_names()
  local known = {}
  for i = 1, #names do known[names[i]] = true end
  local active = names[1]
  local parsed = {}

  local function rows(n)
    local got = parsed[n]
    if got == nil then
      got = scene_rows(n)
      parsed[n] = got
    end
    return got
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

  function scene(name)
    local n = name
    if n == nil then n = active end
    if n == nil then return {} end
    return snapshot(rows(n))
  end

  function load_scene(name)
    if name ~= nil and known[name] then
      active = name
      return snapshot(rows(name))
    end
    return {}
  end

  local live = nil

  local function world()
    if live == nil then
      local src = active ~= nil and rows(active) or {}
      if #src > 256 then
        error("actors: the scene holds " .. #src .. " actors, past a run's 256", 3)
      end
      live = {}
      for i = 1, #src do
        local r = src[i]
        local fl = {}
        if type(r.flags) == "table" then
          for k, v in pairs(r.flags) do fl[k] = v end
        end
        live[i] = { tag = r.tag, tile = r.tile, x = r.x, y = r.y, flip = r.flip,
                    flags = fl }
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

  -- int() truncates toward zero, as SceneWorld's move does.
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
        return
      end
    end
  end

  function draw_scene()
    draw_rows(world())
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
PRELUDE_HANDLES = (PRELUDE_HANDLES.replace(
    "@LAYER_VERBS@", ", ".join('"%s"' % v for v in LAYER_VERBS))
    + PRELUDE_EDITOR)

_LUA_PRELUDE = PRELUDE_HANDLES + PRELUDE_FASTMATH


def put_scenes(ns, put):
    """Hand the cart's scene texts to a run that parses them in C
    (native/moycore/moycore_scene.h), in the order the namespace's Scenes keeps,
    so the first is the default active scene. The Scenes object carries the
    Editor's live, unsaved placement (Scenes.put). Before the prelude."""
    scenes = ns.get("_moy_scenes") if hasattr(ns, "get") else None
    if scenes is None:
        return
    for name in scenes.names:
        text = scenes.raw(name)
        if isinstance(text, (str, bytes)):
            put(name, text)


def install_handles(ns, reg):
    """Register the int-handle half of PRELUDE_HANDLES that is Python's: the
    editor handle (#112), for a cart that earned `open_editor`. Return the
    list that PINS the run's editors for its lifetime; drop it at close.

    `reg` is the runtime's own register verb (`moycore.register`). The
    layers, paint images and scenes are the run's C on every tier
    (native/moycore/moycore_lua.c, moycore_scene.h): nothing of them is
    registered."""
    pins = []
    _install_editor(ns, reg, pins)
    return pins


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
