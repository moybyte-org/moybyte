"""Self-contained console support widgets, extracted from Workstation
(runtime/console.py) -- the cohesive, boundaried little classes with their own state
that don't belong to any one surface Layer or the router:

  * `_Blit`        -- the minimal blittable the cursor sprite + the #39 game->system
                      composite build (canvas.spr reads only w/h/pix/transparent).
  * `Pointer`      -- the screen-space cursor (trackball-relative / touch-absolute,
                      auto-hides after CURSOR_IDLE_MS idle).
  * `Achievements` -- the milestone tracker (#21): the catalog + award-once + toast.
  * `Pmem`         -- a cart's persistent 256x uint32 memory (TIC-80 pmem()).
  * `Actor`/`Scenes` -- a cart's placed-actor scenes (#85): read-only data rows +
                      the scene()/load_scene() accessor over the .moyscene blobs.
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
(`json` is imported for Scenes, which parses the .moyscene blobs -- available on
both CPython and MicroPython.)

(Launcher stayed in console.py: its draw() needs the palette NAMES + the shared
`_blit_glyph` glyph vocabulary + the tile-type constants, and its bare-construction
default needs Layout -- moving it would inject NAMES/a glyph fn into a class the
Workstation constructs, or pull the whole glyph vocabulary out here, i.e. the ugly
cross-import web. It's cohesive with the launcher-home rendering, so it's left put.)
"""
import json
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


def rotate_indices(pix, w, h, deg, transparent):
    """Rotate an indexed sprite (`pix` = w*h palette indices) by `deg` degrees, nearest-
    neighbour, expanding the canvas so no corner is clipped. Pixels that fall outside the
    source become -1 (Canvas.spr always skips index < 0), so the rotated sprite has clean
    transparent corners; the source's own `transparent` index is carried through. Pure
    integer/float pixel math -> host and device rotate identically (#85/#93 all-around
    rotation). Returns (out_pix, out_w, out_h)."""
    import math
    a = math.radians(deg)
    ca = math.cos(a)
    sa = math.sin(a)
    ow = int(abs(w * ca) + abs(h * sa) + 0.5) or 1
    oh = int(abs(w * sa) + abs(h * ca) + 0.5) or 1
    out = [-1] * (ow * oh)
    ocx = (ow - 1) * 0.5
    ocy = (oh - 1) * 0.5
    icx = (w - 1) * 0.5
    icy = (h - 1) * 0.5
    for oy in range(oh):
        ry = oy - ocy
        for ox in range(ow):
            rx = ox - ocx
            sx = ca * rx + sa * ry + icx     # inverse-rotate into the source
            sy = -sa * rx + ca * ry + icy
            ix = int(sx + 0.5)
            iy = int(sy + 0.5)
            if 0 <= ix < w and 0 <= iy < h:
                out[oy * ow + ox] = pix[iy * w + ix]
    return out, ow, oh


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


class Clipboard:
    """The ONE system clipboard (#132): a tiny typed holder every editor
    writes THROUGH while keeping its local behavior. `kind` is "text" (v1)
    or "pixels" (the planned Paint lane); no host-OS clipboard integration
    anywhere -- ours end-to-end, so host/device parity holds by construction.
    Every copy in every attached editor lands here, so this is always the
    NEWEST copy across apps; a paste that wants text just reads text()."""

    def __init__(self):
        self.kind = None          # "text" | "pixels" | None (empty)
        self.data = None
        self.seq = 0              # bumps per put -- lets a lane detect updates

    def put_text(self, s):
        self.kind = "text"
        self.data = str(s)
        self.seq += 1

    def text(self):
        """The clipboard's text, or '' (empty / holding a non-text kind)."""
        return self.data if self.kind == "text" and self.data else ""


