"""Self-contained console support widgets, extracted from Workstation
(runtime/console.py) -- the cohesive, boundaried little classes with their own state
that don't belong to any one surface Layer or the router:

  * `_Blit`        -- the minimal blittable the cursor sprite + the #39 game->system
                      composite build (canvas.spr reads only w/h/pix/transparent).
  * `Pointer`      -- the screen-space cursor (trackball-relative / touch-absolute,
                      auto-hides after CURSOR_IDLE_MS idle).
  * `Achievements` -- the milestone tracker (#21): the catalog + award-once + toast.
  * `Pmem`         -- a cart's persistent 256x uint32 memory (TIC-80 pmem()).
  * `_SilentAudio` -- the no-op audio backend (#16) when none was injected.
  * `Popup`        -- the reusable dropdown overlay primitive (#52) the ≡ menu is
                      built on.

All are backend-agnostic + MicroPython-safe: they take NO NAMES/canvas (they don't
draw chrome -- Popup/Launcher DRAWING lives on their owners), so this file is a
dependency-free leaf. The trivial helpers a couple of them need (`_ticks_ms` /
`_ticks_diff` / `_err_text` / `_in`) are duplicated here (time-/pure-only, exactly like
achievements_ui.py does) rather than imported back from console -- no circular import.
console.py imports these classes back so `console.Pointer` / `console.Popup` /
`console.ACHIEVEMENTS` / ... still resolve for its own use + the tests + host_app.

(Launcher stayed in console.py: its draw() needs the palette NAMES + the shared
`_blit_glyph` glyph vocabulary + the tile-type constants, and its bare-construction
default needs Layout -- moving it would inject NAMES/a glyph fn into a class the
Workstation constructs, or pull the whole glyph vocabulary out here, i.e. the ugly
cross-import web. It's cohesive with the launcher-home rendering, so it's left put.)
"""
import time


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _err_text(exc):
    """A short, kid-readable one-liner for an exception (type: message). Robust
    on MicroPython, whose exceptions sometimes stringify oddly."""
    try:
        name = type(exc).__name__
    except Exception:  # noqa: BLE001
        name = "Error"
    try:
        msg = str(exc)
    except Exception:  # noqa: BLE001
        msg = ""
    return (name + ": " + msg) if msg else name


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


class _Blit:
    """Minimal blittable for the cursor sprite (canvas.spr reads only these)."""
    def __init__(self, w, h, pix, transparent=-1):
        self.w = w
        self.h = h
        self.pix = pix
        self.transparent = transparent


CURSOR_IDLE_MS = 2000  # hide the trackball cursor after this long with no movement


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
        self.visible = True
        self.idle_ms = idle_ms
        self._last_move = _ticks_ms()

    def move(self, dx, dy):
        # Relative move from the trackball: clamp, and wake the cursor.
        self.x = max(0, min(self.w - 1, self.x + dx))
        self.y = max(0, min(self.h - 1, self.y + dy))
        self.visible = True
        self._last_move = _ticks_ms()

    def place(self, x, y):
        # Absolute position from touch: hit-test there, but keep the cursor
        # hidden (the finger already shows where you are).
        self.x = max(0, min(self.w - 1, x))
        self.y = max(0, min(self.h - 1, y))
        self.visible = False

    def tick(self, now):
        # Auto-hide once the trackball has been idle long enough.
        if self.visible and _ticks_diff(now, self._last_move) >= self.idle_ms:
            self.visible = False


# --- achievements + Easter eggs (#21) ---------------------------------------
#
# A small, tasteful set of fun milestones a kid hits naturally (open/run a cart,
# paint a sprite, edit a map, save code, play a few carts, visit each editor) plus
# the hidden Easter-egg rewards. Each is a tuple (id, name, glyph, hidden):
#   id     -- stable key persisted in achievements.json
#   name   -- the friendly title shown in the toast + the achievements view
#   glyph  -- the icon from _GLYPHS drawn beside it
#   hidden -- True hides the name as "???" in the view until it's unlocked, so a
#             secret stays a surprise (the Easter-egg rewards are all hidden).
# Backend-agnostic + MicroPython-safe (a plain tuple of tuples, frozen into the
# device build). The store (moy_carts.load/save_achievements) holds only the
# unlocked ids; this catalog is the single source of what each one MEANS, so host
# and device show identical badges.
ACHIEVEMENTS = (
    ("first_open",   "First Steps",     "app",    False),
    ("first_run",    "Lift Off!",       "run",    False),
    ("first_paint",  "Little Artist",   "paint",  False),
    ("first_map",    "Map Maker",       "map",    False),
    ("first_code",   "Code Wizard",     "code",   False),
    ("play_five",    "Cart Explorer",   "star",   False),
    ("toolbox",      "Toolbox Master",  "gear",   False),
    ("decorator",    "Home Decorator",  "heart",  False),
    # Hidden Easter-egg rewards (name shown as "???" until found):
    ("konami",        "Secret Coder",    "spark",  True),
    ("clock_tinker",  "Time Traveler",   "smile",  True),
    ("secret_door",   "Secret Finder",   "key",    True),
)

