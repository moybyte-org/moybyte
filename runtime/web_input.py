"""Browser input decode: the batch a browser event stream ships becomes
console input.

Transport-shaped, not raster-shaped: both the wasm head and the plan's 3.4
sync/controller RPC speak this same `{"events":[...]}` format, so the decode
is its own module with no draw path beside it.

The edge cases it carries: the hover-without-button rule and the held-key
latch that stops browser autorepeat flapping key() at 60fps. Backspace is a
plain typed 0x08; the console's key is the `super` event.
"""

import json

# `runtime` first, NOT the usual device-name-first ladder: there are TWO input
# modules and `input` is the frozen name of BOTH (the boards' 15-name
# moybyte.input outranks this one on the semantic-trace harness's sys.path).
# web_input decodes into the HOST class, so it must name it unambiguously; the
# bare-name branch is for the wasm head, which stages runtime/input.py as `input`
# and has no `runtime` package.
try:
    from runtime.input import InputState
except ImportError:  # pragma: no cover - wasm head (frozen as `input`)
    from input import InputState

# The console's button set, as the wire names them.
BUTTON_NAMES = InputState.BUTTONS
# The input source a page's on-screen gamepad writes (a hold's "src"); every
# other hold is the page keyboard's, the table's "local".
PAD_SOURCE = "pad"


def apply_ws_text(payload, apply):
    """Decode one inbound WS text payload ({"events":[...]}) and feed the event list
    to `apply` -- the ONE input-decode path every WS consumer shares (the wasm
    head's worker pump today; the 3.4 controller role rides the same format), so
    the wire can't drift. A malformed message just yields no input (never raises)."""
    try:
        data = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload
        obj = json.loads(data)
        events = obj.get("events", []) if isinstance(obj, dict) else obj
        if isinstance(events, list):
            apply(events)
    except Exception:  # noqa: BLE001 -- a bad message just yields no input
        pass


def apply_events(events, input, pointer, on_press=None, on_pan=None,
                 on_key=None, on_esc=None, on_hold=None, on_key_hold=None,
                 on_super=None, on_full=None):
    """Inject a batch of browser events into an InputState + Pointer. Each event is fully
    guarded (a malformed one is skipped, never raised) so a buggy client can't crash the loop.

      {"type":"down","x":..,"y":..}  -> pointer tap (place + click + down)
      {"type":"move","x":..,"y":..}  -> pointer drag (place, down, no tap)
      {"type":"up"}                  -> release (pointer up)
      {"type":"pan","dx":..,"dy":..} -> trackball nudge (on_pan)
      {"type":"press","name":..}     -> one-shot button press (on_press)
      {"type":"hold","name":..,"down":bool} -> held button (on_hold, else input.set_button)
      {"type":"hold",...,"src":"pad"} -> the same from the page's gamepad,
                                        written to the "pad" source
      {"type":"key","code":<ascii>}  -> typed key (on_key)
      {"type":"esc"}                 -> close panel (on_esc)
      {"type":"full"}                -> F11 (on_full): a desk window full
                                        screen, and back
      {"type":"super"}               -> the system key (on_super): the system
                                        menu, or the pause menu in play
    """
    for ev in events:
        try:
            t = ev.get("type")
            if t == "down":
                pointer.place(int(ev.get("x", 0)), int(ev.get("y", 0)))
                pointer.down = True
                pointer.click = True
            elif t == "move":
                pointer.place(int(ev.get("x", 0)), int(ev.get("y", 0)))
                pointer.down = True
            elif t == "hover":
                # POINTER POSITION WITHOUT A BUTTON: the shell has real hover
                # feedback (the desk icon highlight `_lhover`, the cards grid's
                # msel), so a browser mouse reports its position between
                # clicks too, as ConsoleDriver.hover does for the sim.
                # Deliberately NOT "move": that one asserts `down`, which would
                # fake a drag out of an idle mouse (drag-scrolling grids, moving
                # windows).
                pointer.place(int(ev.get("x", 0)), int(ev.get("y", 0)))
                pointer.hovers = True   # a browser mouse has a position always
            elif t == "up":
                pointer.down = False
            elif t == "pan":
                if on_pan is not None:
                    on_pan(int(ev.get("dx", 0)), int(ev.get("dy", 0)))
            elif t == "press":
                name = ev.get("name")
                if name in BUTTON_NAMES and on_press is not None:
                    on_press(name)
            elif t == "hold":
                name = ev.get("name")
                if name in BUTTON_NAMES:
                    # Route to on_hold when wired (the device's keyboard.poll clears buttons,
                    # so a hold must be re-asserted AFTER the poll); else a direct set.
                    # A hold from the page's gamepad names its source, so the pad,
                    # not the keyboard, becomes the input in use.
                    down = bool(ev.get("down"))
                    src = PAD_SOURCE if ev.get("src") == PAD_SOURCE else None
                    if on_hold is not None:
                        if src is None:
                            on_hold(name, down)
                        else:
                            on_hold(name, down, src)
                    elif src is not None and hasattr(input, "source"):
                        input.source(src).set_button(name, down)
                    else:
                        input.set_button(name, down)
            elif t == "key":
                code = ev.get("code")
                if isinstance(code, int) and 0 <= code <= 0xFF and on_key is not None:
                    on_key(code)
            elif t == "khold":
                # A physically HELD printable key (browser keydown/keyup edges):
                # outside text mode the driver latches it so key() streams every
                # frame, matching the device raw matrix -- browser autorepeat is
                # ~30Hz and made key() flap code/0/code at 60fps. Text mode
                # ignores the down (typing is the queued-byte path) but always
                # takes the release, so leaving text mode can't strand a latch.
                down = bool(ev.get("down"))
                if down and getattr(input, "text_mode", False):
                    pass
                elif on_key_hold is not None:
                    on_key_hold(ev.get("code"), down)
            elif t == "esc":
                if on_esc is not None:
                    on_esc()
            elif t == "super":
                if on_super is not None:
                    on_super()
            elif t == "full":
                if on_full is not None:
                    on_full()
        except Exception:  # noqa: BLE001 -- one bad event must not drop the batch
            pass


def effective_input_kinds(ws):
    """The manifest input hint (#42 Thread 3), but only while the RUNNING cart
    actually owns the keyboard (wm.keys_to_cart: fullscreen play / the focused
    game window). Any system surface -- Editor, launcher, Settings -- needs
    the FULL control set: the Code tab is exactly where the phone's soft-
    keyboard summon matters, and gating it by the open cart's hint hid the
    ⌨ button the moment a buttons-only game was opened for CHANGE. Shared by
    both web transports so host and device can't drift."""
    try:
        if ws is not None and ws.wm.keys_to_cart():
            cart = getattr(ws, "cart", None)
            return cart.get("input") if cart else None
    except Exception:  # noqa: BLE001 -- a hint failure just shows every control
        pass
    return None