class Pmem:
    """A cart's persistent memory: 256 x SIGNED 32-bit ints, TIC-80 pmem().

    Backend-agnostic (host + device share this). The Workstation builds one per
    cart from moy_carts.load_pmem and injects its `cell` accessor into make_api as
    `pmem(i[, v])`: read pmem(i) -> int, write pmem(i, v) -> RAM + a dirty mark.
    Persistence is DEFERRED to flush() (#66, measured on glass 2026-07-14: the
    old per-write SD save was Letter Blitz's 81-130ms mid-play hitch on every
    letter pop -- a FAT-over-SD write can't be made fast, only moved off the
    play path). The Player guarantees a flush on cart exit + crash and runs a
    periodic one while dirty; a no-change write never dirties, so a cart calling
    pmem(i, v) with the same value every frame stays clean."""

    CELLS = 256
    # A slot holds a SIGNED 32-bit integer -- exactly what SPEC.md 4.2 makes a Lua
    # integer, which is where every value comes from and goes back to. Storing
    # unsigned (as this did) meant pmem(i, -1) read back 4294967295: wrong for a
    # python cart, and not even representable in a LUA_32BITS int, so a lua cart
    # could not round-trip a negative score at all.
    MASK = 0xFFFFFFFF
    SIGN = 0x80000000

    def __init__(self, cells=None, on_write=None):
        # `cells` is the loaded list (already 256 long from moy_carts.load_pmem);
        # default to all-zero so an embedded/non-SD cart still gets working RAM.
        if cells is None:
            cells = [0] * self.CELLS
        self.cells = cells
        self._on_write = on_write   # called(cells) to persist; None = volatile
        self._dirty = False

    def cell(self, index, value=None):
        i = int(index)
        if i < 0 or i >= self.CELLS:
            return 0
        if value is None:
            return self.cells[i]
        v = int(value) & self.MASK
        if v >= self.SIGN:              # wrap into the signed range, like the VM
            v -= self.MASK + 1
        if self.cells[i] != v:
            self.cells[i] = v
            self._dirty = True
        return v

    def flush(self):
        """Persist the cells IF a write changed them since the last flush.
        Returns True when a save actually ran (the PMEM diag line's cadence).
        Cleared before writing: a failed save loses that one snapshot (the
        pre-#66 semantics), never retries per frame."""
        if not self._dirty or self._on_write is None:
            return False
        self._dirty = False
        self._on_write(self.cells)
        return True


# --- placed-actor scenes (#85) ----------------------------------------------
#
# A scene is a saved table of placed actors (a sprite + a world position + a tag),
# authored WYSIWYG and consumed once at cart start (data-only, #85 Variant A). Each
# .moyscene file is compact JSON: an ordered list of rows {tag, tile, x, y, flip,
# flags}; order is the spawn order and the default draw order. The cart reads the
# table in _init and spawns whatever it wants -- there is NO drawing here, so this
# lives once in shared code (host == device) and make_api just binds the accessors.


class Actor:
    """One placed actor from a scene (#85): a read-only data ROW the cart branches on.

    Attribute access -- a.tag / a.tile / a.x / a.y / a.flip / a.flags -- like the
    {tag, tile, x, y, flip, flags} JSON it came from. `tag` is the kind string code/
    blocks dispatch on; `tile` the sheet index (placement preview + default draw);
    `x`/`y` world-space ints; `flip` 0/1; `flags` an optional dict of kid-tunable
    extras. Read-only by convention (writing a scene back at runtime is out of scope --
    that's pmem's job); MicroPython-safe (plain attributes, no dependencies)."""

    def __init__(self, tag="", tile=0, x=0, y=0, flip=0, flags=None):
        self.tag = str(tag)
        self.tile = int(tile)
        self.x = int(x)
        self.y = int(y)
        self.flip = int(flip)
        self.flags = flags if isinstance(flags, dict) else {}

    def __repr__(self):
        return "Actor(tag=%r, tile=%d, x=%d, y=%d, flip=%d)" % (
            self.tag, self.tile, self.x, self.y, self.flip)