# Which achievement(s) a plain milestone event unlocks. Counters (play_five,
# toolbox) are tallied separately by Achievements.note; everything else maps an
# event name straight to one id. Keep this list of events in sync with the hook
# points in Workstation (open/run/save_*/editor opens).
_EVENT_ACHIEVEMENT = {
    "open": "first_open",
    "run": "first_run",
    "paint_save": "first_paint",
    "map_save": "first_map",
    "code_save": "first_code",
    "wallpaper_change": "decorator",
}

# Editors a kid can visit; opening all of them earns "toolbox".
_TOOLBOX_VIEWS = ("code", "paint", "map")
_PLAY_GOAL = 5            # distinct carts opened to earn "Cart Explorer"

ACH_TITLE = {a[0]: a[1] for a in ACHIEVEMENTS}
ACH_GLYPH = {a[0]: a[2] for a in ACHIEVEMENTS}
ACH_HIDDEN = {a[0]: a[3] for a in ACHIEVEMENTS}

TOAST_MS = 2600          # how long a celebratory unlock banner stays on screen


class Achievements:
    """Tracks which fun milestones a kid has unlocked, awards each exactly once,
    persists the unlocked set, and queues a celebratory toast on a fresh unlock.

    Backend-agnostic + MicroPython-safe. The Workstation owns one of these, calls
    note(event[, key]) at the existing flow points (open/run/paint-save/...), and
    reads `toast`/`toast_until` to draw the banner. Persistence + audio are injected
    callbacks so this class stays free of the SD wrapper and the audio backend (the
    Workstation wires those), which also makes it trivially unit-testable."""

    def __init__(self, unlocked=None, on_save=None, on_unlock=None):
        # `unlocked` is the list loaded from achievements.json (ids already valid).
        self.unlocked = {}                 # id -> True (a set; dict for MP parity)
        for i in (unlocked or ()):
            self.unlocked[i] = True
        self._on_save = on_save            # called(list_of_ids) to persist; None = volatile
        self._on_unlock = on_unlock        # called(id) on a FRESH unlock (e.g. a beep)
        self._seen_views = {}              # editor views visited this session+history
        self._played = {}                  # distinct cart keys opened (for play_five)
        self.toast = None                  # (id, title, glyph) of the live toast, or None
        self.toast_until = 0               # _ticks_ms deadline the toast hides at

    # -- queries -------------------------------------------------------------
    def has(self, ach_id):
        return ach_id in self.unlocked

    def count(self):
        return len(self.unlocked)

    # -- awarding ------------------------------------------------------------
    def award(self, ach_id):
        """Unlock `ach_id` if it isn't already and is a known achievement. Returns
        True only on the FIRST unlock (so a milestone awards exactly once), and then
        persists + raises a toast + fires the on_unlock hook. A repeat is a no-op."""
        if ach_id in self.unlocked or ach_id not in ACH_TITLE:
            return False
        self.unlocked[ach_id] = True
        if self._on_save is not None:
            try:
                self._on_save(list(self.unlocked.keys()))
            except Exception as exc:  # noqa: BLE001 -- a failed save must not crash the UI
                print("Moybyte achievements save failed:", _err_text(exc))
        self.toast = (ach_id, ACH_TITLE[ach_id], ACH_GLYPH.get(ach_id, "trophy"))
        self.toast_until = _ticks_ms() + TOAST_MS
        if self._on_unlock is not None:
            try:
                self._on_unlock(ach_id)
            except Exception:  # noqa: BLE001 -- audio is best-effort celebration
                pass
        return True

    def note(self, event, key=None):
        """Record a milestone `event` and award whatever it earns. `key` is the
        per-event detail used by the counter milestones: for "open" it's the cart
        identity (distinct carts -> play_five); for "editor" it's the view name
        (visiting all editors -> toolbox). Direct-mapped events (open/run/saves)
        award their id immediately. Safe to call every time the event happens --
        award() makes the once-only guarantee."""
        if event == "open":
            if key is not None:
                self._played[key] = True
                if len(self._played) >= _PLAY_GOAL:
                    self.award("play_five")
            self.award("first_open")
        elif event == "editor":
            if key in _TOOLBOX_VIEWS:
                self._seen_views[key] = True
                if all(v in self._seen_views for v in _TOOLBOX_VIEWS):
                    self.award("toolbox")
        elif event in _EVENT_ACHIEVEMENT:
            self.award(_EVENT_ACHIEVEMENT[event])

    def toast_active(self, now=None):
        if self.toast is None:
            return False
        if now is None:
            now = _ticks_ms()
        if _ticks_diff(self.toast_until, now) <= 0:
            self.toast = None
            return False
        return True


