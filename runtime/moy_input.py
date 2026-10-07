"""Input as the kernel's native `moy_input` will expose it: its Python twin
(docs/kernel_survival_2026-10.md section 4.1).

  Pointer          a screen-space cursor: trackball-relative, touch-absolute
  pointer_state    the pointer a CART sees, one answer for every tier
"""

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff


CURSOR_IDLE_MS = 2000  # hide the trackball cursor after this long with no movement
# How long a touchscreen pointer OUTLIVES the finger that made it. A touch
# panel reports a position only while it is being touched, and a cart wants a
# MOUSE: hold and drag, then let go and the pointer is still where you left it
# for a moment before it is gone. Without the linger a released finger reads as
# "no pointer" on the very next frame, which is not a mouse and is not what a
# cart's cursor, drag handle or hover highlight is written against.
POINTER_LINGER_MS = 1500


class Pointer:
    """A screen-space cursor. The trackball drives it relatively (and shows it);
    touch places it absolutely (finger is the pointer, so it stays hidden). The
    cursor auto-hides after CURSOR_IDLE_MS without trackball movement."""

    def __init__(self, w, h, idle_ms=CURSOR_IDLE_MS):
        self.w = w
        self.h = h
        self.x = w // 2
        self.y = h // 2
        self.click = False
        self.down = False         # touch/button currently held (for drag gestures)
        # Does this pointer's SOURCE report a position with no button down? A
        # mouse does (host, browser) and a touch panel does not, and that is
        # the whole of the difference: a hovering pointer never expires, a
        # touched one lingers (POINTER_LINGER_MS) and then reads as absent.
        # Set by whoever feeds a hover, so a console inherits its own answer
        # rather than being told which kind of machine it is.
        self.hovers = False
        self._sampled = _ticks_ms()
        # Did THIS frame's sample come from the input hardware, or is it a repeat
        # of the last one? A mouse always reports a level, so the host never sets
        # this False; the T-Deck's GT911 hands over ~20-30 samples/s while a
        # finger drags (it clock-stretches 20-45ms on most finger-down reads,
        # #74), which is well under the frame rate -- so its backend holds the
        # last point and marks the repeats stale. Kinetic scrolling (#113) reads
        # it: a repeat carries NO new information about finger speed, so charging
        # it a zero delta would decay the fling velocity toward nothing.
        self.fresh = True
        self.visible = True
        self.idle_ms = idle_ms
        self._last_move = _ticks_ms()

    def move(self, dx, dy):
        # Relative move from the trackball: clamp, and wake the cursor.
        self.x = max(0, min(self.w - 1, self.x + dx))
        self.y = max(0, min(self.h - 1, self.y + dy))
        self.visible = True
        self._last_move = self._sampled = _ticks_ms()

    def place(self, x, y):
        # Absolute position from touch: hit-test there, but keep the cursor
        # hidden (the finger already shows where you are).
        self.x = max(0, min(self.w - 1, x))
        self.y = max(0, min(self.h - 1, y))
        self.visible = False
        self._sampled = _ticks_ms()

    def live(self):
        """Is there a pointer for a CART to read right now?

        Held is live, hovered is live, and a just-released touch stays live for
        POINTER_LINGER_MS so a drag that ends does not teleport the cart's
        cursor into nowhere on the next frame. Past that a touch console
        honestly has no pointer, which is what `touch()` answers None/nil for.
        """
        return (self.down or self.hovers
                or _ticks_diff(_ticks_ms(), self._sampled) < POINTER_LINGER_MS)

    def tick(self, now):
        # Auto-hide once the trackball has been idle long enough.
        if self.visible and _ticks_diff(now, self._last_move) >= self.idle_ms:
            self.visible = False


# The pointer a CART sees, resolved once for every tier.
#
# Python carts reach it through cart_api's `touch()`; Lua carts reach it through
# the snapshot the glues fill (device/moycore_glue.py, runtime/lua_host.py) and
# libmoy's `touch()` reads. Those are two code paths and they must not be two
# ANSWERS -- "one cart, every tier" is the whole contract, and a pointer that
# expires on one tier and not the other breaks it silently, in a cart that draws
# a cursor.
#
# ONE INTEGER, because the Lua snapshot has one slot to say all three things in
# (see moycore_glue / moyhost_lua's h_touch). FLAGS and not a ladder: `click`
# and `down` are independent, not nested -- a scripted tap (host_api.click, the
# suites) raises the edge with the finger already lifted, and a ladder that
# assumed down >= click swallowed it, which `letter blitz` scores with.
P_NONE = 0
P_LIVE = 1                     # there is a pointer at all
P_HELD = 2                     # the finger/button is down this frame
P_CLICK = 4                    # ...and it went down THIS frame (the press edge)


def pointer_state(inp, out):
    """Fill `out` as `[x, y, state, ms]` and return it. `state` is P_*.

    `ms` is 0 and the slot is vestigial: it carried a press duration only so
    h_touch could read `held` out of it, and `held` is a flag now. The slot
    stays because it is in the C snapshot ABI, not because anything reads it.

    `out` is CALLER-OWNED and reused: this runs once per frame on the play
    path, and a fresh tuple here is an allocation the S3 charges most of a
    millisecond for when the collector comes round (#66).
    """
    out[2] = P_NONE
    # A LINKED MATCH HAS NO POINTER. Only buttons cross the radio, so a touch
    # read here would move this screen's player and not the other one's -- a
    # divergence the lockstep exchange cannot see and cannot heal, the same
    # class of bug as drawing from the shared random stream. Reporting "no
    # pointer" makes a touch-driven cart fall back to its button path, which is
    # the honest answer while two consoles share one game.
    if getattr(inp, "netplay_live", False):
        return out
    p = getattr(inp, "pointer", None)
    if p is None:
        return out
    # LIVENESS IS THE POINTER'S, always, and asking it FIRST is the whole point.
    # A touch panel reports a position only while it is touched; the pointer
    # outlives the finger by POINTER_LINGER_MS so a released drag does not
    # teleport a cart's cursor into nowhere, and a hovering source never
    # expires at all. The game-space publication below is a coordinate MAPPING
    # of this pointer, not a second opinion about whether there is one --
    # console.py republishes it with a position every frame whether or not the
    # pointer is alive, so reading liveness off it left a p8 cart holding a
    # cursor over its board forever and its d-pad stamped over every frame.
    if hasattr(p, "live") and not p.live():   # hasattr: no bound method
        return out
    # Two-domain seam (#39): the game-space publication wins where the console
    # makes one (a distinct big system canvas, or a cart with a smaller
    # canvas), so a cart reads its own viewport coordinates rather than the
    # desktop's -- and its own tap/hold flags, which an overlay may have
    # stripped on the way through.
    gp = getattr(inp, "game_pointer", None)
    if gp is not None:
        out[0], out[1] = gp[0], gp[1]
        out[2] = (P_LIVE | (P_CLICK if bool(gp[2]) else 0)
                  | (P_HELD if (len(gp) > 3 and gp[3]) else 0))
        out[3] = 0
        return out
    out[0], out[1] = p.x, p.y
    out[2] = (P_LIVE | (P_HELD if getattr(p, "down", False) else 0)
              | (P_CLICK if getattr(p, "click", False) else 0))
    out[3] = 0
    return out