class Scenes:
    """A cart's placed-actor scenes (#85), parsed from the scenes/*.moyscene blobs.

    Backend-agnostic + MicroPython-safe (json only). The Workstation builds one per
    cart from the raw {name: json-text} blobs moy_carts.load returns (+ the manifest's
    ordered names, element 0 = the default active scene) and injects scene()/
    load_scene() into make_api, so both the host and the device get the accessors from
    THIS one implementation. Parsing is lazy + memoised, so scene() is cheap even
    called every frame. The active scene resets to the default on each run via reset()
    (Player.start), so a load_scene() switch never leaks across a re-run ("resets on
    next _init", #85 Section 6). An absent/malformed scene yields an empty list -- a
    cart with no scenes just spawns nothing, never crashes."""

    def __init__(self, blobs=None, names=None):
        self._raw = dict(blobs) if blobs else {}
        if names:
            order = [n for n in names if n in self._raw]
        else:
            order = sorted(self._raw.keys())
        self.names = order                 # ordered scene names (0 = default active)
        self._default = order[0] if order else None
        self.active = self._default
        self._cache = {}                   # name -> [Actor], parsed once
        self._world = None                 # #109: the live mutable actor world (lazy)

    def reset(self):
        """Back to the default active scene -- called at each run's start so a
        load_scene() switch doesn't persist into the next _init (#85). Also drops
        the parse cache: scene() hands out the CACHED Actor objects (fresh list,
        shared rows), so a cart that mutated its rows in-place would otherwise
        carry that drift into its next run. Re-parsing once per run is cheap. The
        live actor world (#109) is dropped too, so each run starts from the scene."""
        self.active = self._default
        self._cache = {}
        self._world = None

    def world(self):
        """The live mutable actor world (#109) for the actor-aware blocks + the
        touching()/move_actor()/remove_actor() cart-API verbs. One per run: it's
        created lazily here and dropped by reset() at each run's start, so a fresh
        run always projects from the scene again. make_api calls this once and binds
        the world's methods (host == device -- pure data, no drawing)."""
        if self._world is None:
            self._world = SceneWorld(self)
        return self._world

    def _parse(self, name):
        got = self._cache.get(name)
        if got is not None:
            return got
        actors = []
        blob = self._raw.get(name)
        if blob is not None:
            try:
                rows = json.loads(blob)
            except (ValueError, TypeError):
                rows = None
            if isinstance(rows, list):
                for r in rows:
                    if isinstance(r, dict):
                        actors.append(Actor(
                            r.get("tag", ""), r.get("tile", 0),
                            r.get("x", 0), r.get("y", 0),
                            r.get("flip", 0), r.get("flags")))
        self._cache[name] = actors
        return actors

    def scene(self, name=None):
        """Iterate a scene's actors (read-only rows). scene() -> the ACTIVE scene;
        scene(name) -> a named scene WITHOUT switching the active one. A missing or
        malformed scene yields an empty list (never raises). A fresh list copy each
        call, so a cart mutating it can't corrupt the parse cache."""
        n = self.active if name is None else name
        if n is None:
            return []
        return list(self._parse(n))

    def load_scene(self, name):
        """Set the active scene and return its actors. An unknown name leaves the
        active scene unchanged and returns [] (the graceful missing-scene path)."""
        if name in self._raw:
            self.active = name
            return list(self._parse(name))
        return []

    def raw(self, name):
        """The raw .moyscene blob of scene `name` (None when absent) -- the
        placement editor (#85 Stage 2) parses this into its editable rows."""
        return self._raw.get(name)

    def put(self, name, text):
        """Replace scene `name`'s raw blob IN the live object (#85 Stage 2: the
        placement editor syncs each committed gesture here, so a PLAY without an
        explicit SAVE runs the freshest placement -- the same live-edit semantics
        the shared TileMap gives the map editor). A NEW name joins the order (and
        becomes the default when there was none); the parse cache entry drops so
        the next scene() re-parses."""
        known = name in self._raw
        self._raw[name] = text
        if not known:
            self.names.append(name)
            if self._default is None:
                self._default = name
            if self.active is None:
                self.active = name
        self._cache.pop(name, None)


