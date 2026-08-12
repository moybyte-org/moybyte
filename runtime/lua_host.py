"""Host Lua cart runtime (#67 Phase 2/3): run a "runtime": "lua" cart over lupa.

`LuaCartRun(ns, src)` builds one Lua 5.4 state per running cart, registers the
cart's make_api namespace (the SAME dict a Python cart would exec under) as the
Lua globals -- lupa makes the Python callables directly callable -- executes the
cart source, and exposes `.init/.update/.draw` (Lua functions, callable from
Python) plus `.close()`. Player._start_lua drives it; a Lua error raises
lupa.LuaError, a normal Python exception, so the Player's existing panel
routing needs nothing special.

The PRELUDE adapts the three API entries whose Python shape doesn't map 1:1:
  * touch()      -> MULTIPLE VALUES (x, y, tapped, held) or nil, instead of a
                    tuple. No per-call table = no per-frame garbage, which is
                    the Lua tier's whole GC argument applied to the API shape.
  * make_layer() -> wraps the Python _Layer so `lay:spr(img, x, y)` colon calls
                    work (a colon call passes self, which a BOUND Python method
                    must not receive).
  * draw_layer() -> unwraps that wrapper back to the _Layer.
It then strips the stdlib to the device plan's sandbox (base + math + string +
table -- no io/os/load/require/debug) and removes lupa's `python` escape hatch,
so a lua cart is sandboxed like a python cart (portable-subset ethos).

HOST-ONLY by design: never staged/frozen into a firmware build (the device
equivalent is the moy_lua native module, #67 Phase 1). Import requires lupa;
host_app probes availability and injects `make_lua_runtime` iff it's there.

Canonical home is runtime/; tests import it as runtime.lua_host.
"""

PRELUDE = """
do
  local py_touch = touch
  touch = function()
    local t = py_touch()
    if t == nil then return nil end
    -- t is the Python tuple (x, y, tapped, held): 0-based via lupa indexing
    return t[0], t[1], t[2], t[3]
  end

  local py_make_layer = make_layer
  local py_draw_layer = draw_layer
  local function wrap_layer(pyl)
    -- Colon-call adapter: lay:spr(...) => shim(lay, ...) => pyl.spr(...) with
    -- the Lua self dropped (pyl.spr is already bound). Verbs resolve lazily and
    -- cache; plain data attributes (W/H) pass straight through.
    local w = { __py = pyl, W = pyl.W, H = pyl.H }
    return setmetatable(w, { __index = function(self, k)
      local v = pyl[k]
      if type(v) == "userdata" then
        local shim = function(_, ...) return v(...) end
        rawset(self, k, shim)
        return shim
      end
      return v
    end })
  end
  make_layer = function(w, h) return wrap_layer(py_make_layer(w, h)) end
  draw_layer = function(l, cx, cy)
    if type(l) == "table" and l.__py ~= nil then l = l.__py end
    py_draw_layer(l, cx or 0, cy or 0)
  end

  -- The #78 `table()` cart verb collides with Lua's `table` LIBRARY (the one
  -- stdlib name the kid API shares; found via celeste's p8 shim calling
  -- table.remove on the injected Python closure, #164). The library stays the
  -- global -- portable lua (table.insert/remove/...) keeps working -- and a
  -- metatable __call makes it double as the verb: table("scores") -> rows.
  if moy_table_verb ~= nil then
    local tv = moy_table_verb
    setmetatable(table, { __call = function(_, name) return tv(name) end })
    moy_table_verb = nil
  end

  -- Sandbox: the device plan's "safe stdlib only" (base/math/string/table),
  -- mirrored on the host so a cart that runs here runs there. `python` is
  -- lupa's Python bridge -- the one escape hatch a kid cart must not have.
  io = nil
  os = nil
  package = nil
  require = nil
  dofile = nil
  loadfile = nil
  load = nil
  debug = nil
  python = nil
  -- lupa's luaL_openlibs opens these three; the device VM never does (moy_lua
  -- opens only base/math/string/table, strips collectgarbage, and its build
  -- drops lcorolib.c/lutf8lib.c from the sources outright). Nil them so a cart
  -- that runs here runs on glass -- SPEC.md 4.1's stdlib list is a MAXIMUM, and
  -- utf8 is the one lupa leaks that the device physically cannot provide.
  coroutine = nil
  collectgarbage = nil
  utf8 = nil
end
"""


