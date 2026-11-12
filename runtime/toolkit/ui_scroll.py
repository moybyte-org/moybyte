# Map (grep -n a name to jump there):
#   drag_steps                        whole steps of drag travel, rounded toward zero
#   PaintRing                         what the last painted frames left of a region, per buffer
#   ScrollRegion                      the one scroll model
#   DragTap                           press/drag/release over a ScrollRegion
"""The toolkit's scrolling: the one scroll model (`ScrollRegion`, its motion
native/moy_ui's moy_ui_scroll_t), the press/drag/release gesture over it
(`DragTap`) and the painted-frame ring a scroll shifts out of (`PaintRing`,
docs/history/scroll_and_text_2026-10.md §3.1).

ui_draw re-exports every name here, so `ui_draw.ScrollRegion` and
`ui.py`'s use of it name these. The scrollbar draws in the look ui_draw
binds (`_look`), imported at the foot of this file: ui_draw imports this
module at its own foot, so either may load first.

MicroPython-safe, no per-frame allocation: the region's state is one
bytearray the C advances in place.
"""

from array import array

try:
    from struct import unpack_from as _unpack
except ImportError:  # pragma: no cover - a port with only ustruct
    from ustruct import unpack_from as _unpack

import moy_ui as _C


def drag_steps(d, step):
    """The whole `step`s in `d` pixels of drag travel, rounded toward zero
    so a finger moved a pixel either way scrolls nothing. A drag that
    carries its remainder advances its anchor by `drag_steps(d, step) * step`."""
    return d // step if d >= 0 else -(-d // step)


# The largest physical-buffer rotation any canvas has (host 1, device
# ping-pong 2, the P4's triple framebuffer 3 -- shipped, efcf5d1). A paint
# ring keeps this many entries so it can vouch for RETAINED_FRAMES
# consecutive paints on ANY tier; raise it if a canvas ever rotates more
# buffers.
_MAX_RETAINED = 3


