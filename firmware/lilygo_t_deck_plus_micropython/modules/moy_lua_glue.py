"""The moy_lua cart-runtime glue (#67), SHARED by every target that builds the
moy_lua native module: both boards (staged from this tree; the T-Deck freezes
modules/ wholesale, the P4 build.sh stages it by name) and the #151 web runner
(MicroPython-WASM -- its CommandCanvas has no _batch_arr, so LuaCartRun's
existing no-batch fallback registers the Python spr closure and every sprite
reaches the recorder). Extracted from device_api.py, which re-exports it, so
`device_api.LuaCartRun` / `device_api.make_lua_runtime` are unchanged for the
boards' moy_runtime wiring. Imports NOTHING device-specific: only `moy_lua`
(lazy) + `array`.
"""

# run_desktop wires ws.lua_runtime = make_lua_runtime(ws) IFF the moy_lua
# native module is in its build. One moy_lua VM per run: hot spr() appends
# quads to the canvas's _batch_arr from C (the moy_gfx spr_gate protocol, its
# own token), every other verb IS the registered Python make_api closure
# (semantic parity by construction), and layers/images never cross the VM
# boundary -- they live in a Python-side registry spoken to through int
# handles by the prelude wrappers. The cart's whole Lua heap sits in PSRAM
# OUTSIDE the gc heap, freed wholesale at close() (#66: cart churn can't
# fragment the console).

_LUA_TOKEN = 0x7A11   # the Lua writer's batch token: never 0 (the Python
                      # writer) and outside the spr_gate sequence (1..0x4000),
                      # so interleaved runs always break via begin_batch.

_LUA_PRELUDE = """
do
  -- #164: `table` stays the Lua LIBRARY (celeste's p8 shim needs
  -- table.remove); the #78 cart verb rides it as a metatable __call.
  if moy_table_verb ~= nil then
    local tv = moy_table_verb
    setmetatable(table, { __call = function(_, name) return tv(name) end })
    moy_table_verb = nil
  end
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


class LuaCartRun:
    """One running lua cart: the moy_lua VM + captured cart verbs (the
    ws.lua_runtime handle shape Player._start_lua drives: .init/.update/.draw
    callables-or-None + .close())."""

    def __init__(self, ws, ns, src):
        import moy_lua
        self._moy_lua = moy_lua
        canvas = ws.canvas
        sheet = ws.project.sheet if ws.project is not None else None
        # The web-view TeeCanvas __getattr__-forwards _batch_arr to the REAL
        # canvas, so a plain getattr would hand the C spr a bypass around the
        # recorder (the exact bug TeeCanvas.make_spr_gate shadows against --
        # sprites the browser never sees). Its `_r` recorder attr marks it:
        # decline the fast path there, like the gate does.
        is_tee = getattr(canvas, "_r", None) is not None
        arr = None if is_tee else getattr(canvas, "_batch_arr", None)
        direct = arr is not None and sheet is not None
        if not direct:
            # No writable batch array (web-view Tee / no sheet): bind a dummy
            # so init() succeeds, then the Python spr closure replaces the C
            # fast path below -- the deliberate slow lane, still correct.
            from array import array
            arr = array("h", bytearray(2 * 8))
        moy_lua.init(canvas, sheet, arr, _LUA_TOKEN)
        try:
            for name in ns:
                v = ns[name]
                if name == "table":
                    # Never clobber Lua's `table` LIBRARY (#164): the prelude
                    # grafts the #78 verb onto it as a metatable __call, so
                    # table.insert/remove (celeste's p8 shim) AND
                    # table("scores") both work. Host twin: lua_host.py.
                    moy_lua.register("moy_table_verb", v)
                    continue
                if name != "spr" and name != "Image" and callable(v):
                    moy_lua.register(name, v)
            moy_lua.exec("W=%d H=%d"
                         % (int(ns.get("W", 320)), int(ns.get("H", 240))), "glue")
            if not direct:
                moy_lua.register("spr", ns["spr"])
            self._install_handles(ns)
            moy_lua.exec(_LUA_PRELUDE, "prelude")
            # "@cart" so error positions render `cart:12:` -- the chunkname
            # player._lua_cart_line parses for the drop-on-the-bad-line panel
            # (#24), matching the host runner's loadstring(src, "@cart").
            moy_lua.exec(src, "@cart")
            self.init = ((lambda: moy_lua.call("_init"))
                         if moy_lua.has("_init") else None)
            self.update = ((lambda dt: moy_lua.call("_update", dt))
                           if moy_lua.has("_update") else None)
            self.draw = ((lambda: moy_lua.call("_draw"))
                         if moy_lua.has("_draw") else None)
        except Exception:
            moy_lua.close()               # a broken load never strands a VM
            raise

    def _install_handles(self, ns):
        # The object-valued API entries (layers, paint images) stay in these
        # Python-side registries; _LUA_PRELUDE's wrappers speak the int handles.
        # The registries also PIN the objects for the run's lifetime.
        layers = []
        images = []
        make_layer = ns.get("make_layer")
        draw_layer = ns.get("draw_layer")
        image = ns.get("image")
        reg = self._moy_lua.register

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
        self._layers = layers
        self._images = images

    def close(self):
        self.init = None
        self.update = None
        self.draw = None
        self._layers = None
        self._images = None
        try:
            self._moy_lua.close()
        except Exception:  # noqa: BLE001 -- close must never block an exit
            pass


def make_lua_runtime(ws):
    """The ws.lua_runtime factory (Player._start_lua's seam), bound to `ws`."""
    def make(ns, src):
        return LuaCartRun(ws, ns, src)
    return make
