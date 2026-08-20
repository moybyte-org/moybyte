"""Browser input decode -- the transport-neutral half of the old web_view.

This is what SURVIVED the recording stack (moycore stage 4). Until the wasm
head re-rastered, `web_view.py` held two unrelated things: the draw-command
recorder + wire protocol that shipped pixels to a JS replayer, and this -- the
decode that turns a browser event batch into console input. The first is
deleted; the second is transport-shaped, not raster-shaped, and both the wasm
head and the plan's 3.4 sync/controller RPC speak this same `{"events":[...]}`
format, so it keeps its own module rather than dying with its old neighbours.

Moved VERBATIM, hard-won edge cases and all: the hover-without-button rule, the
Backspace-is-also-HOME mapping and its text-mode exemption, the held-key latch
that stops browser autorepeat flapping key() at 60fps, and the desktop
hold-Backspace-to-exit gesture.
"""

import json

# The console's button set, as the wire names them.
BUTTON_NAMES = ("left", "right", "up", "down", "a", "b", "run", "home")


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
                 on_key=None, on_esc=None, on_hold=None, on_key_hold=None):
    """Inject a batch of browser events into an InputState + Pointer. Each event is fully
    guarded (a malformed one is skipped, never raised) so a buggy client can't crash the loop.

      {"type":"down","x":..,"y":..}  -> pointer tap (place + click + down)
      {"type":"move","x":..,"y":..}  -> pointer drag (place, down, no tap)
      {"type":"up"}                  -> release (pointer up)
      {"type":"pan","dx":..,"dy":..} -> trackball nudge (on_pan)
      {"type":"press","name":..}     -> one-shot button press (on_press)
      {"type":"hold","name":..,"down":bool} -> held button (on_hold, else input.set_button)
      {"type":"key","code":<ascii>}  -> typed key (on_key)
      {"type":"esc"}                 -> close panel (on_esc)
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
                # POINTER POSITION WITHOUT A BUTTON (2026-07-31). The shell has
                # real hover feedback -- the desk icon highlight (_lhover), the
                # cards grid's msel -- and the browser only ever reported the
                # pointer while a button was down, so on the web the whole shell
                # looked dead under the cursor. (This note used to add "it works
                # in the pygame sim because that loop reads the mouse every
                # frame". It did not: that loop only placed the pointer on
                # button-down, and got its own ConsoleDriver.hover on
                # 2026-08-14.) Deliberately NOT
                # "move": that one asserts `down`, which would fake a drag out
                # of an idle mouse (drag-scrolling grids, moving windows).
                pointer.place(int(ev.get("x", 0)), int(ev.get("y", 0)))
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
                    if on_hold is not None:
                        on_hold(name, bool(ev.get("down")))
                    else:
                        input.set_button(name, bool(ev.get("down")))
            elif t == "key":
                code = ev.get("code")
                if isinstance(code, int) and 0 <= code <= 0xFF and on_key is not None:
                    on_key(code)
                    # ONE console key on the web too: outside text mode a browser
                    # Backspace also fires the HOME button (Stage 5: HOME is the EXIT
                    # key -- a single edge the running cart reads; the ☰ button, wired
                    # as a HELD "home" below, is the hold-to-exit gesture for games),
                    # mirroring the physical key -- the raw-matrix path likewise
                    # reports last_key=0x08 AND the home button. In text mode it stays
                    # a typed 0x08 only (DELETE for a tool -- zero special-casing).
                    if (code == 0x08 and on_press is not None
                            and not getattr(input, "text_mode", False)):
                        on_press("home")
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
            elif t == "bshold":
                # Desktop-keyboard hold-to-exit: a physically HELD Backspace
                # streams the same sustained "home" the touch burger button
                # provides, so the console's ~700ms hold-to-exit gesture works
                # from a desktop browser (it previously had NO game-exit path
                # -- the burger row is media-query hidden there). Gated on
                # text_mode SERVER-side: a text cart's delete autorepeat must
                # never arm the exit; the release always passes so leaving
                # text mode can't strand a held home.
                down = bool(ev.get("down"))
                if down and getattr(input, "text_mode", False):
                    pass
                elif on_hold is not None:
                    on_hold("home", down)
                else:
                    input.set_button("home", down)
            elif t == "esc":
                if on_esc is not None:
                    on_esc()
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