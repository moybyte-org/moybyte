# Pin Light -- tap the button, the board's LED changes (#9).
#
# This cart is not "for the Zero". It is for ANY console that has pins, and it
# asks that question the only honest way: whether the verb has a name. The
# console injects pin_write/pin_read only when the thing serving this page
# answered the GPIO probe (runtime/cart_api.py), exactly like wifi and net --
# a verb that cannot work must not exist, because a pin_write that quietly does
# nothing is the worst answer a kid can get: the cart looks right and the light
# never moves.
#
# So: no pins, no crash. The cart says what is missing and stays usable.
try:
    pin_write
    HAS_PINS = True
except NameError:
    HAS_PINS = False

# The XIAO's own little light is GPIO 21 and it is ACTIVE-LOW -- confirmed by
# blinking it on a real board, 2026-08-30. So 0 is ON. `cfg("pin")` makes that
# editable in the cards editor, because the next board's LED will be elsewhere.
on = False
flash = 0.0


def _pin():
    return int(cfg("pin", 21))


def _apply():
    # ACTIVE-LOW: 0 lights it.
    if HAS_PINS:
        pin_write(_pin(), 0 if on else 1)


def _init():
    global on, flash
    on = False
    flash = 0.0
    _apply()


def _button():
    return (80, 96, 160, 64)


def _update(dt):
    global on, flash
    if flash > 0:
        flash = max(0.0, flash - dt)
    hit = btnp("a")
    tp = touch()
    if tp is not None and tp[2]:
        x, y, w, h = _button()
        if x <= tp[0] < x + w and y <= tp[1] < y + h:
            hit = True
    if hit:
        on = not on
        flash = 0.12
        _apply()


def _draw():
    cls("dark_blue")
    print("PIN LIGHT", 116, 24, "white")
    if not HAS_PINS:
        print("this console has no pins", 60, 104, "orange")
        print("open it from a board that does", 36, 120, "gray")
        return
    x, y, w, h = _button()
    face = "green" if on else "dark_gray"
    if flash > 0:
        face = "white"
    rect(x, y, w, h, face)
    rectb(x, y, w, h, "white")
    print("ON" if on else "OFF", x + (66 if on else 62), y + 28, "black" if on else "white")
    print("pin %d is %s" % (_pin(), "LOW (lit)" if on else "HIGH (dark)"),
          72, 184, "gray")
    print("tap it, or press A", 96, 204, "dark_gray")