class PaintRing:
    """What the last painted frames left of one region in the buffers a
    canvas presents in turn (docs/history/scroll_and_text_2026-10.md §3.1): a slot
    per painted frame -- its number, the canvas it drew on, its key (what
    the pixels are a function of besides the region's own state, compared
    `==`, and an object compared `is`: a theme), the region's offset, a
    stamp (a rect the caller drew over it, which it repaints), what the
    frame changed (its owner's record; None: unknown) and whether the paint
    left the region whole with nothing over it. The slots are kept, never
    made again: noting a frame allocates nothing.

    `vouch` is the shift frame's licence on the host's side: frame n-1
    painted the region whole under this key into the buffer `shift_last`
    reads, so a shift brings n-1 to n. `held` names the paint the buffer a
    frame draws into holds, RETAINED_FRAMES back. Under the windowed WM the
    shown ring vouches for the buffer instead (one voucher per region). A
    ScrollRegion keeps one of these; an app's page (`AppHost`) keeps its
    slots in its context (moy_ui_api_host_begin), and the home's band and
    the desk home's still frames name their parts in the band host's paints
    there (`ui.held`)."""

    def __init__(self, n=_MAX_RETAINED):
        self.n = n
        self._f = array("i", [-1] * n)
        self._off = array("i", [0] * n)
        self._key = [None] * n
        self._obj = [None] * n
        self._cv = [None] * n
        self._stamp = [None] * n
        self._change = [None] * n
        self._whole = bytearray(n)
        self._i = 0                     # the newest slot
        self.size = 0                   # the slots holding a frame

    def clear(self):
        """Forget every frame: the region repaints whole until it re-arms."""
        self.size = 0
        for i in range(self.n):
            self._cv[i] = self._obj[i] = self._key[i] = self._stamp[i] = self._change[i] = None

    def note(self, frame_no, key=None, offset=0, stamp=None, cv=None, obj=None, whole=True,
             change=None):
        """Frame `frame_no` painted the region at `offset`."""
        i = self._i + 1
        if i >= self.n:
            i = 0
        self._i = i
        self._f[i] = frame_no
        self._key[i] = key
        self._obj[i] = obj
        self._off[i] = offset
        self._stamp[i] = stamp
        self._cv[i] = cv
        self._change[i] = change
        self._whole[i] = 1 if whole else 0
        if self.size < self.n:
            self.size += 1

    def close(self, whole):
        """The newest frame's paint is done: whether it left the region
        whole, with nothing drawn over it."""
        if self.size and not whole:
            self._whole[self._i] = 0

    def _slot(self, back):
        i = self._i - back
        return i + self.n if i < 0 else i

    def frame(self, back=0):
        """The number of the frame `back` paints before the newest."""
        return self._f[self._slot(back)] if back < self.size else -1

    def offset(self, back=0):
        return self._off[self._slot(back)]

    def key(self, back=0):
        return self._key[self._slot(back)]

    def stamp(self, back=0):
        return self._stamp[self._slot(back)]

    def change(self, back=0):
        return self._change[self._slot(back)]

    def whole(self, back=0):
        return back < self.size and self._whole[self._slot(back)] == 1

    def held(self, cv, frame_no):
        """How many paints back the paint is whose pixels the buffer frame
        `frame_no` draws into holds: RETAINED_FRAMES - 1, where the frames
        between were this region's paints, one after another (a gap means
        another surface may have painted the buffers) and that paint left
        the region whole. None otherwise."""
        k = getattr(cv, "RETAINED_FRAMES", 0)
        if k < 1 or k > self.size:
            return None
        for b in range(k):
            if self._f[self._slot(b)] != frame_no - 1 - b:
                return None
        return k - 1 if self._whole[self._slot(k - 1)] else None

    def vouch(self, cv, frame_no, key=None, obj=None):
        """True when frame `frame_no` - 1 painted the region whole under
        `key` (and `obj`) on `cv`, whose `shift_last` reads that frame's
        buffer; else None."""
        if not self.size or getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return None
        i = self._i
        if (self._f[i] != frame_no - 1 or self._cv[i] is not cv or not self._whole[i]
                or self._obj[i] is not obj or self._key[i] != key):
            return None
        return True