class Pmem:
    """A cart's persistent memory: 256 x 32-bit unsigned ints, TIC-80 pmem().

    Backend-agnostic (host + device share this). The Workstation builds one per
    cart from moy_carts.load_pmem and injects its `cell` accessor into make_api as
    `pmem(i[, v])`: read pmem(i) -> int, write pmem(i, v) -> persists (when the
    cart is on a writable store). A write only persists if the value actually
    changed, so a cart calling pmem(i, v) every frame doesn't hammer the SD."""

    CELLS = 256
    MASK = 0xFFFFFFFF

    def __init__(self, cells=None, on_write=None):
        # `cells` is the loaded list (already 256 long from moy_carts.load_pmem);
        # default to all-zero so an embedded/non-SD cart still gets working RAM.
        if cells is None:
            cells = [0] * self.CELLS
        self.cells = cells
        self._on_write = on_write   # called(cells) to persist; None = volatile

    def cell(self, index, value=None):
        i = int(index)
        if i < 0 or i >= self.CELLS:
            return 0
        if value is None:
            return self.cells[i]
        v = int(value) & self.MASK
        if self.cells[i] != v:
            self.cells[i] = v
            if self._on_write is not None:
                self._on_write(self.cells)
        return v


class _SilentAudio:
    """No-op audio backend (#16): wraps an AudioEngine but never produces sound.
    The default when no make_audio backend was injected. Exposes the same control
    surface the api binds to, so make_api stays identical whether or not real
    playback is wired. (Permission-gating audio on the manifest 'sound' permission
    is future work -- the v0.4 console doesn't yet enforce any cart permissions.)"""

    def __init__(self, engine):
        self.engine = engine

    def sfx(self, n, chan=None):
        pass

    def beep(self, freq, dur=0.15):
        pass

    def music(self, track, loop=True):
        pass

    def music_stop(self):
        pass

    def sound_stop(self, chan=None):
        pass

    def volume(self, level):
        pass

    def tick(self, dt):
        pass


# --- reusable overlay popup (#52) -------------------------------------------
# A minimal left-anchored dropdown drawn ON TOP of whatever screen is up, with its
# OWN open/selected state. It's the primitive the top-bar system menu is built on,
# and is reusable for #55 (system-as-cart) / future menus. Index-only drawing (the
# existing cls/rect/rectb/print verbs -- host == device, no new native primitive),
# petme128 8x8 text, the _glyph fallback contract for an optional per-row icon.
#
# Items are tuples; the first element is a kind:
#   ("header", text)               -- a dim section title; NOT cursor-selectable
#   ("sep",)                        -- a 1px separator line between groups
#   ("item", text, action)         -- a selectable row; `action` is called on activate
# The cursor (`sel`) only ever lands on "item" rows (move()/_clamp skip the rest);
# activate() runs the selected item's action then closes (close-on-select).
_POPUP_X = 0                  # panel left edge (flush to the screen left, x = 0)
_POPUP_Y = 18                 # top edge flush under the 18px bar (== bar_layer._STATUS_H)
_POPUP_W = 128                # panel width (~120-140px band; 128 keeps it clear of clock)
_POPUP_ROW_H = 12             # per-row height (selectable + header rows alike)
_POPUP_PAD_X = 4              # text inset from the panel left
_POPUP_SEP_H = 1              # separator line height


