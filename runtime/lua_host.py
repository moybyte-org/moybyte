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
            g[k] = v
        self._lua.execute(PRELUDE)
        self._lua.execute(src)
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