class ScrollRegion:
    """The one scroll model: a view rect over larger content, on either AXIS
    (vertical by default -- the Settings rows; `horizontal=True` for the
    Library shelf). The caller draws items at (item_pos - self.offset) along
    the scroll axis and clips to the view; this owns the offset bookkeeping,
    clamping, drag deltas, and the slim scrollbar (drawn + hit through the
    same geometry -- along the right edge vertically, the bottom edge
    horizontally)."""

    # Scaled by fs at draw. This is style METRICS (`_METRICS["scrollbar"]`),
    # the one metric that is also a public class attribute -- a caller reading
    # BAR_W and the pixels drawn must never disagree. ui_draw's last line
    # (`_load_metrics`) sets it from C.
    BAR_W = 4
    # The motion -- a drag, the fling a release leaves, the glide a step or a
    # reveal aims -- is native/moy_ui's (moy_ui_scroll_t), its constants
    # moy_ui.h's; every dt is INJECTED (the loop's tick), never read from a
    # clock, so the physics is deterministic under test.

    def __init__(self, horizontal=False):
        self.horizontal = bool(horizontal)
        self.view = (0, 0, 0, 0)
        self.content = 0            # content extent along the scroll axis
        self.offset = 0
        # The drag, the fling and the glide (the last drag sample, the EMA
        # finger velocity in px/ms, the float offset while coasting, the
        # glide's target, their flags) live in this buffer; the rules are
        # native/moy_ui's (moy_ui_scroll_t).
        self._st = bytearray(28)
        # Scroll-as-blit paint ring (#113): what the canvas's buffers hold
        # of this view (`PaintRing`), so a scrolled view can shift its
        # already-correct pixels and repaint only the exposed band.
        self.ring = PaintRing()

    def set(self, view_rect, content):
        self.view = view_rect
        self.content = max(0, int(content))
        self._clamp()

    def _extent(self):
        return self.view[2] if self.horizontal else self.view[3]

    def _max_offset(self):
        return max(0, self.content - self._extent())

    def _clamp(self):
        m = self._max_offset()
        if self.offset < 0:
            self.offset = 0
        elif self.offset > m:
            self.offset = m

    def scroll_by(self, d):
        self.offset += int(d)
        self._clamp()

    # -- scroll-as-blit (#113) ----------------------------------------------
    # The band contract: an eligible frame shifts the view's pixels by the
    # offset delta (Canvas.scroll_rect) and repaints ONLY the exposed band --
    # pixel-identical to a full repaint, at a fraction of the draw calls. The
    # ring pins everything that must match for the shift to be sound: the
    # paints must be the immediately preceding console frames (a gap means
    # another surface may have painted these framebuffers), and the target
    # buffer's paint must share the caller's `key` (selection/statics -- any
    # difference means the retained pixels aren't a pure translation of the
    # current state).

    def invalidate(self):
        """Content changed under the pixels (items/layout/theme): force full
        paints until the ring re-arms."""
        self.ring.clear()

    def note_painted(self, frame_no, key=None, stamp=None, alone=True):
        """Record a paint of this view at the current offset. `key` pins the
        non-scroll state the pixels depend on; `stamp` is an opaque damage rect
        (the cursor sprite baked into this paint) returned by a later
        blit_shift so the caller can repaint it. `alone` False: something
        was drawn over the view this frame, so the paint vouches for nothing.
        The ring keeps _MAX_RETAINED paints: blit_shift needs
        RETAINED_FRAMES consecutive ones, and this has no canvas to read
        the real N from."""
        self.ring.note(frame_no, key, self.offset, stamp, whole=alone)

    def blit_shift(self, cv, frame_no, key=None):
        """(delta, stamp) when the canvas's target framebuffer holds this
        view's pixels at a known offset, else None (caller paints full). delta
        is current offset minus the offset baked in the target buffer; stamp
        is that paint's recorded damage rect (or None)."""
        ring = self.ring
        if getattr(cv, "scroll_rect", None) is None:
            return None
        b = ring.held(cv, frame_no)
        if b is None or ring.key(b) != key:
            return None
        delta = self.offset - ring.offset(b)
        if abs(delta) >= self._extent():
            return None                # nothing survives the shift
        return (delta, ring.stamp(b))

    def scroll_to_show(self, p, size):
        """Nudge the offset so the content span [p, p+size) is inside the view."""
        vis = self._extent()
        if p < self.offset:
            self.offset = p
        elif p + size > self.offset + vis:
            self.offset = p + size - vis
        self._clamp()

    # -- drag (touch) ------------------------------------------------------
    # drag_* take the AXIS coordinate (py vertically, px horizontally);
    # DragTap below picks it for callers that feed whole pointer samples.

    @property
    def _vel(self):
        """The release velocity, px/ms (single precision) -- for tests."""
        return _unpack("<f", self._st, 0)[0]

    @property
    def _drag(self):
        """The last drag sample's axis coordinate, None with no drag live."""
        if not self._st[12] & 1:
            return None
        return _unpack("<i", self._st, 8)[0]

    def drag_start(self, p):
        _C.scroll_drag_start(self._st, p)

    def drag_move(self, p, dt_ms=None):
        off = _C.scroll_drag_move(self._st, p, dt_ms, self.offset, self.content,
                                  self.view[2] if self.horizontal else self.view[3])
        if off < 0:
            return False
        self.offset = off
        return True

    def drag_end(self):
        _C.scroll_drag_end(self._st, self.offset, self.content,
                           self.view[2] if self.horizontal else self.view[3])

    def stop(self):
        """Kill any motion, a fling or a glide (a catching touch / a
        programmatic scroll)."""
        _C.scroll_stop(self._st)

    @property
    def animating(self):
        """True while the view moves on its own -- a released fling coasts
        or a glide eases -- so the owner ticks it each frame and keeps the
        redraw gate open until it rests."""
        return bool(self._st[12] & 6)

    @property
    def flinging(self):
        """True while a released fling coasts."""
        return bool(self._st[12] & 2)

    @property
    def gliding(self):
        """True while a glide eases the view to its target."""
        return bool(self._st[12] & 4)

    @property
    def target(self):
        """The offset the glide is carrying the view to, None with none."""
        if not self._st[12] & 4:
            return None
        return _unpack("<i", self._st, 16)[0]

    def glide_to(self, p, slide=False):
        """Carry the view to offset `p` (clamped), eased in time from where
        it is; `slide`, over the fixed slide time instead (the handheld home's
        carousel). A target where the view is ends any glide."""
        _C.scroll_glide_to(self._st, self.offset, p, slide, self.content, self._extent())

    def fling(self, vel):
        """Add `vel` px/ms to the fling, begun from the offset where none
        coasts: a momentum that is not a drag's release (the trackball's
        coast). Stops a glide."""
        _C.scroll_fling(self._st, self.offset, vel)

    def step(self, n, size):
        """Move the glide's target `n` steps of `size` -- from the target
        while a glide is live, else from the offset -- and answer it."""
        return _C.scroll_step(self._st, self.offset, n, size, self.content, self._extent())

    def land(self):
        """End the motion where it was going (a glide at its target)."""
        off = _C.scroll_land(self._st, self.offset)
        if off >= 0:
            self.offset = off

    def tick(self, dt_ms):
        """Advance the fling or the glide by one frame (dt_ms injected): a
        fling integrates the offset and decays, a glide eases toward its
        target, each hard-stopping at the clamp edges (no overshoot).
        Returns True when a motion was live this frame."""
        off = _C.scroll_tick(self._st, dt_ms, self.offset, self.content, self._extent())
        if off < 0:
            return False
        self.offset = off
        return True

    @property
    def drag_active(self):
        return bool(self._st[12] & 1)

    # -- scrollbar -----------------------------------------------------------

    def bar_rect(self, fs=1):
        """The scrollbar thumb rect, or None when everything fits."""
        return _C.bar_rect(self.view, self.content, self.offset, self.horizontal, fs,
                           self.BAR_W)

    def draw_bar(self, cv, th):
        _C.scrollbar(cv, _look(th), self.view, self.content,
                     self.offset, self.horizontal, self.BAR_W)