class Popup:
    """A self-contained dropdown overlay (#52): owns open/closed + the moving cursor,
    dismisses on outside-tap / ESC, and draws on top. Backend-agnostic -- the host and
    the device drive it through the same indexed canvas."""

    def __init__(self):
        self.open = False
        self.items = []               # list of row tuples (see module note above)
        self.sel = 0                  # index of the highlighted SELECTABLE row
        # Panel left edge. Stage 4 moved the ≡ toggle to the bar's RIGHT zone, so the
        # dropdown anchors UNDER it (hanging down-left to stay on screen) instead of the
        # old flush-left x=0. toggle_sysmenu sets this from the live ≡ button rect before
        # opening; it defaults to _POPUP_X so a Popup opened without an anchor (tests /
        # any future caller) still lands flush-left.
        self.anchor_x = _POPUP_X

    # -- open/close ----------------------------------------------------------
    def show(self, items):
        """Open with `items`, cursor on the first selectable row."""
        self.items = list(items)
        self.open = True
        self.sel = self._first_selectable()

    def close(self):
        self.open = False

    def toggle(self, items):
        """≡ pressed: open with `items` if closed, else close (the same control
        toggles it shut)."""
        if self.open:
            self.close()
        else:
            self.show(items)

    # -- selectable-row helpers ----------------------------------------------
    def _is_selectable(self, i):
        return 0 <= i < len(self.items) and self.items[i][0] == "item"

    def _first_selectable(self):
        for i in range(len(self.items)):
            if self.items[i][0] == "item":
                return i
        return 0

    def _clamp_sel(self):
        if not self._is_selectable(self.sel):
            self.sel = self._first_selectable()

    # -- navigation (cursor skips headers/separators; clamps at the ends) -----
    def move(self, d):
        """Step the highlight by d (+1 down / -1 up), skipping non-selectable rows.
        Clamps at the first/last selectable row (no wrap)."""
        if not self.open or d == 0:
            return
        step = 1 if d > 0 else -1
        i = self.sel + step
        while 0 <= i < len(self.items):
            if self.items[i][0] == "item":
                self.sel = i
                return
            i += step
        # no further selectable row in that direction -> stay put (clamp)

    def activate(self):
        """Fire the selected row's action, then close (close-on-select). No-op when
        closed or the cursor isn't on a selectable row."""
        if not self.open:
            return
        self._clamp_sel()
        if self._is_selectable(self.sel):
            action = self.items[self.sel][2]
            self.close()              # close BEFORE running so the action can re-open
            if action is not None:
                action()

    # -- geometry + hit-testing ----------------------------------------------
    def panel_rect(self):
        """(x, y, w, h) of the whole panel -- height grows with the row count. The
        left edge is `anchor_x` (set under the ≡ button by toggle_sysmenu, Stage 4);
        defaults to _POPUP_X (flush left)."""
        h = 0
        for it in self.items:
            h += _POPUP_SEP_H if it[0] == "sep" else _POPUP_ROW_H
        return (self.anchor_x, _POPUP_Y, _POPUP_W, h)

    def row_at(self, px, py):
        """Index of the row under (px, py), or None when outside the panel."""
        if not self.open:
            return None
        x, y, w, h = self.panel_rect()
        if not _in(px, py, (x, y, w, h)):
            return None
        cy = _POPUP_Y
        for i in range(len(self.items)):
            rh = _POPUP_SEP_H if self.items[i][0] == "sep" else _POPUP_ROW_H
            if cy <= py < cy + rh:
                return i
            cy += rh
        return None

    def click(self, px, py):
        """Apply a tap: outside the panel -> dismiss; on a selectable row -> move the
        cursor there AND activate (tap = move+select in one gesture, #52); on a
        header/separator -> swallow (taps inside the panel never dismiss). Returns
        True when the tap was consumed (so the caller stops dispatching it)."""
        if not self.open:
            return False
        i = self.row_at(px, py)
        if i is None:
            self.close()              # tap OUTSIDE dismisses
            return True
        if self.items[i][0] == "item":
            self.sel = i
            self.activate()
        return True                    # tap inside is always consumed