class LuaCartRun:
    """One running lua cart: its lua_State + the captured cart verbs."""

    def __init__(self, ns, src):
        try:
            from lupa import lua54
            self._lua = lua54.LuaRuntime(register_eval=False,
                                         register_builtins=False)
        except ImportError:  # pragma: no cover - older lupa wheels
            import lupa
            self._lua = lupa.LuaRuntime(register_eval=False,
                                        register_builtins=False)
        g = self._lua.globals()
        for k, v in ns.items():
            if k == "table":
                # Never clobber Lua's `table` library (#164): the prelude
                # grafts the #78 verb onto it as a metatable __call instead.
                g["moy_table_verb"] = v
                continue
            g[k] = v
        # Captured BEFORE the prelude's sandbox nils `load`: the cart chunk is
        # loaded as "@cart" so every error position renders `cart:12:` -- the
        # same chunkname the device passes to moy_lua.exec, which is what
        # player._lua_cart_line parses for the drop-on-the-bad-line panel (#24).
        loadstring = self._lua.eval("load")
        self._lua.execute(PRELUDE)
        chunk = loadstring(src, "@cart")
        if isinstance(chunk, tuple):     # (nil, errmsg): a load/syntax error
            from lupa import LuaError
            raise LuaError(chunk[1])     # errmsg already carries `cart:N:`
        chunk()
        # Captured post-exec, like the Python path's ns.get("_update"): a cart
        # may define any subset; missing verbs just don't run.
        self.init = g["_init"]
        self.update = g["_update"]
        self.draw = g["_draw"]

    def close(self):
        """Drop the state; the interpreter (and the cart's whole Lua heap) is
        collected with it. Mirrors the device moy_lua close() contract."""
        self.init = None
        self.update = None
        self.draw = None
        self._lua = None


def make_lua_runtime(ns, src):
    """The ws.lua_runtime factory shape Player._start_lua expects."""
    return LuaCartRun(ns, src)


# ---------------------------------------------------------------------------
# The moycore lane (plan rung 4): the boards' OWN Lua, not lupa's.
#
# lupa is a second embedding with second semantics -- 64-bit doubles where both
# boards build LUA_32BITS -- so a float-heavy cart can agree with the goldens
# here and disagree on glass, and an integer that wraps at 2^31 there does not
# wrap here. `runtime/lua_binding.py` is the same C the boards run; this routes
# a cart to it when it can.
#
# WHEN IT CAN: libmoy binds the SPEC verb table, and moybyte's cart API is a
# superset (layers/images, scenes, tables, texts, view). A cart using one of
# those keeps lupa, which supplies the whole namespace through the trampoline
# -- correct, and the slower of two correct paths. The same split moycore_glue
# makes on the device, for the same reason and by the same source scan.
# ---------------------------------------------------------------------------

def _moycore_available():
    try:
        from runtime.lua_binding import HostLuaRun
    except ImportError:                     # pragma: no cover
        try:
            from lua_binding import HostLuaRun
        except ImportError:
            return None
    return HostLuaRun if HostLuaRun.available() else None