class DragTap:
    """Press/drag/release disambiguation over a ScrollRegion -- the ONE
    touch-list gesture machine (the Library shelf and the Settings rows both
    ride it). A press inside the region's view arms a pending tap and starts
    a drag; finger travel past the slop turns the gesture into a SCROLL (the
    pending tap dies); a clean release -- press and release with no drag --
    FIRES the tap. Callers feed every pointer sample to frame() and activate
    ONLY on its result, so scrolling can never 'click' whatever happens to be
    under the finger (the press-edge-activation bug this class retires)."""

    def __init__(self, region):
        self.region = region
        # The gesture's state (the press point and its flags) lives in a buffer this
        # object owns; the rules are native/moy_ui's.
        self._t = bytearray(12)
        self._out = array("i", [0, 0, 0])
                                    # its release is a CATCH, never a tap

    @property
    def dragging(self):
        return bool(self._t[8] & 2)

    def frame(self, px, py, click, down, slop=6, dt_ms=None):
        """One pointer sample: `click` is the press edge, `down` the held
        state, `dt_ms` the loop's tick (feeds the kinetic release velocity).
        Returns the press-edge (x, y) on a clean tap release, else None.
        While a drag is live the region's offset follows the finger -- the
        caller syncs its scroll state from region.offset."""
        r = self.region
        out = self._out
        tapped = _C.dragtap(self._t, r._st, r.view, r.horizontal, px, py, click, down,
                            slop, dt_ms, r.offset, r.content, out)
        r.offset = out[0]
        return (out[1], out[2]) if tapped else None


# The look the scrollbar draws in is ui_draw's; imported last (see above).
from ui_draw import _look