class SceneWorld:
    """The live, mutable actor world for the actor-aware blocks (#109, #85 Section 8).

    `scene()` is READ-ONLY authored data (the placement, unchanged across runs); the
    world is the PLAYABLE projection the game acts on -- it MOVES and REMOVES actors
    and those changes persist across frames within a run. It is built LAZILY from the
    active scene's rows on first access (fresh, independent Actor copies, so touching
    the world never drifts the scene's parse cache), and dropped by Scenes.reset() at
    each run's start, so a fresh run always projects from the scene again.

    The declarative floor of #85 Section 8 lives HERE, not in a C engine: the actor
    blocks compile to plain calls on these verbs -- `actors(tag)` (a SNAPSHOT list, so
    a for-each can safely remove during iteration), `touching(a, b)` (AABB over the
    8px tile boxes; `b` an Actor or a tag string), `move_actor`/`move_actor_to`,
    `remove_actor` -- and the SAME verbs ship in make_api, so a kid who graduates to
    code calls exactly what they used to click. Backend-agnostic + MicroPython-safe
    (pure data, no drawing -- draw_scene lives in make_api, which owns the canvas)."""

    def __init__(self, scenes):
        self._scenes = scenes
        self._actors = None                # lazy: [Actor], built on first access

    def _ensure(self):
        if self._actors is None:
            live = []
            src = []
            if self._scenes is not None:
                try:
                    src = self._scenes.scene()
                except Exception:  # noqa: BLE001 -- a bad scene just spawns nothing
                    src = []
            for a in src:
                live.append(Actor(a.tag, a.tile, a.x, a.y, a.flip,
                                  dict(a.flags) if a.flags else None))
            self._actors = live
        return self._actors

    def actors(self, tag=None):
        """A SNAPSHOT list of the live actors (all of them, or only those of `tag`).
        A fresh list each call, so a `for each` loop over it can remove_actor() an
        item mid-iteration without skipping the next -- the remove-while-iterating
        trap is handled here, never by the kid (#85 Section 8)."""
        live = self._ensure()
        if tag is None:
            return list(live)
        return [a for a in live if a.tag == tag]

    def remove(self, actor):
        """Remove `actor` from the live world (by identity). A no-op for an actor
        already gone or for None (a `remove actor` block outside a for-each), so a
        misplaced block never crashes the cart."""
        if actor is None:
            return
        live = self._ensure()
        for i in range(len(live)):
            if live[i] is actor:
                del live[i]
                return

    def move(self, actor, dx, dy):
        """Nudge `actor` by (dx, dy) world pixels. None -> no-op (a `move actor`
        block outside a for-each)."""
        if actor is not None:
            actor.x = int(actor.x + dx)
            actor.y = int(actor.y + dy)

    def move_to(self, actor, x, y):
        """Place `actor` at world (x, y). None -> no-op."""
        if actor is not None:
            actor.x = int(x)
            actor.y = int(y)

    def touching(self, a, b):
        """AABB overlap over the 8px tile boxes (#85 Section 8). `a` is an actor; `b`
        is an Actor OR a tag string. A tag tests `a` against ANY OTHER live actor of
        that tag (a==b identity is skipped, so an actor never 'touches' itself).
        Returns False for a None `a` (a `touching?` reporter outside a for-each)."""
        if a is None:
            return False
        if isinstance(b, Actor):
            return self._overlap(a, b)
        for other in self._ensure():
            if other is a:
                continue
            if other.tag == b and self._overlap(a, other):
                return True
        return False

    def _overlap(self, a, b):
        return (a.x < b.x + 8 and b.x < a.x + 8 and
                a.y < b.y + 8 and b.y < a.y + 8)


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

    def is_active(self):
        return False        # nothing is ever audible on the silent backend

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
        # Geometry scale (#39/#58): the popup's rows hold fs-scaled petme128 text,
        # so the panel/row/hit geometry must scale WITH the system font or the rows
        # overlap (glass-found on the P4 at font scale 2). toggle_sysmenu sets this
        # from _effective_font_scale(); 1 keeps every product byte-identical (the
        # T-Deck / 320x240 baseline).
        self.fs = 1

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
        defaults to _POPUP_X (flush left). All geometry scales by `fs` (1 = the
        byte-identical baseline)."""
        fs = self.fs
        h = 0
        for it in self.items:
            h += _POPUP_SEP_H * fs if it[0] == "sep" else _POPUP_ROW_H * fs
        return (self.anchor_x, _POPUP_Y * fs, _POPUP_W * fs, h)

    def row_at(self, px, py):
        """Index of the row under (px, py), or None when outside the panel."""
        if not self.open:
            return None
        x, y, w, h = self.panel_rect()
        if not _in(px, py, (x, y, w, h)):
            return None
        fs = self.fs
        cy = _POPUP_Y * fs
        for i in range(len(self.items)):
            rh = _POPUP_SEP_H * fs if self.items[i][0] == "sep" else _POPUP_ROW_H * fs
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