class MoycoreHostRun:
    """A cart run under the boards' Lua. Same shape lupa's run exposes."""

    def __init__(self, ws, ns, src):
        HostLuaRun = _moycore_available()
        if HostLuaRun is None:
            raise RuntimeError("no host lua binding")
        canvas = ws.canvas
        project = getattr(ws, "project", None)
        self._run = HostLuaRun(canvas.buf, canvas.w, canvas.h,
                               getattr(project, "sheet", None),
                               getattr(project, "tilemap", None))
        err = self._run.load(src, "@cart")
        if err:
            self._run.close()
            raise RuntimeError(err)
        self._ns = ns
        self._ws = ws
        self.init = None                    # load() already ran it
        self.update = self._update
        self.draw = None                    # tick drew; see _update

    def _update(self, dt):
        s = self._run.snap
        inp = self._ws.input
        held = pressed = 0
        from runtime.lua_binding import (SNAP_BTN, SNAP_BTNP, AQ_SFX, AQ_MUSIC,
                                         AQ_BEEP, AQ_MUSIC_STOP,
                                         AQ_SOUND_STOP, AQ_VOLUME)
        for i, name in enumerate(("left", "right", "up", "down",
                                  "a", "b", "run", "home")):
            try:
                if inp.held(name):
                    held |= 1 << i
                if inp.pressed(name):
                    pressed |= 1 << i
            except Exception:  # noqa: BLE001
                pass
        s[SNAP_BTN], s[SNAP_BTNP] = held, pressed
        err = self._run.tick(dt)
        # Audio drains through the SAME api closures a Python cart uses, so the
        # engine's behaviour lives in one place; only the per-call trip is gone.
        for op, a, b, _c in self._run.audio():
            try:
                if op == AQ_SFX:
                    self._ns["sfx"](a, None if b < 0 else b)
                elif op == AQ_MUSIC:
                    self._ns["music"](a, bool(b))
                elif op == AQ_BEEP:
                    self._ns["beep"](a, b / 1000.0)
                elif op == AQ_MUSIC_STOP:
                    self._ns["music_stop"]()
                elif op == AQ_SOUND_STOP:
                    self._ns["sound_stop"](None if a < 0 else a)
                elif op == AQ_VOLUME:
                    self._ns["volume"](a)
            except Exception:  # noqa: BLE001 -- one bad command is not the frame
                pass
        if err:
            raise RuntimeError(err)

    def close(self):
        self._run.close()


# The verbs libmoy does not bind; a cart naming one keeps lupa. Kept beside the
# device list (moycore_glue.SUPERSET) rather than imported from it: this file
# is host-only and that one is staged into firmware trees.
SUPERSET = ("make_layer", "draw_layer", "image", "scene", "load_scene",
            "actors", "touching", "move_actor", "move_actor_to",
            "remove_actor", "draw_scene", "table", "text", "view",
            "spr_batch", "rect_batch", "spans", "mouse", "background",
            "col", "on_net", "wifi", "net")



# A verb is a CALL, so that is what this looks for: the name at a word
# boundary followed by "(". The first cut of this was a plain substring
# search, which sounded conservative and was in fact a gate that never
# opened -- `table.insert`, a variable named `col`, or the letters "net"
# inside any identifier disqualified every cart in the tree. Erring toward
# the old path is right; erring so far that the new one is unreachable is
# a silent no-op, which is worse than either.
def _uses(src, names):
    """The first superset verb `src` CALLS, or None.

    Scans by hand rather than by regex: MicroPython's `re` has no lookbehind,
    and this predicate has a device twin (moycore_glue._calls) that must agree
    with it -- a host gate that opens where the board's does not is worse than
    either being strict. The version before this one was a plain substring
    test, which disqualified every cart in the tree.
    """
    for name in names:
        n = len(name)
        i = src.find(name)
        while i >= 0:
            before = src[i - 1] if i else " "
            j = i + n
            while j < len(src) and src[j] == " ":
                j += 1
            if (j < len(src) and src[j] == "("
                    and not (before.isalnum() or before in "_.:")):
                return name
            i = src.find(name, i + 1)
    return None


def moycore_supports(src):
    """True when this cart stays inside the spec verb table."""
    if _moycore_available() is None:
        return False
    return _uses(src, SUPERSET) is None
