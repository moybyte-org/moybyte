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

# The prelude and the int-handle registry live in runtime/lua_ext.py -- ONE
# definition for every Lua runtime in the tree (this one, moycore on both
# boards and the browser, and the host's ctypes binding). Imported rather than
# copied for the reason recorded there: the copy that did not exist is why the
# host crashed on layer carts while the device merely declined them.
try:
    from lua_ext import (PRELUDE_TABLE, PRELUDE_HANDLES, PRELUDE_FASTMATH,
                         install_handles)
except ImportError:                      # host tests importing the device module
    from runtime.lua_ext import (PRELUDE_TABLE, PRELUDE_HANDLES,
                                 PRELUDE_FASTMATH, install_handles)

_LUA_PRELUDE = PRELUDE_TABLE + PRELUDE_HANDLES + PRELUDE_FASTMATH


class LuaCartRun:
    """One running lua cart: the moy_lua VM + captured cart verbs (the
    ws.lua_runtime handle shape Player._start_lua drives: .init/.update/.draw
    callables-or-None + .close())."""

    def __init__(self, ws, ns, src):
        import moy_lua
        self._moy_lua = moy_lua
        canvas = ws.canvas
        sheet = ws.project.sheet if ws.project is not None else None
        # A canvas without a writable batch array (the wasm head's recording
        # CommandCanvas, or no open sheet) declines the C fast path. (The old
        # `_r`-sniff that declined the device web view's TeeCanvas died with
        # that class in the 2026-08 streaming sunset -- no surviving canvas
        # both records and forwards _batch_arr.)
        arr = getattr(canvas, "_batch_arr", None)
        direct = arr is not None and sheet is not None
        if not direct:
            # Bind a dummy so init() succeeds, then the Python spr closure
            # replaces the C fast path below -- the deliberate slow lane,
            # still correct.
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
            # #189 libmoy-direct draw verbs: hand the canvas's DrawCtx to the
            # VM so pix/rect/.../print become lua_CFunctions that never enter
            # Python. MUST run after the register loop (each verb's trampoline
            # becomes the odd-shape fallback) and before exec(src) (the p8
            # shim captures the globals into locals at load). A no-op (returns
            # False) on builds without moy_gfx (wasm) or canvases without
            # gates -- the wasm head's recording CommandCanvas has no
            # _gate_ctx, so it stays on the trampolines by construction.
            ctx = getattr(canvas, "_gate_ctx", None)
            bd = getattr(moy_lua, "bind_draw", None)
            if ctx is not None and bd is not None:
                bound = bd(ctx)
                # #67 stage-1 (moycore): register the cart's indexed sheet
                # + palt with the ctx so the sprite-batch protocol (run
                # breaks + the #63 order-rule flush) runs entirely in C --
                # the begin_batch/flush_batch upcalls die for this run.
                # _lua_batch_sheet is the Python flush's fallback for a
                # C-stamped run (see DeviceCanvas.flush_batch). A refusal
                # (odd sheet shape, older moy_gfx) leaves the upcall
                # protocol standing -- correct, just slower.
                sbs = getattr(ctx, "set_batch_src", None)
                if bound and direct and sbs is not None:
                    if getattr(canvas, "_palt", None) is None:
                        canvas.reset_state()
                    try:
                        sbs(sheet.pix, sheet.w, sheet.h,
                            getattr(canvas, "_palt", None))
                        canvas._lua_batch_sheet = sheet
                        canvas._lua_batch_token = _LUA_TOKEN
                        self._canvas = canvas
                        # stage-1b: the tilemap too, so tline goes direct.
                        # Registered only under a live batch source (the
                        # direct sspr/tline gate on both); a refusal keeps
                        # those verbs on their trampolines.
                        sms = getattr(ctx, "set_map_src", None)
                        tilemap = (ws.project.tilemap
                                   if ws.project is not None else None)
                        if sms is not None and tilemap is not None:
                            try:
                                sms(tilemap.cells, tilemap.w, tilemap.h)
                            except (ValueError, TypeError):
                                pass
                    except (ValueError, TypeError):
                        pass
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
            try:
                self.close()              # un-register the batch source AND
            except Exception:             # close: a broken load never strands
                moy_lua.close()           # a VM or the canvas registration
            raise

    def _install_handles(self, ns):
        self._layers, self._images = install_handles(ns, self._moy_lua.register)

    def close(self):
        self.init = None
        self.update = None
        self.draw = None
        self._layers = None
        self._images = None
        cv = getattr(self, "_canvas", None)
        if cv is not None:
            # Un-register the C batch source (#67 stage-1): the next run
            # re-registers its own sheet; a stale pending run's quads drop
            # defensively, exactly as a lost _batch_sheet always has.
            cv._lua_batch_sheet = None
            cv._lua_batch_token = -1
            ctx = getattr(cv, "_gate_ctx", None)
            for name in ("set_batch_src", "set_map_src"):
                fn = getattr(ctx, name, None) if ctx is not None else None
                if fn is not None:
                    try:
                        fn(None)
                    except Exception:  # noqa: BLE001 -- close must never block an exit
                        pass
            self._canvas = None
        try:
            self._moy_lua.close()
        except Exception:  # noqa: BLE001 -- close must never block an exit
            pass


def make_lua_runtime(ws):
    """The ws.lua_runtime factory (Player._start_lua's seam), bound to `ws`."""
    def make(ns, src):
        return LuaCartRun(ws, ns, src)
    return make
