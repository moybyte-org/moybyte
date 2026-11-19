# Map (grep -n a name to jump there):
#   Player                runs one cart: start, tick every frame, always exit
#   -- What the Player decides about a cart  player_fit; its diagnostics, player_diag
#   Player.start          start or re-run a cart under make_api
#   Player.frame_plan     what this loop frame is for the cart
#   Player.tick           one frame of the running cart
#   Player.release_world  drop the dead run's world at exit
#   Player.is_app         whether the run has the shell's bar; its parts: face, pace
"""The cart PLAYER -- the run-loop black box (Stage 2 of
docs/history/shell_ux_technical_plan_v1.md).

`Player` is the one object that RUNS a cart: it starts a cart under the frozen
`make_api`, feeds it input, presents its pixels, and guarantees it exits (a crash
becomes an on-canvas error panel, never a hang). Pulling this out of `Workstation`
is the pivotal cut of the shell-UX migration: a cart now runs identically whether
it was launched from the home grid or (Stage 3) from the editor's PLAY -- the
Player has zero knowledge of who launched it.

What the Player owns (moved verbatim from Workstation):
  * `start(project)` -- reset the canvas, stamp the cart clock, gate `wifi` by the
    manifest permission, call the injected `make_api`, exec the source, capture
    `_update`/`_draw`, turn any exception into `cart_error`/`crash_line`.
  * `tick(dt)` -- the per-frame game loop: key-edge derivation (cart_key/cart_keyp),
    `_update(dt)`/`_draw()`/`audio.tick`, the crash capture, then the crash chrome.
    It fills the DRAWBRK perf split
    (ws.pf_upd/pf_cart/pf_audio/pf_bg) -- that contract stays on `ws`.
  * `handle_input`/`handle_pointer` -- a running cart owns every key and tap. The
    console's key (`start`, Super) is read by the router before this layer and
    opens the pause menu (`pause`/`resume` below); a cart ends itself with `quit()`,
    and a tool/app exits through its bar's context-X.
  * a stopped run's notice (`notice`, `newer`), which the console's stop card
    shows (`shell_cards.PauseMenu.stop`).

The BUNDLE the Player reaches (spec Section 2): the open `Project` (its live data),
plus the RAW canvas / input / audio / `make_api` / wifi through its `ws` back-ref --
the SAME raw objects `Workstation._start` handed `make_api` before, so the hot draw
path stays injected-direct (the Section 5 perf guardrail: WHO calls make_api moved,
WHAT it closes over did not). It deliberately does NOT reach the store, the shell
top bar, the home grid, or the layouts -- the stop card it opens is reached
through thin `ws` helpers so this file never names them (a spike-test grep enforces
the isolation). The cart-run fields it owns are exposed back on Workstation as
forwarding properties, so every surface file + test is unchanged.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/ tree
so the device freezes it (same pattern as console.py/project.py). It stays a leaf --
the tiny `_ticks_*`/`_wrap` helpers are defined here, `_err_text`/`_in` come from the
widgets leaf and NAMES is injected -- so it imports nothing back into console (no
circular import). The one cross-module value it needs
is the code line-height for the crash panel, imported from the code editor leaf
(bare name on the device / once host_app has aliased it, `runtime.X` for a direct
test load).
"""

import sys as _sys
import chrome_ops as _ch

# #65 Phase 2: the lockstep frame's local-input packer. The BUTTON ORDER comes
# from cart_api (its one author) and is handed to the session.
from players import mask_of as _netplay_mask, world_text as _world_text
from cart_api import CART_BUTTONS as _NET_BUTTONS
import os_log as _os_log
import devices as _devices
import moybyte as _moybyte
from moy_play import Tick as TickScheduler     # the kernel's tick model
import moy_play as _moy_play


# Auto-native carts (#67 spike): when the runtime HAS the native code emitter
# (MicroPython on device / unix; never host CPython), every top-level def in a
# cart is compiled to machine code at load -- the kid writes ordinary Python,
# the loader applies @micropython.native. Measured on the unix build: -5..-15%
# cart logic with BYTE-IDENTICAL pixels across all seed carts (the emitter
# preserves full Python semantics); the S3 delta is expected larger (its
# per-call dispatch cost dominates kid loops -- see the CALIB line). Guarded:
# if the emitter refuses a cart (compile-time), the pristine bytecode path
# runs instead -- a cart can never break because of this flag.
try:
    import micropython as _micropython
    # The decorator is a COMPILER construct (recognized syntactically, stripped
    # from the runtime module), so probing an attribute can't detect it -- compile
    # a one-liner through the emitter instead. Raises on a build without it.
    exec(compile("@micropython.native\ndef _t():\n    pass", "<probe>", "exec"),
         {"micropython": _micropython})
    NATIVE_CARTS = True
except Exception:  # noqa: BLE001 -- host CPython / emitter-less build: bytecode only
    _micropython = None
    NATIVE_CARTS = False


# The cart TYPES that run with the shell's minimal exitable bar rather than
# owning the whole raster, and the one of them with no folder. `console.py`
# imports both -- the two files decided this in parallel with two literal
# tuples until scripts made it three types, and a bar the Player draws but the
# shell does not route taps to is a cart nobody can leave.
SCRIPT_TYPE = "script"
BAR_TYPES = ("tool", "app", SCRIPT_TYPE)

# The optional features this build implements (moy SPEC.md 10). A cart whose
# manifest "extensions" lists anything else is REFUSED at start -- the clean
# §10 decline, never a mid-frame crash on a missing verb.
#
# The first two are BACK-COMPAT names, not features this gate decides. Upstream
# b9dbba1 (vendored 2026-08-19) made `make_layer`/`draw_layer`/`background` and
# `view` CORE -- SPEC.md 6, installed unconditionally, because a verb that
# degrades truthfully cannot be an extension. They stay listed here so a
# manifest written against the older spec still starts: declaring them is now a
# no-op, and REMOVING them from this tuple would refuse carts that already ship
# with the declaration. The rest are moybyte's own,
# namespaced `vendor.feature` exactly as §10 requires so they can never collide
# with a future standard name -- a cart declaring one is non-portable by
# construction, which is the honest trade its author is making. Before this
# list existed the gate refused every namespaced name, so a cart that truthfully
# declared what it used was rejected by the one console that implements it.
SUPPORTED_EXTENSIONS = (
    "layers",             # CORE now (SPEC.md 6): make_layer/draw_layer/background
    "viewport",           # CORE now (SPEC.md 6): view(w, h)
    "moybyte.scenes",     # #85/#109: scene/load_scene + the actor world
    "moybyte.images",     # #63: image(name) / Image paint-image assets
    "moybyte.net",        # #65: net.send / on_net (also permission-gated)
    "moybyte.wifi",       # #38: the injected wifi service (permission-gated)
    "moybyte.app",        # #224: a compiled app's role imports (native/moy_app/moy_app_wasm.h)
    "moybyte.ui",         # the public UI API, a Lua cart's `ui` (native/moycore/moycore_ui.h)
    "moybyte.mouse",      # a compiled cart's mouse import (native/moycore/moycore_wasm_ext.h)
    "moybyte.prefs",      # a compiled game's prefs imports (native/moycore/moycore_wasm_ext.h)
)
APP_EXT = "moybyte.app"
PREFS_EXT = "moybyte.prefs"


def _game(cart, spine):
    """A game: a cart that is no app (user, shipped or system). Its run holds
    a grant of its own with the prefs role, which a game holds without asking
    (docs/app_api_v1.md section 5): `prefs` in Python and Lua, the
    "moybyte.prefs" imports in a compiled game that declares them."""
    return (cart is not None and cart.get("type") != "app"
            and not spine.is_user_app(cart) and not spine.is_system_app(cart))


def _game_perms(cart, granted):
    """The permissions a game's grant is made from: the prefs role's alone,
    and a compiled game declaring the app extension its own beside it, for
    its imports from that (`_wasm_ext_game`)."""
    perms = ("prefs",)
    if cart.get("runtime") == "wasm" and APP_EXT in (cart.get("extensions") or ()):
        own = granted if granted is not None else (cart.get("permissions") or ())
        perms = tuple(p for p in own if p != "prefs") + perms
    return perms


def _wasm_ext_game(cart, spine):
    """A compiled game that declares the app extension: its run holds a
    grant of its own, as a user app's does, for its imports from it --
    award, progress, notify and the device handles -- under its
    permissions and their consent."""
    return (cart is not None and cart.get("runtime") == "wasm"
            and APP_EXT in (cart.get("extensions") or ())
            and cart.get("type") != "app" and not spine.is_system_app(cart))


def _python_files(cart):
    """A Python cart's files (moy-spec SPEC.md 9.1): its folder and the
    "writable" entries its manifest declares, opened on the store a Lua and a
    compiled run's are kept in (native/moycore/moycore_files.h, moycore's
    CartFiles on every tier). None for a run with no folder, or where the
    store cannot be opened: its verbs then answer as a host with none."""
    path = cart.get("path") if cart is not None else None
    if not path or cart.get("runtime", "python") != "python":
        return None
    try:
        import moycore
        return moycore.CartFiles(path, "\0".join(cart.get("writable") or ()) or None)
    except (ImportError, OSError, AttributeError):
        return None


from ticks import _ticks_ms, _ticks_us, _ticks_diff
from widgets import _err_text, _in

from ui_draw import cart_text as _cart_text      # the app-cart boundary (§4.7)
import ui_draw as _ui_draw
from array import array as _array
_RUN_STEPS = _array("i", [0, 0])        # reused: a run face's frame's steps
# The run's two parts (runtime/run/player_face.py, runtime/run/player_pace.py).
from player_face import AppFace, steps as _steps
from player_pace import Pace
# USER APPS (#181, ui_refactor_2026-08 Phase 7): the permission-keyed filter over
# AppContext, plus the responsive opt-in probe. A leaf like the rest of what this
# file imports -- it holds no Workstation, only the context FACTORY it is handed.
from system_api import draws_in_look, make_run_grant, make_system_api, wants_layout
import moy_app as _moy_app              # the grant policy (native/moy_app)
import app_perms as _perms

# The cart-facing text editor (docs/history/text_editing_2026-09.md step 3),
# imported here rather than reached through the shell: it is a leaf over the
# Files role and the cart's canvas.
from editor_handle import EditorHandle
# What the Player decides about a cart (player_fit) and its diagnostics
# (player_diag), imported by name so a call costs what it did in this file.
from player_fit import (_nativize, _verdict, _exc_cart_line, RUNTIME_NAMES, _compiled,
                        INTERP_NOTICE_TITLE, INTERP_NOTICE_SUB, INTERP_NOTICE_SUB_DEFAULT,
                        _Refused, newer_notice, fit_notice, _cart_fit, _fits, _cart_unbuilt,
                        _cart_missing, _out_of_memory, _lua_err_text, _lua_cart_where,
                        _format_newer)
from player_diag import PlayerDiag, _heap_stats, _moycore_sram
from scheduler import allocated as _allocated, ALLOC_WRAP as _ALLOC_WRAP


# Compiled-code cache (#66 exec-arena fix, see Player.start): one entry per
# cart, keyed by the source's (len, hash) -- so the native emitter runs once
# per source VERSION, not once per PLAY. Module-level: survives Player runs,
# dies with the VM (exactly the arena's own lifetime).
_CODE_CACHE = {}


# A `"fps": "free"` game's longest single tick, seconds: two 30Hz periods and a
# bit. Longer gaps are stalls, and a stall slows time rather than jumping it
# (the tick model's rule for paced carts, kept for the unpaced ones).
FREE_DT_MAX = 0.1

_SIG_FPS = 2                            # the WM's overlay sig bit of the FPS chip


def _deaf_btn(name, player=None):
    return False


class Player(PlayerDiag):
    """Runs one cart: start -> tick every frame -> guarantee exit (Stage 2). Holds a
    `ws` back-ref (the shared draw toolkit + services seam every surface uses) and is
    injected NAMES like the other surfaces. The cart-run fields it owns
    (ns/_update/_draw/cart_error/crash_line/_cart_start_ms) are exposed
    back on Workstation as forwarding properties, so every reader of ws.cart_error/
    ws.run.player.update/... is byte-for-byte unchanged; they are reset per run in start()."""

    play = None                      # the run's frame in C: Pace.bind
    press_owed = False               # a pointer press waiting for a logic tick
    stack_pre = None                  # the task's stack mark before the open: start
    # The task manager's accounting (runtime/shell/procs.py, `Procs.account`):
    # the microseconds this run's `_update` and `_draw` took since the last
    # sample, its window events counted with its updates; `cpu` the last
    # sample's busy microseconds a second (None before one); `mem` what its
    # start allocated -- its body and `_init`, the world it built -- in bytes,
    # None where the VM keeps no allocation count (scheduler.allocated).
    us_upd = 0
    us_draw = 0
    cpu = None
    mem = None

    def __init__(self, ws, NAMES, run):
        self.ws = ws
        self.run = run                # the Run it plays in (procs.Run): its canvas, project, audio, clock
        self.NAMES = NAMES
        self.declined = False         # the last start asked first (`Pace.music_keeps_the_vm`)
        # The run's owner tag: its layer loans, its run canvas and its audio
        # session are lent to it (runtime/shell/procs.py hands each run one).
        self.owner = "cart"
        # Detached: the run is one of the running set's beside the front
        # (runtime/shell/procs.py): it reads no input, keeps no edges, pulls
        # no mix and leaves by `quit_asked` instead of the caller's route.
        self.detached = False
        # Deaf: its desk window does not hold the keyboard (`set_deaf`).
        self.deaf = False
        self._deaf_ns = None          # (ns, btn, btnp) the deafness took
        self.quit_asked = False
        self.ns = None
        self.update = None
        self.draw = None
        self.cart_error = None        # last cart failure text -> on-canvas error panel
        self.pid = None               # this run's process in the OS log (os_log.begin)
        self.crash_seq = 0            # ...and its crash's record there, 0: none
        self._notice = None           # a notice's text: the panel is a notice
                                      # while cart_error still holds it (`notice`)
        self.notice_title = None  # ...and the title it is drawn under (None: the fit's)
        self._detail = None           # ...and its cause exact, under Details
        self.crash_line = None        # 1-based cart line of the last runtime crash (#24)
        self.crash_file = None        # WHICH of the cart's scripts that line is in
                                      # (SPEC.md 4), or None for main/no crash
        self._cart_start_ms = 0       # _ticks_ms when the running cart last start()ed
        self.verdict = None           # the run's (runtime, vm_free, why): _verdict
        self.front = False           # the run is the kernel loop's foreground
        self._front_want = False      # ...not yet, and the console's frames ask again
        self._stopping = False        # the kernel stops the VM for the run
        self.paused_at = 0           # _ticks_ms the pause menu held the run at (0: not held)
        self._cart_palette_canvas = None  # the canvas _cart_palette came off
        self._cart_palette = None     # default table saved while a cart's own
                                      # manifest palette (spec 2.2) is applied
        # #75: the tool/app-vs-game decision is a per-run CONSTANT (the manifest type
        # can't change mid-run), cached at start() so the per-frame tick/input paths
        # don't re-derive it through ws.running_cart_shows_bar (a property-forward
        # chain) on the sacred play path. Combined with the live cart_error check at
        # each use site it answers exactly what running_cart_shows_bar answers.
        self._tool = False
        self.script = False          # a `type: "script"` run: the shell's text console
                                      # is its screen (docs/theme_and_shell_2026-11.md §10)
        self._free = False            # a `"fps": "free"` game: unpaced, real dt (SPEC 5)
        # USER APP state (#181), all per-run and all reset in start():
        self._app_layout = None       # the cart's `_layout(w, h, fs)`, when it opted into
                                      # the RESPONSIVE canvas -- None for every game and
                                      # every fixed-canvas app (the default)
        self._app_wh = None           # the (w, h, fs) _layout was last told about, so a
                                      # window resize / font-scale change re-runs it and a
                                      # steady frame costs one tuple compare
        self._app_id = None           # the crash guard's key for this run (#160), or None
        # The run's permissions (runtime/shell/app_perms.py): the manifest's
        # granted ones and its absent ones.
        self.granted = ()
        self.absent = ()
        self._boundary = 1            # the app-cart boundary's mode while its code runs
                                      # (ui_draw.cart_text): 0 a built-in app's, which
                                      # draws in the look (system_api.draws_in_look); 2 a
                                      # responsive app's, whose `ui` verbs do
        self._responsive = False      # the app defines `_layout` (Player._wants_layout)
        self.ui = None               # a Python app's frame of ui verbs: [context, input, canvas]
        self._run_handle = 0          # the kernel Player's handle on this run, or 0
        self.app_ctx = None          # a user app's, script's or game's context: its run grant
        self._grant_perms = None     # a game's grant's permissions (_game_perms)
        self._files = None            # a Python run's files (_open_files)
                                      # when the run is not guarded
        self._restore_bg = None       # #63: the api's declared-background restore hook
        self._flag_put = None         # flag_edits' applier for the running cart
        self._wasm = False            # the run is a compiled cart's
        self.lua = None              # #67: the running runtime cart's state -- the
                                      # handle a ws.runtimes factory returned, Lua or
                                      # wasm; _close_lua() on exit so the cart's whole
                                      # heap dies with its run
        self.play = None             # the run's frame in C (moy_play), or None: Pace.bind
        self._lua_split = None        # its frame_split, bound ONCE per run: _run_ticks
                                      # asks after every tick
        self._sram_run = None         # #211: the Lua allocator's headroom report for the
                                      # run that ENDED, kept until the next run starts
        self._net = None              # #65: the running cart's net.* service, when it
                                      # has the "multiplayer" permission (else None); tick()
                                      # pumps inbound messages to its on_net handler
        self.netplay = None          # #65 Phase 2: the live two-console lockstep session
        self._drain_input = None      # pre-tick radio drain, bound by start()
                                      # (ws.netplay), when this run is a linked match. It
                                      # OWNS the tick: fixed dt, and a frame the peer's
                                      # input has not reached does not simulate at all
        self._pmem_sync = 0           # _ticks_ms a Lua run's pmem image was last read
        self._pmem_read = None        # the Lua run's image reader (`_pmem_tick`)
        self._native_ins = None       # #67 spike: nativize's inserted-line map (crash-line fix)
        # Diagnostics for repeat-run regressions (#66 follow-up): one line on cart
        # start and rate-limited slow-logic lines while PERF DIAG is on. Kept here
        # instead of moy_runtime so it tracks the actual lifecycle without touching
        # the device loop.
        self._run_seq = 0
        self._start_diag = None       # (reclaim,audio,api,compile,exec,init,total,free0,free1,alloc0,alloc1)
        self._slow_logic_next = 0
        self._native_fail = None      # reason for bytecode fallback, when auto-native fails
        # The tick model (#217): one scheduler per run. `tick_ms` is the cart's
        # tick period while a GAME is paced and 0 otherwise -- a flat attribute,
        # told to the kernel's loop (moy_loop.tick) where it paces frames.
        self.sched = TickScheduler()
        self._tick_dt = 1.0 / 30
        self.tick_ms = 0
        self.n_ticks = 1             # frame_plan's answer, run by tick()
        self._np_plan = True          # ...and in a match, whether a tick may start
        self.keyp_latch = 0          # a keyp edge waiting for a logic tick
        self.tick_edges = None       # ws.input.tick_edges, bound while paced
        self.keep_edges = None       # ws.input.keep_edges, bound while paced
        # The editor handles this run opened (#181/#112). A per-run list, like
        # the Lua tier's layer pins: they die with the world, flushed HARD on
        # the way out so a kid who taps X never loses a note (#154).
        self.editors = []
        self.focus_editor = None     # the handle holding the keyboard, if any
        # Its two parts: the app-cart face and the pacing half with the
        # front hand-off. A frame calls no method through either: the six
        # a frame runs are bound here, once.
        self.face = face = AppFace(self)
        self.pace = pace = Pace(self)
        self._ui_begin = face.begin
        self._ui_close = face.close
        self._ui_still = face.still
        self._ui_unchanged = face.unchanged
        self._resume_from_front = pace.resume_from_front
        self._front_again = pace.front_again

    def is_app(self):
        """Whether the run is a cart with the shell's bar -- a tool, an app or
        a script (`BAR_TYPES`) -- rather than a game, decided at its start."""
        return self._tool

    # -- the cart-facing text editor (#181/#112) ----------------------------

    def _open_cart_editor(self, files, kind, name, mode, canvas, clip=None):
        """Build one editor handle and PIN it to this run.

        Handed to `make_system_api` as its `editor` factory, so a cart reaches
        it only through the `open_editor` its manifest earned."""
        ed = EditorHandle(files, kind, name, mode, canvas, self._theme_colors,
                          clip=clip, host=self.ws, frame=self.face)
        self.editors.append(ed)
        return ed

    def _theme_colors(self):
        # The flat kernel token dict every shell surface reads per draw.
        return self.ws.theme_colors

    def set_editor_focus(self, handle):
        """The handle the keyboard belongs to, or None. Written by
        `Workstation.cart_editor_focus`; the keyboard's mode follows through
        `typing`. The one it replaces is blurred here, so two handles can
        never both believe they are being typed into; the keys let go inside a frame are the
        page's again in that frame (`api_keys_back`)."""
        prev = self.focus_editor
        if prev is not None and prev is not handle:
            prev._blur()
        self.focus_editor = handle
        if handle is None and prev is not None and self.face._pass and self.ui is not None:
            # Let go inside the frame (the cart's update closed it): the page
            # this frame draws has the keys back, so its focus marks now.
            _ui_draw._C.api_keys_back(self.ui[0])

    def focused_editor(self):
        """The handle holding the keyboard, or None -- what `ws.router.nav` asks
        before it spends a trackball roll on the cursor. None while the run
        is deaf: the keyboard is another window's."""
        return None if self.deaf else self.focus_editor

    def set_deaf(self, on):
        """Its desk window lost (`on`) or took back the keyboard
        (`Procs.frame` says, every frame): while deaf the run reads no key,
        no typed text and no button -- key(), keyp(), btn() and btnp()
        answer nothing, its editor handle and its ui frame take no key, and
        a Lua or compiled run's snapshot carries no input. Its pointer is
        the console's to strip (`player_has_pointer`)."""
        ns = self.ns
        took = self._deaf_ns
        want = ns if on and ns and "btn" in ns else None
        if on == self.deaf and want is (took[0] if took is not None else None):
            return
        if took is not None:
            self._deaf_ns = None
            if took[0] is ns:
                ns["btn"], ns["btnp"] = took[1], took[2]
        if want is not None:
            self._deaf_ns = (ns, ns["btn"], ns["btnp"])
            ns["btn"] = ns["btnp"] = _deaf_btn
        if on != self.deaf:
            self.deaf = on
            self.keyp_latch = 0
            m = getattr(self.lua, "mute", None)
            if m is not None and not self.detached:
                m(on)

    def _keep_grant(self, ctx):
        """Hold the context `make_system_api` built for this run: its grant
        is the run's, ended with it."""
        self._end_grant()
        self.app_ctx = ctx

    def _end_grant(self):
        """The run is over: its grant ends, and so does any store session it
        left open (a session holds no gate; runtime/shell/shell_servers.py).
        A Lua or compiled run closes first: a Lua state saves the files it
        left open at its close, through the grant, so the grant outlives it."""
        self._close_lua()
        ctx, self.app_ctx = self.app_ctx, None
        self._grant_perms = None
        if ctx is not None:
            ctx.end()
        self.ws.store.end_all()

    def _stop_grant(self, cart):
        """A compiled app, or a game declaring the app extension, the VM stops
        for (its imports all C-served, the
        verdict's rule) runs with no Python to make its grant: the grant is
        made here, before the stop, as make_system_api makes a user app's
        (the C policy and key, the ungated theme and surface beside its
        roles), and held in the kernel's state as the run the stop carries,
        which the kernel's open hands its imports. It is kernel state, so the
        stop leaves it; the start after the run ends it (console._app_abi)."""
        ws = self.ws
        app = getattr(ws, "app_abi", None)
        if (app is None or getattr(app, "stop_hold", None) is None
                or cart.get("runtime") != "wasm"
                or APP_EXT not in (cart.get("extensions") or ())
                or not (ws.spine.is_user_app(cart) or _wasm_ext_game(cart, ws.spine))):
            return
        perms = (_game_perms(cart, self.granted) if _wasm_ext_game(cart, ws.spine)
                 else self.granted)
        roles, kind = _moy_app.policy(perms)
        needs = roles + tuple(r for r in ("surface", "theme") if r not in roles)
        app_id = _moy_app.id_for(cart.get("id"), cart.get("title"))
        g = app.grant(app_id, needs, kind=kind, ns=app_id, run=True, owner=self.pid or 0)
        devs = getattr(app, "grant_devs", None)
        if devs is not None and self.granted:
            # its device handles' (moy_dev.h), and the folder they read its files in
            devs(g, tuple(self.granted), _devices.folder(cart))
        app.stop_hold(app.run(g, roles))

    def _heal_front_run(self):
        """A user app whose frames the kernel drew in its front (a compiled
        app on a board: no Python tick, so no `app_guard.frame`) heals at its
        end when the kernel's run drew the guard's frames and raised nothing,
        as a Python-ticked run heals during them."""
        guard = self.ws.app_guard
        info_of = getattr(_moy_play, "info", None)
        run, self._run_handle = self._run_handle, 0
        try:
            info = info_of(run) if info_of is not None and run else None
        except Exception:  # noqa: BLE001 -- no run, no heal
            info = None
        if info is not None and info[3] >= guard.HEAL_FRAMES and info[7] is None:
            guard.heal()

    def _app_run(self, cart):
        """The run the app ABI's tier bindings serve this launch
        (native/moy_app/moy_app_run.h): this run's grant and the roles its
        permissions name, for a compiled user app that declares the app
        extension, whose imports reach them, for a native app, whose rows
        reach them (runtime/run/elf_run.py), and for a Lua app or script,
        whose state opens their names (moy_app_lua.h). A game holds one too
        (`_game`): a Lua game's state opens `prefs` and no `os` ("game"
        among the roles), and a compiled game that declares the app
        extension or "moybyte.prefs" serves its imports from it. The run's own: the namespace hands it to the runtime's open,
        which copies it, so two runs hold two grants. None otherwise, so a
        module's imports from the extension are refused at load."""
        app = getattr(self.ws, "app_abi", None)
        run = getattr(app, "run", None)
        if run is None:
            return None
        ctx = self.app_ctx
        rt = cart.get("runtime") if cart is not None else None
        native_app = cart is not None and cart.get("type") == "app" and bool(cart.get("native"))
        exts = (cart.get("extensions") or ()) if cart is not None else ()
        if (ctx is None or not (rt == "lua" or native_app or (
                rt == "wasm" and (APP_EXT in exts or PREFS_EXT in exts)))):
            return run(0, ())
        perms = self._grant_perms if self._grant_perms is not None else self.granted
        roles = _moy_app.policy(perms)[0]
        if self._grant_perms is not None:
            roles += ("game",)          # its Lua state opens no app run's own
        return run(ctx.grant, roles)

    def flush_editors(self):
        """Write every handle this run opened that holds an edit: the run is
        being left (`_PlayerLayer.close`). The handles stay open."""
        for ed in self.editors:
            try:
                if ed.dirty():
                    ed.save()
            except Exception:  # noqa: BLE001 -- a leaving step must never raise
                pass

    def _release_editors(self):
        """Hard-flush and drop every handle this run opened."""
        eds = self.editors
        self.focus_editor = None
        if not eds:
            return
        self.editors = []
        for ed in eds:
            try:
                ed.close()
            except Exception:  # noqa: BLE001 -- teardown must never raise
                pass

    def _layout_args(self):
        """`(w, h, fs)` for a responsive app cart's `_layout` (#181).

        `w`/`h` come off the canvas the cart actually draws on -- the system
        surface when `bind_app_canvas` took, the fixed game raster otherwise --
        so a cart whose `_layout` was found but whose bind declined (the
        shared-canvas T-Deck, or a cart that also declared a small SPEC canvas)
        is told the truth rather than a size it cannot reach. `fs` is the shell's
        effective font scale only where the app owns the system surface; on the
        fixed game raster the cart draws its own pixels at 1x and the shell's
        text size is none of its business."""
        ws = self.ws
        cv = self.run.canvas
        w, h = ws.app_window_size() if self.run.app_window_canvas else (cv.w, cv.h)
        return (w, h, ws.look.effective_font_scale() if self.run.app_full_canvas else 1)

    def _layout_moved(self, tell=True):
        """Whether `_layout_args` moved from what `_layout` was told, told
        into `_app_wh` when it did (and `tell`) -- read field by field, so a
        steady frame builds no tuple."""
        ws = self.ws
        if self.run.app_window_canvas:
            wh = ws.app_window_size()
            w = wh[0]
            h = wh[1]
        else:
            cv = self.run.canvas
            w = cv.w
            h = cv.h
        fs = ws.look.effective_font_scale() if self.run.app_full_canvas else 1
        a = self._app_wh
        if a is not None and a[0] == w and a[1] == h and a[2] == fs:
            return False
        if tell:
            self._app_wh = (w, h, fs)
        return True

    def _map_crash_line(self, line):
        """Map a crash line reported against the NATIVIZED source back to the
        kid's original line (#67 spike / #24): subtract the @micropython.native
        decorator lines inserted at-or-before it. Identity when the cart ran the
        pristine bytecode path."""
        ins = self._native_ins
        if line is None or not ins:
            return line
        n = 0
        for pos in ins:
            if pos <= line:
                n += 1
            else:
                break
        return line - n

    def _restore_palette(self):
        """Put back the default palette table a cart-supplied one (spec 2.2)
        displaced. Idempotent; called from every exit path AND at start (so a
        re-run never saves a cart table as 'the default')."""
        if self._cart_palette is not None:
            # Onto the canvas that was SWAPPED, not whatever self.run.canvas is now:
            # a load failure releases the run canvas before this runs, and a
            # restore aimed at the system canvas left the native kernels' gate
            # table (owned by the run canvas) on the cart's palette -- every
            # native cart after a failed p8 load drew in PICO-8 colours.
            cv = self._cart_palette_canvas or self.run.canvas
            try:
                cv.palette = self._cart_palette
            except Exception:  # noqa: BLE001 -- restore must never block an exit
                pass
            self._cart_palette = None
            self._cart_palette_canvas = None

    def _xor_play_flag(self, n, mask):
        """A Python run's half of `ProjectStore.flag_edits`."""
        self.run.project.play_flags[n] ^= mask

    def _open_files(self, cart):
        """This run's Python files (`_python_files`), held to be let go."""
        self._close_files()
        self._files = _python_files(cart)
        return self._files

    def _close_files(self):
        files, self._files = self._files, None
        if files is not None:
            try:
                files.close()
            except Exception:  # noqa: BLE001 -- a leaving step must never raise
                pass

    def _close_lua(self):
        """Tear down the previous run's Lua state (#67), if any. Idempotent and
        exception-proof: a close() failure must never block the next start or
        the exit path -- the state is unreachable either way and gc finishes it."""
        lua = self.lua
        self.lua = None
        self._wasm = False
        self._lua_split = None
        self.play = None
        if lua is not None:
            try:
                lua.close()
            except Exception:  # noqa: BLE001
                pass
            # #211: the allocator's meters outlive the VM (moycore resets them
            # at run_begin, not at close), so the run's headroom is still
            # readable here -- which is what lets the exit report run AFTER the
            # heap it describes is gone.
            self._sram_run = _moycore_sram()

    def release_radio(self):
        """The run's radio leases go: the link's (its radio stopped) and the
        run's own. A run's teardown calls it, and so does a RETURN start
        (docs/history/kernel_cartpath_2026-10.md section 5.4), whose run ended in the
        kernel with no VM to run this teardown: the leases are the kernel's
        table, which the stop keeps."""
        # Nobody to talk to, and power save back on. UNLESS a session is already arranged for the run about to
        # start -- which is not a hypothetical: the way a match forms is that the
        # peer's invite arrives while this console is already playing, sets
        # ws.netplay, and re-runs the cart from frame zero. The dying run tears
        # down here on the way through, and stopping the radio then killed the
        # very session that triggered the restart. On glass that read as the host
        # stalling at frame 0 forever while the guest played on alone, with
        # nothing logged anywhere (#65). release_world has already
        # cleared ws.netplay if the session belonged to THIS run, so what
        # survives here belongs to the next one.
        _link = getattr(self.ws, "link", None)
        if _link is not None and self.ws.netplay is None:
            try:
                _link.stop()
            except Exception:  # noqa: BLE001 -- teardown must never block an exit
                pass
            self.ws.spine.wifi_release("link")
        # ...and the run's own lease, which its process's end let go; a run
        # that ended in the kernel has no process left to name, so every
        # "cart" hold goes (one run holds it at a time). Nothing else holds
        # the WiFi at a plain exit, so this is where a console goes back to
        # radio-off.
        self.ws.spine.wifi_release("cart", 0)

    def release_world(self):
        """Drop the dead run's WORLD at EXIT, not at the next start (#66 the
        repeat-run fragmentation fix, glass-fingerprinted 2026-07-10): the ns
        used to linger until the NEXT cart's start() -- go_home() nulled ws.ns
        but never _update/_draw, whose function objects close over the ns dict
        and keep the whole ~300-400KB world alive at the home screen. The next
        cart then BUILT ITS WORLD AROUND the lingering one; when that finally
        freed, the new world sat in a minefield of holes, and MicroPython's gc
        never compacts -- measured: sakura logic 6.5ms fresh vs 13-14ms after
        three other carts, with an IDENTICAL live set. Clearing the ns dict IN
        PLACE breaks the globals for every retained function ref; the layer
        reclaim + collect leave a compact heap for the next build. Idempotent;
        safe on the crash path (the error panel reads cart_error, never ns)."""
        self.front = False
        self._front_want = False
        self.paused_at = 0
        self.pace.audio_held = 0
        _os_log.end(self.pid)
        _moybyte.end(self.pid)
        self.pid = None
        self._close_files()
        # The dead run's `view(w, h)` must not outlive it: every fullscreen
        # surface shares viewport(), and a lingering view would crop chrome.
        self.run.view = None
        # Nor its per-run cart canvas (SPEC.md 1/3.1) -- whoever ran us draws
        # on the boot raster, so the small canvas dies with the run. (Also the
        # RESPONSIVE app-cart bind, #181: same field, same release.)
        self.ws.binding.release_run_canvas()
        # ...and the root's fold scratch its frames were shown through (a
        # canvas without one has no release_fold).
        rf = getattr(self.ws.sys_canvas, "release_fold", None)
        if rf is not None:
            rf()
        # A dirty EDITOR HANDLE is written HERE and nowhere else (#154): this
        # is the one place every exit path passes -- the tap on X,
        # quit(), a workspace swap, a crash -- so a kid's note is
        # saved by leaving, exactly as a code tab's half-typed Python is.
        self._release_editors()
        # A USER APP's crash-guard arming dies with its run. Any STRIKE it took
        # stands -- an exit before the heal is precisely the evidence kept.
        self._app_layout = None
        self._app_wh = None
        self.face.reset()
        # The guard reads the kernel's run, which the run's close ends.
        if self._app_id is not None:
            self._app_id = None
            self._heal_front_run()
            self.ws.app_guard.release()
        self._end_grant()
        # Nor may its SOUND. The device mixer is a global the cart only ever
        # posts notes to -- libmoy keeps sequencing a looping sfx or music track
        # long after the run that started it is gone, so a demo cart's tones
        # and celeste's music went on playing over whatever the kid did next
        # (owner, T-Deck). Same category as the view and the palette above:
        # state the run set on a shared surface, cleared where the run ends
        # rather than wherever the next one happens to overwrite it.
        au = self.run.audio
        if au is not None:
            try:
                au.music_stop()
                au.sound_stop()
            except Exception:  # noqa: BLE001 -- teardown must not raise
                pass
        # A cart-supplied palette (spec 2.2) dies with its run -- the system
        # surfaces underneath get the default table back.
        self._restore_palette()
        ns = self.ns
        # Was there a world to drop? The collect below and the exit diags are
        # for the heap a RUN leaves behind; go_home reaches here from the desk
        # and the Editor too, where nothing ran and a collect is pure pause --
        # 440ms of the PLAY tap on a Guition P4 (2026-09-09).
        had_world = bool(ns) or self.update is not None \
            or self.draw is not None or getattr(self, "lua", None) is not None
        if ns:
            try:
                ns.clear()
            except Exception:  # noqa: BLE001
                pass
        self.ns = None
        self.update = None
        self.draw = None
        self._restore_bg = None
        # #65: drop the net handler/inbox with the dead run so a stale on_net
        # callback (closing over the cleared ns) can't fire into the next run.
        if self._net is not None:
            try:
                self._net.reset()
            except Exception:  # noqa: BLE001 -- reset must never block an exit
                pass
        self._net = None
        # The match dies with the run: drop the session's player slots so the
        # console is back to one local player the moment the cart is gone.
        if self.netplay is not None:
            try:
                self.netplay.close()
            except Exception:  # noqa: BLE001 -- teardown must never block an exit
                pass
            self.netplay = None
            self.ws.netplay = None
        try:
            self.ws.input.netplay_live = False
            self.ws.input.match_clock = None
        except Exception:  # noqa: BLE001
            pass
        # ...and the radio goes down with it.
        self.release_radio()
        self._close_lua()          # #67: the dead run's Lua heap goes with its world
        # The dying run's last save, once a Lua or compiled run's close has
        # handed its image to the Pmem and BEFORE the world drops
        # (self.run.project still points at this run's Project here -- both exit
        # call sites run release_world ahead of replacing/slimming it).
        self._pmem_flush()
        rl = getattr(self.run.canvas, "reclaim_layers", None)
        if rl is not None:
            try:
                # pool the dead run's layer buffers now. The owner arg was
                # missing until 2026-08-04: reclaim_layers(owner) raised a
                # TypeError this except swallowed, so the release_world
                # reclaim was silently dead and only the NEXT start() pooled
                # (found by the #186 audit).
                rl(self.owner)
            except Exception:  # noqa: BLE001
                pass
        if had_world:
            try:
                import gc
                gc.collect()       # off the play path: the run just ended
            except Exception:  # noqa: BLE001
                pass
            self._diag_frag()      # #66: the heap the NEXT cart inherits
            self._diag_sram()      # #211: the internal SRAM the run just had
        self._reset_stage_meters()  # #210: the shell does not inherit the run's
        self.pace.disarm()

    def _maybe_diag_slow_logic(self, upd_us, render_us, audio_us):
        # Microseconds since 2026-08-14 (see the note at the call site); the gate
        # and the printed line stay in ms so SLOWLOGIC reads as it always has.
        upd_ms = upd_us // 1000
        render_ms = render_us // 1000
        audio_ms = audio_us // 1000
        # PERF DIAG's alone: a periodic line, and the HUD a kid can tap on
        # (perf_hud) is no measurement session.
        if upd_ms < 10 or not getattr(self.ws, "diag_live", False):
            return
        now = _ticks_ms()
        if self._slow_logic_next and _ticks_diff(now, self._slow_logic_next) < 0:
            return
        self._slow_logic_next = now + 2000
        # NO _heap_stats() here (2026-08-03): gc.mem_free()+gc.mem_alloc() each
        # walk the whole 8MB heap table -- ~150ms on the T-Deck, landing right
        # after the perf brackets close, i.e. charged to CHROME. With Brick
        # Siege tripping the >=10ms gate at this 2s rate limit's ceiling, the
        # diagnostic itself was the single largest stutter source in every
        # measured build (ledger included: 20 of its 31 play hitches), and it
        # arms off perf_hud too -- watching the FPS chip injected it.
        self._print_run_diag(
            "SLOWLOGIC",
            "upd=%d render=%d audio=%d" % (upd_ms, render_ms, audio_ms),
        )

    def start(self, project):
        """Start (or re-run) `project`'s cart under make_api. Resets the canvas draw
        state, stamps the cart clock, gates `wifi` by the manifest permission, execs
        the source, and captures _update/_draw. Any exception becomes cart_error/
        crash_line (returned False) so the caller opens to the desktop and frame()
        paints the on-canvas panel instead of hanging. Returns True iff it started."""
        ws = self.ws
        self._run_seq += 1
        t0 = _ticks_ms()
        # Heap stats only in measurement mode: each _heap_stats() is two full
        # heap-table walks (~150ms on device). (-1, -1) is already the host
        # sentinel, so the RUNSTART/RUNERR phases(...) format never changes.
        _hs = _heap_stats if self._diag_enabled() else (lambda: (-1, -1))
        h0 = _hs()
        cart = self._begin_run(ws, project, t0)
        self._log_begin(cart)
        self.verdict = _verdict(cart)
        newer = _format_newer(cart)
        if newer is not None:
            return self._refuse_notice(newer_notice([newer]),
                                       _ch.say_title(_ch.SAY_NEWER))
        err = self._gate_spec(ws, cart)
        if err is not None:
            return self._refuse(err)
        # The tier the cart runs on here (app_perms.tier_of): a native cart's
        # is "elf" whatever its runtime field says, and a compiled cart that
        # carries both modules runs the one this console and its consent pick.
        _rt = _perms.run_tier(ws, project.cart)
        self._wasm = _rt == "wasm"
        run = 0
        self._run_handle = 0
        if _rt != "python":
            # The kernel decides the stop from the catalogue entry before
            # anything of the run is built here (docs/history/kernel_cartpath_2026-10.md
            # section 5.2): an audio session or a buffer made with the VM up
            # would sit inside the free run the stop is about to widen.
            run = self.pace.launch(cart)
            self._run_handle = run
            if run and self.pace.music_keeps_the_vm(run, cart):
                return False
            if run and self.pace.stop_for(run, cart):
                return True
        t_reclaim, t_audio = self._prepare_world(ws, project, cart, t0)
        wifi, net, gpio, console = self._grant_services(ws, project, cart)
        # #65 Phase 2: a linked two-console match. THIS is the moment a solo run
        # becomes one -- before the seed is drawn and before _init runs, so both
        # consoles' rnd() start from the same place. The guest side has already
        # been handed a session by its radio (link._on_start), so it just finds
        # ws.netplay set; the host side offers here and becomes player 0.
        link = getattr(ws, "link", None)
        if net is not None and link is not None:
            # ARM the radio here and nowhere earlier. It is only up while a game
            # that could use it is running, because disabling power save (which
            # is what buys the latency tail) costs battery on a handheld, and a
            # console on a shelf has nobody to talk to. Announcing the cart is
            # what lets a peer recognise "we are both in the same game".
            try:
                ws.spine.wifi_hold("link")   # the STA up, owned by the wifi service, BEFORE the radio
                link.start()
                link.announce(cart.get("title") or "", 1, fps=cart.get("fps"))
                # A DEAD match never survives into the next run (#65). The
                # link's own give-up paths clear it as they re-run the cart,
                # but a re-run arriving from anywhere else -- Editor PLAY, the
                # dev channel's `run`, a kid re-launching from the shelf --
                # would otherwise adopt a session whose advance() can only
                # ever stall, and the cart would never simulate again.
                _np = ws.netplay
                if _np is not None and getattr(_np, "dead", False):
                    link.end_match(ws)
                if ws.netplay is None:
                    link.offer(ws, cart.get("title") or "")
            except Exception as exc:  # noqa: BLE001 -- a radio must never block a cart
                print("Moybyte link failed:", exc)
        # Gated on the same permission: an unlinked console leaves this None and
        # the cart runs exactly as a single-player cart does.
        self.netplay = ws.netplay if net is not None else None
        # The pre-tick radio drain (see the frame loop below). Bound once: a
        # linked run drains the link it started with, the way it keeps the
        # session it started with.
        self._drain_input = None
        if self.netplay is not None and link is not None:
            self._drain_input = getattr(link, "drain_input", None)
        # The cart api reads this to refuse touch()/mouse() for the duration:
        # only buttons cross the radio, so a pointer would move one screen's
        # player and not the other's. Set on the InputState because that is what
        # make_api is handed.
        try:
            ws.input.netplay_live = self.netplay is not None
            ws.input.match_clock = self.netplay  # time() reads the match's clock
        except Exception:  # noqa: BLE001 -- a bare test stub need not carry it
            pass
        if self.netplay is not None and self.netplay.config:
            # The host's tuning wins for the duration of the match. Applied to
            # the LIVE dict rather than written to the card: it is a property of
            # this match, not a change to the kid's own copy of the cart.
            try:
                project.config.update(self.netplay.config)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte link config failed:", exc)
        t2 = _ticks_ms()
        ns = self._build_namespace(ws, project, cart, wifi, net, gpio, console)
        t_api = _ticks_diff(_ticks_ms(), t2)
        src = project.cart["src"]
        # The driving task's stack high-water mark before the runtime opens,
        # beside the Player's own readings after the open and the frames
        # (moy_play.info): what the cart path costs the S3s' stacks.
        _st = getattr(_moy_play, "stack", None)
        self.stack_pre = _st() if _st is not None else None
        if _rt != "python":
            ok = self._start_runtime(_rt, ns, src, t0, h0,
                                     (t_reclaim, t_audio, t_api))
            if ok:
                self.pace.arm(cart)
                self.pace.bind(run)
                self.front = self.pace.take_front()
            elif run:
                _moy_play.end(run, _moy_play.END_CRASH)
            return ok
        code, t_compile_native, _ckey, _csig = self._compile_python(project, src, ns)
        if self.netplay is not None:
            # The desync check's world digest, when the link has it armed:
            # the cart's globals entering a checkpoint frame.
            self.netplay.world = lambda: _world_text(ns)
        try:
            t4 = _ticks_ms()
            if code is None:
                # The kid's own syntax error surfaces HERE -> the friendly panel.
                code = compile(src, "<cart>", "exec")
            # Cache the outcome (native OR bytecode-fallback) for this source
            # version -- a repeat PLAY re-execs the same code object (see above).
            _CODE_CACHE[_ckey] = (_csig, code, self._native_ins, self._native_fail)
            t_compile = t_compile_native + _ticks_diff(_ticks_ms(), t4)
            t5 = _ticks_ms()
            _was = _cart_text(self._boundary)
            a0 = _allocated()
            try:
                exec(code, ns)
                t_exec = _ticks_diff(_ticks_ms(), t5)
                t6 = _ticks_ms()
                # A RESPONSIVE app cart (#181) is told its geometry BEFORE _init, so
                # the cart's own state is built against the size it will draw at
                # rather than against a default it then has to correct.
                if self._app_id is not None:
                    self._app_layout = ns.get("_layout")
                    if self._app_layout is not None:
                        self._app_wh = self._layout_args()
                        self._app_layout(*self._app_wh)
                if ns.get("_init"):
                    ns["_init"]()
            finally:
                _cart_text(_was)
                a1 = _allocated()
                self.mem = (a1 - a0) & _ALLOC_WRAP if a0 is not None and a1 is not None \
                    else None
            t_init = _ticks_diff(_ticks_ms(), t6)
        except Exception as exc:  # noqa: BLE001
            # The device's native run loop starves USB, so a print() never reaches
            # serial -- stash the failure so tick() can paint an on-canvas panel.
            # Print only the _err_text-guarded string, never the raw `exc`: a cart
            # exception whose __str__ itself raises would otherwise escape here and
            # become the exact silent device hang the panel exists to prevent.
            self.cart_error = _err_text(exc)
            self._crashed(exc, self.cart_error)
            self.crash_line = self._map_crash_line(_exc_cart_line(exc))
            self.crash_file = None      # the python tier runs main and nothing else
            h1 = _hs()
            self.ns = ns
            self._start_diag = (t_reclaim, t_audio, t_api,
                                locals().get("t_compile", t_compile_native),
                                locals().get("t_exec", -1),
                                locals().get("t_init", -1),
                                _ticks_diff(_ticks_ms(), t0),
                                h0[0], h1[0], h0[1], h1[1])
            self._print_run_diag("RUNERR", "err=%s" % self.cart_error)
            print("Moybyte cart error:", self.cart_error)
            return False
        self.cart_error = None
        self.crash_line = None
        self.crash_file = None
        self.ns = ns
        self.update = ns.get("_update")
        self.draw = ns.get("_draw")
        # Declared background (#63): the api's frame-start restore hook. Cached here so
        # tick() pays one attribute read; it early-outs when the cart declared nothing.
        self._restore_bg = ns.get("_moy_restore_bg")
        h1 = _hs()
        self._start_diag = (t_reclaim, t_audio, t_api, t_compile, t_exec, t_init,
                            _ticks_diff(_ticks_ms(), t0),
                            h0[0], h1[0], h0[1], h1[1])
        self.pace.arm(cart)
        self._print_run_diag("RUNSTART")
        return True


    def _log_begin(self, cart):
        """This run is a process of the OS log (runtime/system/os_log.py): a
        pid named by the cart's id, current until the run's world is
        released; a re-run without a release ends the last one first."""
        _os_log.end(self.pid, "restart")
        _moybyte.end(self.pid)
        c = cart or {}
        self.pid = _os_log.begin(c.get("id") or c.get("title") or "cart")
        self.crash_seq = 0

    def _crashed(self, exc, text):
        """The crash intake: the run's failure into the OS log at crit, the
        Python traceback where there is one, else the panel's words."""
        self.crash_seq = _os_log.crashed(self.pid, exc, text)

    def _refuse(self, msg):
        """Refuse a run before any of the cart's code has run: the ordinary
        error panel, with no line to mark."""
        _os_log.failed(self.pid, msg)
        self.cart_error = msg
        self.crash_line = None
        self.crash_file = None
        return False

    def _refuse_notice(self, text, title, detail=None):
        """Refuse a run before any of the cart's code has run, on the panel
        drawn as a notice under `title`, `detail` its cause exact."""
        self._refuse(text)
        self._notice = text
        self.notice_title = title
        self._detail = detail
        return False

    def _begin_run(self, ws, project, t0):
        """Reset the previous run's world and stamp this run's per-run flags.
        Returns the cart."""
        ws.mark_dirty()               # a (re)started cart paints its first frame (#44)
        self._reset_stage_meters()     # #210: this run's misses are its own
        self.us_upd = self.us_draw = 0
        self.cpu = self.mem = None
        self.pace.disarm()          # re-armed below, once the cart is running
        # USER APP per-run state (#181): cleared here rather than at the bind
        # site below, so the early SPEC refusals (extensions / canvas) cannot
        # leave a previous app's `_layout` armed against this cart.
        self._app_layout = None
        self._app_wh = None
        self._app_id = None
        self.granted = ()
        self.absent = ()
        self._boundary = 1
        self._responsive = False
        self.face.reset()
        self._end_grant()
        self._release_editors()        # a RE-RUN is an exit too: flush, then drop
        self.run.view = None      # the `view(w, h)` verb is per-run (cart_quit
                                       # pattern): a cart re-declares it each start
        # The run's own map and scenes, fresh from the Editor's (E-02): a fresh
        # Scenes stands at its default scene with no world, so a load_scene()
        # switch never leaks across a re-run (#85's "resets on next _init").
        project.play_world(True)
        self._flag_put = self._xor_play_flag   # a Python run's flags; a Lua run's below
        self._close_lua()              # a re-run replaces the previous run's Lua state
        # #63 leak fix: the PREVIOUS cart is dead and moycore has let go of it
        # -- return everything on loan to it (its make_layer worlds, its run
        # canvas, its paint bakes) before this run binds and allocates its own.
        # Probe: a canvas with no glass has nothing to return.
        rl = getattr(self.run.canvas, "reclaim_layers", None)
        if rl is not None:
            rl(self.owner)
        self._t_reclaim = _ticks_diff(_ticks_ms(), t0)
        self._sram_run = None          # #211: whatever it reported was the LAST
                                       # run's; a Python cart must not inherit it
        self._pmem_sync = t0
        self._slow_logic_next = 0
        # #75: cache the bar-visibility-by-type rule for this run (see __init__).
        cart = project.cart
        self._tool = cart is not None and cart.get("type") in BAR_TYPES
        # A SCRIPT (docs/theme_and_shell_2026-11.md §10): a cart with no folder whose
        # screen is the shell's text console. Tool-shaped -- one unpaced tick
        # per loop frame, the minimal bar's X to leave. Its responsive bind is
        # below, WITH the app one: `release_run_canvas` sits between here and
        # there and would drop it.
        self.script = cart is not None and cart.get("type") == SCRIPT_TYPE
        return cart

    def _gate_spec(self, ws, cart):
        """The SPEC gates a cart must pass before its code runs -- required
        extensions, the canvas size, a user app's manifest and crash guard --
        binding the run's canvas on the way. Returns the refusal text, or None
        when the cart may start."""
        # Required-extension gate (moy SPEC.md 10): a cart listing an extension
        # this build doesn't implement is refused cleanly -- the normal error
        # panel -- instead of crashing partway into a frame on a missing verb.
        missing = [e for e in ((cart.get("extensions") or ()) if cart else ())
                   if e not in SUPPORTED_EXTENSIONS]
        if missing:
            return "needs extension: " + ", ".join(missing)
        # The launch check (runtime/shell/app_perms.py): what Settings turned
        # off, what the console lacks, a permission an update added unasked
        # are absent; a `required` one absent refuses the run by name.
        if cart:
            granted, absent, refusal = ws.consent.check(cart)
            if refusal is not None:
                return refusal
            self.granted = tuple(granted)
            self.absent = tuple(absent)
        # Cart canvas gate (SPEC.md 1/3.1): `canvas` is the other capability
        # field -- an out-of-set size was carried raw by the loader and is
        # refused here BY NAME, like an unknown runtime; an in-set size binds a
        # real small canvas for the run (released in release_world). A tier
        # with no factory refuses too: running a 128x128 cart on a 320x240
        # canvas would letterbox it into a corner and lie about W/H.
        cv = cart.get("canvas") if cart else None
        if cv is not None and (not isinstance(cv, (tuple, list)) or len(cv) != 2):
            # clip the repr: a malformed value can be an arbitrary object and
            # the panel wraps 8px cells -- the NAME matters, not the whole blob
            return 'no "%.32s" canvas (SPEC.md 3.1)' % (cv,)
        ws.binding.release_run_canvas()        # a straggler bind never leaks into this run
        if cv is not None and not ws.binding.bind_run_canvas(cv[0], cv[1]):
            return "no %dx%d canvas on this screen yet" % (cv[0], cv[1])
        # -- USER APPS (#181, ui_refactor_2026-08 Phases 7 + 8) ---------------
        #
        # A `type: "app"` cart no shell app claims is a USER APP: written by
        # whoever owns the console, editable on Make, and handed shell
        # capabilities by MANIFEST PERMISSION (make_system_api, below).
        if ws.spine.is_user_app(cart):
            # A manifest this build cannot honour AS WRITTEN is refused by
            # name, like an unknown runtime or an out-of-set canvas -- and
            # BEFORE the crash guard arms, because a mis-declared permission is
            # not a crash and must not spend a strike. Today the only such rule
            # is "at most one file kind"; the alternative was the silent
            # last-one-wins grant this replaced.
            _man = _moy_app.manifest_error(cart.get("permissions"))
            if _man is not None:
                return _man
            # CRASH ISOLATION FIRST, before a single line of the cart's code has
            # been compiled, let alone run: the mark has to survive a death the
            # interpreter never observes (a hang, an OOM, a native fault). See
            # runtime/shell/crash_guard.py. A cart that has used up its strikes is
            # refused into the ORDINARY error panel, whose top bar carries
            # EDIT/CODE -- so "this app is broken" and "here is how you fix it"
            # are the same screen.
            self._app_id = ws.spine.app_cart_id(cart)
            self._boundary = 0 if draws_in_look(cart) else 1
            if not ws.app_guard.arm(self._app_id):
                self._app_id = None
                return ("app turned off after %d crashes - EDIT it"
                                   % ws.app_guard.STRIKES)
            # THE RESPONSIVE OPT-IN. Fixed is the DEFAULT and deliberately so:
            # a kid cart hardcodes coordinates, and the manifest `canvas` (320x240
            # unless it says otherwise) is what the WM centres and integer-scales.
            # A cart that defines a top-level `_layout(w, h, fs)` is saying it can
            # reflow, and gets the whole system surface instead. Probed before
            # the cart runs because the canvas has to be chosen before make_api
            # closes over it -- see `_wants_layout`.
            if self._wants_layout(ws, cart):
                # its `ui` verbs draw in the shell's look, on every tier
                # (docs/app_api_v1.md section 3): the boundary keeps its print
                self._responsive = True
                self._boundary = self._boundary and 2
                ws.binding.bind_app_canvas()
        # A SCRIPT is responsive by construction -- the SHELL draws it, and text
        # is the one thing an integer upscale of the 320x240 raster serves worst
        # -- so it takes the same whole-surface bind an app opts into, with no
        # opt-in to make. (Refused in the windowed desk world for the reason the
        # bind's own docstring gives, where it stays the fixed raster in a
        # window like any cart.)
        elif self.script:
            ws.binding.bind_app_canvas()
        return None

    @staticmethod
    def _wants_layout(ws, cart):
        """Whether the app cart defines `_layout(w, h, fs)`, asked of its
        tier before anything of it runs: a Python cart's source
        (`system_api.wants_layout`); any other tier's runtime answers
        `wants_layout(cart)` -- a Lua cart's scripts (lua_ext), a compiled or
        WASI app's module exports, a native app's dynamic symbols (elf_run).
        A runtime with no such answer, or one that fails, is fixed."""
        rt = _perms.run_tier(ws, cart)
        if rt == "python":
            return wants_layout(cart.get("src"))
        probe = getattr((getattr(ws, "runtimes", None) or {}).get(rt), "wants_layout", None)
        if probe is None:
            return False
        try:
            return bool(probe(cart))
        except Exception:  # noqa: BLE001 -- a probe is advisory; the run stays fixed
            return False

    def _prepare_world(self, ws, project, cart, t0):
        """Build the audio, reset the canvas state, install a declared palette
        and stamp the cart clock. Returns the (reclaim, audio) ms for the
        RUNSTART diag; the reclaim is _begin_run's."""
        t_reclaim = self._t_reclaim
        t1 = _ticks_ms()
        project._build_audio()
        t_audio = _ticks_diff(_ticks_ms(), t1)
        # Reset the canvas draw state (camera/clip/pal/palt, #11) so a fresh cart run
        # never inherits a previous cart's clip rect or palette swap.
        rs = getattr(self.run.canvas, "reset_state", None)
        if rs is not None:
            rs()
        # Cart-supplied palette (moy SPEC.md 2.2): 64 "RRGGBB" strings in the
        # manifest replace the default table for this run; layers made during
        # the run inherit it (make_layer shares canvas.palette), and every exit
        # path restores it (release_world). Indexed backends without a live
        # .palette (the device compositor's baked RGB565 LUT) skip it -- a
        # recorded conformance gap there, never a crash.
        self._restore_palette()        # a re-run must never stack two swaps
        pal = cart.get("palette") if cart else None
        if pal and len(pal) == 64 and getattr(self.run.canvas, "palette", None):
            try:
                table = []
                for s in pal:
                    v = int(s.lstrip("#"), 16)
                    table.append(((v >> 16) & 255, (v >> 8) & 255, v & 255))
            except (ValueError, AttributeError, TypeError):
                table = None               # malformed -> keep the default table
            if table is not None:
                self._cart_palette = self.run.canvas.palette
                self._cart_palette_canvas = self.run.canvas
                self.run.canvas.palette = table
        # Stamp the cart-start clock so the cart's time() reads ms since this run
        # began (re-run on apply/run_code/edit-close resets it, like TIC-80).
        self._cart_start_ms = _ticks_ms()
        self.run.cart_start_ms = self._cart_start_ms
        return t_reclaim, t_audio

    def _grant_services(self, ws, project, cart):
        """The capability services this cart's manifest permissions grant --
        (wifi, net, gpio, console) -- each None when not declared."""
        # Capability-permission gate (#38): hand make_api the wifi backend ONLY
        # when this cart declares the "network" permission, so a normal kid cart
        # gets NO `wifi` name (sandbox preserved). make_api injects `wifi` into the
        # cart namespace iff the backend it receives is non-None.
        wifi = ws.wifi if ws.cart_has_perm("network") else None
        if wifi is not None:
            ws.spine.wifi_hold("cart", self.pid)  # the radio lease for this run; its end lets go
        # Multiplayer message service (#65): gate net.* by the "multiplayer"
        # manifest permission exactly like wifi's "network" gate. reset() drops any
        # handler/inbox from a previous run so a fresh run starts clean; make_api
        # injects `net`/`on_net` into the namespace iff the backend is non-None.
        net = ws.net if ws.cart_has_perm("multiplayer") else None
        # Physical pins (#9), gated HERE with its two siblings rather than in the
        # tier that happens to supply it. It used to be injected by the browser
        # tier's own make_api wrapper the moment the serving host answered the pin
        # probe -- which gave it the CAPABILITY half of the gate (does this
        # console have pins) and none of the CONSENT half (did this cart ask),
        # while cart_api's comment claimed it was gated "the same way as wifi and
        # net". It was not, and the difference stopped being theoretical once a
        # dropped .p8 could land in a board's own store: a cart nobody wrote
        # could move a pin nobody declared. The pin ALLOWLIST bounds which pins,
        # never which carts.
        gpio = ws.gpio if ws.cart_has_perm("pins") else None
        # The TEXT CONSOLE, gated on the "console" permission exactly as wifi is
        # on "network": a cart that never declares it keeps `print` as the DRAW
        # verb, and a script -- whose synthesized manifest DOES declare it --
        # gets the scrollback instead. The terminal (#115) declares it too.
        console = (getattr(ws, "script_console", None)
                   if ws.cart_has_perm("console") else None)
        if net is not None:
            net.reset()
        self._net = net
        return wifi, net, gpio, console

    def _build_namespace(self, ws, project, cart, wifi, net, gpio, console):
        """The cart's namespace: make_api over the project, the artwork and
        system-api capabilities a shipped app / user app / script is granted,
        the text console's verbs, and the other scripts either side of main."""
        ns = ws.make_api(self.run.canvas, ws.input, project.config, project.sheet,
                         self.run.audio, project.play_map, project.pmem, wifi, project.images,
                         project.play_scenes,   # #85: scene()/load_scene(), the run's own
                         net=net,           # #65: capability-gated net.* backend
                         gpio=gpio,         # #9: capability-gated physical pins
                         flags=project.play_flags,   # SPEC.md 3.5: the run's own flags
                         owner=self.owner,  # its layer loans end with it
                         run=self.run,      # its view() and its clock
                         files=self._open_files(cart))   # SPEC.md 9.1
        # Paint is a regular cartridge with one narrow shell capability. Keep it out
        # of the kid API and inject it only into the shipped app identity that asks
        # for the artwork permission; copied/renamed carts do not inherit it.
        if (getattr(ws, "artwork", None) is not None
                and ws.artwork.is_paint_app(cart)):
            ns["artwork"] = ws.artwork
        # USER APP capability injection (#181): the cart's manifest permissions,
        # intersected with `system_api`'s allowlist, become names in its
        # namespace -- and a capability it did not declare has NO NAME AT ALL,
        # exactly like `wifi` above. Same AppContext the shipped apps get,
        # through the same `ws.app_context` factory; only the source of the
        # `needs` tuple differs (a manifest here, a class constant there). The
        # ungated riders (`ui`, `theme()`, `screen()`, `content_rect()`,
        # `bar_h()`) are how an app draws, not what it may reach -- see
        # runtime/shell/system_api.py.
        # A SCRIPT is handed the same filter by the same call: its permissions
        # come from the manifest `run_script` synthesized, so the refusing is
        # the machinery that already refuses, and `carts` is not grantable to
        # anything (no permission maps to it, native/moy_app's policy).
        if self._app_id is not None or self.script:
            ns.update(make_system_api(ws.app_context, cart, self.run.canvas,
                                      editor=self._open_cart_editor,
                                      request=ws.take_text_request(),
                                      keep=self._keep_grant, perms=self.granted,
                                      owner=self.pid))
            if "ui" in ns:
                self.face.bind(ns, cart)
        elif _game(cart, ws.spine):
            perms = _game_perms(cart, self.granted)
            ctx = make_run_grant(ws.app_context, cart, keep=self._keep_grant,
                                 perms=perms, owner=self.pid)[0]
            self._grant_perms = perms
            if cart.get("runtime", "python") == "python":
                ns["prefs"] = ctx.prefs
        if console is not None:
            ns.update(console.api())
        # A permission the manifest lists that Settings turned off reads None
        # in a Python cart, never NameError (app_perms.absent_names).
        if self.absent and cart.get("runtime", "python") == "python":
            rec = ws.consent.get(_perms.app_id(cart))
            for name in _perms.absent_names(self.absent, rec):
                ns[name] = None
        # The cart's OTHER SCRIPTS, either side of main (SPEC.md 4). A ported
        # cart's generated half -- data tables + the compat shim -- lives in
        # p8.lua and runs ahead of main.lua, so main.lua is the cart's own code
        # and an error in the shim reads `p8.lua:N:`, never landing on the kid's
        # line in the crash-to-code panel. Each is its own chunk, which is what
        # lets them be separate files at all.
        #
        # They ride the namespace like `_moy_prelude`, the same mechanism the
        # text console's binding uses; both lists are empty for every cart that
        # declares no `sources`, and lua_ext.cart_chunks then builds the same
        # one-entry list the tiers always ran.
        pre = project.cart.get("src_before")
        if pre:
            ns["_moy_pre"] = pre
        post = project.cart.get("src_after")
        if post:
            ns["_moy_post"] = post
        # #67 dual-runtime seam (Phase 2): a non-python cart never touches the
        # Python compile / auto-native / code-cache path below -- it starts
        # through the injected Lua runtime instead (same ns, same error panel).
        return ns

    def _compile_python(self, project, src, ns):
        """Compile a Python cart, preferring the auto-native rewrite through
        the per-source code cache. Returns (code, native_compile_ms, cache key,
        source signature); code is None when the emitter refused and the
        pristine compile must run -- the caller stores that outcome under the
        key."""
        # Compile with the "<cart>" filename so a runtime traceback carries cart
        # line numbers (_exc_cart_line reads them to mark the bad line). #67 spike:
        # prefer the AUTO-NATIVE rewrite (machine code per top-level def) when the
        # emitter exists; if it refuses the cart at compile time, fall back to the
        # pristine bytecode compile -- the flag can never break a cart. The
        # inserted-line map keeps crash lines exact (#24) either way.
        self._native_ins = None
        self._native_fail = None
        code = None
        t3 = _ticks_ms()
        # #66 THE repeat-run cliff fix (glass-fingerprinted 2026-07-11): the esp32
        # port's exec arena (esp_native_code_commit, MALLOC_CAP_EXEC internal
        # IRAM) is a GROW-ONLY list -- every @micropython.native compile leaks its
        # machine-code blobs until soft reset. Recompiling the SAME source on
        # every PLAY (the kid's edit->PLAY->edit loop!) exhausted it in ~5 runs of
        # a heavy cart; the emitter then died on a ~300-byte MemoryError and the
        # silent bytecode fallback HALVED the cart's logic speed ("the floor",
        # logic 6.5 -> 13-17ms; nfail= in RUNSTART names it). So compile ONCE per
        # (cart, source version): the cache key is the source's (len, hash) and
        # re-PLAYs of unchanged source re-exec the SAME code object -- its blobs
        # are immortal in the arena anyway, so reuse costs nothing and saves the
        # ~110-210ms recompile per PLAY too. An EDIT legitimately recompiles (new
        # hash -> one new blob-set; the old one still leaks -- the arena has no
        # free -- so a marathon edit session can still exhaust it, but at the
        # per-edit rate instead of the per-PLAY rate; the fallback stays graceful).
        _ckey = project.cart.get("path") or id(project.cart)
        _csig = (len(src), hash(src))
        _hit = _CODE_CACHE.get(_ckey)
        if _hit is not None and _hit[0] == _csig:
            code = _hit[1]
            self._native_ins = _hit[2]
            self._native_fail = _hit[3]
            if NATIVE_CARTS and self._native_ins is not None:
                ns["micropython"] = _micropython
        elif NATIVE_CARTS:
            # Compile MISS (new cart or edited source): reclaim the exec arena
            # FIRST (#66 -- it is grow-only otherwise; the port patch exposes
            # the free through moy_gfx). Safe here: the cache is being purged
            # (its code objects' native blobs die with the arena) and no cart
            # world is live (the previous run's release_world dropped it; the
            # wallpaper compiles plain, never native). The arena then holds
            # exactly ONE cart's current-version blobs at any time -- unlimited
            # edit->PLAY cycles. On builds without the patch the call reports
            # False and behavior is the pre-patch cache-only mitigation.
            # While the running set holds another world (runtime/shell/procs.py)
            # its code lives in the arena too, so nothing is reclaimed: the
            # arena grows until the set is empty again, and an emitter that
            # runs out falls back to bytecode as above.
            procs = getattr(self.ws, "procs", None)
            if procs is None or not procs.list:
                _CODE_CACHE.clear()
                try:
                    import moy_gfx
                    moy_gfx.native_code_free_all()
                except Exception:  # noqa: BLE001 -- host / bench builds: no reclaim
                    pass
            nsrc, ins = _nativize(src)
            ns["micropython"] = _micropython   # the decorator's global, no import line
            try:
                code = compile(nsrc, "<cart>", "exec")
                self._native_ins = ins
            except Exception as exc:  # noqa: BLE001 -- emitter limitation -> bytecode path
                self._native_fail = _err_text(exc)
                code = None
        t_compile_native = _ticks_diff(_ticks_ms(), t3)
        return code, t_compile_native, _ckey, _csig

    def _loop_dt(self, dt):
        """The dt an UNPACED tick gets: the loop's own for a tool/app, and for
        a `"fps": "free"` game the same CLAMPED at FREE_DT_MAX -- a stall (a
        radio scan, a cart load) slows its time instead of teleporting every
        dt-scaled body across the screen."""
        if self._free and dt > FREE_DT_MAX:
            return FREE_DT_MAX
        return dt


    def frame_plan(self, dt):
        """What this loop frame is for the running cart (#217): schedules its
        logic ticks (tick() runs them) and returns whether the frame DRAWS. A
        tool/app or a crashed cart ticks and draws with the loop it serves. A
        lockstep match (#65) owns its own clock: its due tick decides, and the
        draw divisor stays 1 there -- composing the two is its own step."""
        # Each store is skipped when it changes nothing: a store on the Player
        # looks through its classes for a property setter.
        np = self.netplay
        if np is not None:
            # A match, whatever the cart's pace: a frame may draw when a tick
            # is due or a stalled one retries, and tick() draws it only if it
            # simulated -- a cart's _draw runs exactly once a tick on both
            # consoles, so a _draw that keeps state cannot part them.
            if self.n_ticks != 1:
                self.n_ticks = 1
            plan = bool(np.waiting or np.pending(_ticks_ms()))
            if plan is not self._np_plan:
                self._np_plan = plan
            return plan
        if not self.tick_ms:
            if self.n_ticks != 1:
                self.n_ticks = 1
            return True
        s = self.sched
        s.plan(dt)
        n = s.n
        if n != self.n_ticks:
            self.n_ticks = n
        if n == 0 and self.keep_edges is not None:
            self.keep_edges()
        return s.draw

    def _run_ticks(self, n, dt, draw):
        """`n` logic ticks of `dt`. Each takes the latched press edges, and the
        last carries this frame's draw for a runtime whose tick fuses _update
        and _draw (the Lua tier)."""
        play = self.play
        if play is not None:
            if self.keyp_latch:
                self.keyp_latch = 0
            # A frame with no tick still hands the run the pointer, whose
            # press edge then waits for the next tick (native/moy_play).
            play(n, dt, draw if n else False)
            return
        upd = self.update
        lua = self.lua
        fs = self._lua_split
        te = self.tick_edges
        inp = self.ws.input
        # The pointer's press edge on a frame with no tick waits for the next
        # one, as moy_play's latch keeps it where the kernel drives the run.
        gp = getattr(inp, "game_pointer", None)
        if not n:
            if gp is not None and gp[2]:
                self.press_owed = True
            return
        if self.press_owed:
            self.press_owed = False
            if gp is not None and not gp[2]:
                inp.game_pointer = (gp[0], gp[1], True, len(gp) > 3 and gp[3])
        sched = self.sched
        i = 0
        while i < n:
            i += 1
            if te is not None:
                te()
            k = self.keyp_latch
            inp.cart_keyp = k
            if k:
                self.keyp_latch = 0
            if lua is not None:
                lua.draw_next = draw and i == n
            if upd is not None:
                t0 = _ticks_us()
                upd(dt)
                us = _ticks_diff(_ticks_us(), t0)
                self.us_upd += us
                cost = us / 1000000.0
                # THE LOGIC HALF, not the frame. A runtime that runs the whole
                # cart frame inside update() (moycore: _update and _draw back
                # to back in C) makes this clock read logic PLUS draw, and the
                # scheduler reads `tick_cost` as the logic alone in both of its
                # rules: the catch-up test for a CHEAP tick, and the pin that
                # says no divisor can help a tick already costing a period.
                # Measured on a T-Deck 2026-09-10, `dank tomb` at 60Hz: the
                # fused frame is 25.7ms against a 16.7ms period, so the pin
                # fired and held the divisor at 1 -- while the scheduler's own
                # fits(3) was True -- and the cart ran its logic at 27Hz, half
                # its declared speed, which is the exact slowdown the tick
                # model exists to refuse. The DRAWBRK split already asks this
                # runtime the same question for the same reason.
                if fs is not None:
                    _sp = fs()
                    if _sp is not None:
                        cost = _sp[0] / 1000.0        # ms -> s, the update half
                sched.note_tick(cost)

    def _start_runtime(self, runtime, ns, src, t0, h0, t_pre):
        """Start a cart on a runtime other than the console's Python: `"lua"`
        (#67) or `"wasm"` (native/moy_wasm/README.md), through the factory
        `ws.runtimes` maps it to -- moycore_glue's runs on every tier,
        runtime/wasm_host.WasmHostRun for a compiled cart on the host, and
        runtime/run/elf_run.ElfGame for a native cart on every tier. The cart gets the SAME make_api namespace a Python cart got
        (a Lua factory registers those callables as the cart's globals; a wasm
        one reads its config and pmem from it), so permission gating, pmem,
        audio and quit() semantics are identical by construction. No
        auto-native, no code cache -- those are Python-compiler concerns. A
        runtime this build lacks is an ABSENT KEY, and it and a load/_init
        error land on the normal cart error panel."""
        ws = self.ws
        t_reclaim, t_audio, t_api = t_pre
        # Same measurement-mode gate as start(): no heap walks in kid mode.
        _hs = _heap_stats if self._diag_enabled() else (lambda: (-1, -1))
        self._native_ins = None        # RUNSTART diag: no auto-native on this path
        self._native_fail = None
        make = (getattr(ws, "runtimes", None) or {}).get(runtime)
        t5 = _ticks_ms()
        t_exec = -1
        t_init = -1
        lua = None
        lay = None
        try:
            if runtime not in RUNTIME_NAMES:
                raise ValueError("unknown cart runtime '%s'" % runtime)
            if make is None:
                # The graceful floor: a cart on a build without its runtime
                # gets the notice that says so, never a hang; its cause
                # exact under Details. A native cart in the browser is the
                # board-only one.
                raise _Refused(_ui_draw.s("say.board_only_text"
                                          if runtime == "elf" and _sys.platform == "webassembly"
                                          else "say.runtime_text"),
                               _ui_draw.s("say.runtime"),
                               "needs the %s runtime (not in this build)"
                               % RUNTIME_NAMES[runtime])
            if runtime == "wasm" or runtime == "elf":
                # A compiled cart's imports against this console's table, and
                # a compiled or native cart's load footprint against what this
                # console can give it, before anything loads.
                cart = ws.cart or {}
                if runtime == "elf" and _cart_unbuilt(make, cart):
                    raise _Refused(_ui_draw.s("say.chip_text"), _ui_draw.s("say.chip"),
                                   "no main.%s.elf" % make.engine.CHIP
                                   if hasattr(make, "engine") else None)
                missing = _cart_missing(make, cart)
                if missing:
                    raise _Refused(newer_notice(missing),
                                   _ch.say_title(_ch.SAY_NEWER))
                fit = _cart_fit(make, cart)
                # A failed fit gets back what the WM holds as caches -- parked
                # window stamps oldest-first, then the home's retained frame
                # and a dialog's held page -- one at a time, until the check
                # passes or nothing is left
                # (docs/theme_and_shell_2026-11.md §8.1).
                while (fit is not None and not _fits(fit)
                       and ws.wm.release_cache()):
                    fit = _cart_fit(make, cart)
                if fit is not None and not _fits(fit):
                    raise _Refused(fit_notice(cart.get("title"), fit[0], fit[1]),
                                   _ch.say_title(_ch.SAY_FIT))
            # The app's run rides the namespace into the runtime's open: a
            # Lua app's state opens its role names over it, a compiled app's
            # imports serve it. A game has none, and the console's state is
            # the one its `ui` reads the theme from (moy_app_wasm.h).
            ns["_moy_app"] = self._app_run(ws.cart)
            # An app's `_layout(w, h, fs)` hears its surface before `_init`,
            # as a Python app's does: the runtime calls it inside its load
            # where the cart defines one, and answers `layout` for the calls
            # after (a native or WASI app's frame words carry it instead).
            lay = self._layout_args() if self._app_id is not None else None
            ns["_moy_layout"] = lay
            if runtime == "wasm" and APP_EXT in ((ws.cart or {}).get("extensions") or ()):
                # A compiled app hears its window events as messages.
                _moybyte._compiled_events(ns, self.pid)
            elif runtime == "lua" and self.pid:
                # A Lua run's `moybyte` wakes are the kernel's under its
                # process, which its run serves as each of its frames starts
                # (native/moycore/moycore_moybyte.h).
                _moybyte.own_queue(self.pid)
                ns["_moy_pid"] = self.pid
            # The cart's code loads and inits inside the app-cart boundary, as
            # a Python cart's exec and _init do (docs/theme_and_shell_2026-11.md §3.7).
            _was = _cart_text(True)
            try:
                lua = make(ns, src)
            finally:
                _cart_text(_was)
            if runtime == "wasm" and getattr(lua, "interp", False):
                # Not an error and not a panel: the cart plays, on the
                # interpreter rather than the module this console wanted.
                # The sub-line names the cause; an unrecognised or absent one
                # (a fixture, or a runtime that predates interp_cause) reads
                # as "missing", the more common case.
                notice = getattr(getattr(ws, "notices", None), "notice", None)
                if notice is not None:
                    sub = INTERP_NOTICE_SUB.get(getattr(lua, "interp_cause", None),
                                                INTERP_NOTICE_SUB_DEFAULT)
                    notice(INTERP_NOTICE_TITLE, _ui_draw.s(sub), "warn")
            t_exec = _ticks_diff(_ticks_ms(), t5)
            t6 = _ticks_ms()
            if lua.init is not None:
                _was = _cart_text(True)
                try:
                    lua.init()
                finally:
                    _cart_text(_was)
            t_init = _ticks_diff(_ticks_ms(), t6)
        except Exception as exc:  # noqa: BLE001 -- load/_init error -> the panel
            if lua is not None:
                try:
                    lua.close()
                except Exception:  # noqa: BLE001
                    pass
            self.cart_error = _lua_err_text(exc)
            # a load/syntax or _init error carries its `cart:N:` position, so
            # EDIT drops on the line exactly like a Python SyntaxError (#24) --
            # and on a cart of several scripts, in the FILE that raised. A
            # compiled cart has no line to drop on; one built for a newer
            # console gets the newer-console notice, and one this console
            # cannot hold gets the fit notice, whether the check refused it or
            # its load ran out of memory. Every OTHER compiled-cart refusal
            # (no/stale module, unsigned with the switch off) was already
            # retried on the interpreter inside WasmRun -- what reaches here
            # is a real error (a trap, a bad signature, a malformed cart).
            # A native cart's is the same.
            if isinstance(exc, _Refused):
                self.crash_file, self.crash_line = None, None
                self._notice = self.cart_error = exc.args[0]
                self.notice_title = exc.args[1]
                self._detail = exc.args[2] if len(exc.args) > 2 else None
            elif runtime == "wasm" or runtime == "elf":
                self.crash_file, self.crash_line = None, None
                title = (ws.cart or {}).get("title")
                if _out_of_memory(exc):
                    print("Moybyte cart load:", self.cart_error)
                    self._detail = self.cart_error
                    fit = _cart_fit(make, ws.cart or {})
                    self._notice = self.cart_error = fit_notice(
                        title, fit[0] if fit else None, fit[1] if fit else None)
                    self.notice_title = _ch.say_title(_ch.SAY_FIT)
            else:
                self.crash_file, self.crash_line = _lua_cart_where(
                    self.cart_error, self.ws.cart)
            self.ns = ns
            h1 = _hs()
            self._start_diag = (t_reclaim, t_audio, t_api, 0, t_exec, t_init,
                                _ticks_diff(_ticks_ms(), t0),
                                h0[0], h1[0], h0[1], h1[1])
            if self.notice:
                _os_log.failed(self.pid, self._detail or self.cart_error)
            else:
                self._crashed(None, self.cart_error)
            self._print_run_diag("RUNERR", "err=%s" % self.cart_error)
            print("Moybyte cart %s:" % ("notice" if self.notice else "error"),
                  self.cart_error)
            return False
        self.lua = lua
        # Its pmem image, read every Pmem.SAVE_MS by the frame's save -- unless
        # the run saves it itself (moycore_glue's `pmem_saves`) or has none.
        self._pmem_read = (None if getattr(lua, "pmem_saves", False)
                           else getattr(lua, "flush_pmem", None))
        relayout = getattr(lua, "layout", None)
        if lay is not None and relayout is not None:
            self._app_layout = relayout
            self._app_wh = lay
        self._lua_split = getattr(lua, "frame_split", None)
        self._flag_put = getattr(lua, "flag_xor", None)
        self.cart_error = None
        self.crash_line = None
        self.crash_file = None
        self.ns = ns
        self.update = lua.update
        self.draw = lua.draw
        self._restore_bg = ns.get("_moy_restore_bg")
        h1 = _hs()
        self._start_diag = (t_reclaim, t_audio, t_api, 0, t_exec, t_init,
                            _ticks_diff(_ticks_ms(), t0),
                            h0[0], h1[0], h0[1], h1[1])
        self._print_run_diag("RUNSTART")
        return True

    def tick(self, dt, render=True):
        """The running-cart content (game domain): the logic ticks frame_plan
        scheduled for this loop frame, the cart's _draw when `render`, the mixer
        feed, then the crash chrome. Fills the
        per-frame perf split (ws._pf_*) the router's DRAWBRK accounting
        reads. Drawn on the fixed 320x240 GAME canvas, composited by the router.

        render=False is a logic-only frame (#217): the cart's ticks, audio and
        exit/textmode handling run as normal, but the backdrop restore, the cart's
        _draw and every chrome draw are skipped -- the game canvas keeps the last
        rendered frame's pixels and nothing composites or flushes this frame."""
        ws = self.ws
        if self._stopping:
            return                     # the VM stops at this frame's end
        if self.front and self._resume_from_front(ws):
            return
        if self.paused_at:
            return                     # the pause menu holds it: the frame stays
        if self._front_want:
            self._front_again()
            if self.front:
                return                 # the kernel draws it from the next frame
        _perf = (ws.perf_hud or ws.perf_capture) and not self.detached
        if self.cart_error is None:
            if not self.detached:
                self._tick_keys(ws)
            fed = self.focus_editor
            gp = getattr(ws.input, "game_pointer", None) if fed is not None else None
            if gp is not None:
                fed.mouse_frame(ws.input, gp[0], gp[1], dt)   # its wheel and autoscroll
            if render and self.ui is not None and self._ui_still():
                self._ui_unchanged()    # its canvas holds the page: no pass
                if _perf:
                    ws.pf_upd = ws.pf_cart = ws.pf_audio = ws.pf_bg = 0
                return
            # The cart's code prints and measures in petme128, whatever the
            # theme names (docs/theme_and_shell_2026-11.md §3.7).
            _was = _cart_text(self._boundary)
            try:
                # A RESPONSIVE app cart follows its surface (#181): a window
                # resize, a font-scale change or a mode switch re-runs `_layout`
                # BEFORE the frame that would draw against the new geometry.
                # Costs a game exactly one `is not None`, and an app three
                # compares -- there is no clock and no probe here.
                if self._app_layout is not None and self._layout_moved():
                    self._app_layout(*self._app_wh)
                # A match's tick is decided before anything draws: a frame
                # that did not simulate draws nothing (frame_plan).
                np = self.netplay
                stalled = False
                if np is not None:
                    dt, stalled, np = self._lockstep_step(ws, dt)
                    if stalled:
                        render = False
                # Declared background (#63): restore the cart's named backdrop BEFORE
                # its frame runs, so a naive cart draws only its actors. No-op (one
                # early-out) when the cart never called background(). Skipped on a
                # logic-only frame (nothing draws over it this frame).
                #
                # TIMED, and charged to RENDER, not chrome (#172, on-glass
                # 2026-08-02): this restore IS the cart's drawing (it stands in
                # for its first cls()), and letting it fall out of the bracket
                # once put ~4.7ms of Brick Siege's frame under CHROME and sent
                # #172 hunting the shell for a regression that was never there.
                lua = getattr(self, "lua", None)
                if (self._tool and lua is not None and not (self.deaf or self.detached)
                        and not getattr(lua, "own_ui", False)):
                    s = _RUN_STEPS              # into the run face's frame begun next
                    _steps(ws.input, s, 0)
                    _ui_draw._C.api_run_steps(s[0], s[1])
                if self.ui is not None and lua is None:
                    self._ui_begin(ws, render)
                elif (lua is not None and getattr(ws.input, "last_key", 0) == _ui_draw.KEY_PASTE
                        and not getattr(lua, "own_ui", False)):
                    t = _ui_draw.paste_text()   # the console's Paste, into the run's next frame
                    if t:
                        _ui_draw.paste(_ui_draw.RUN_UI, t)
                _tb = _ticks_us() if _perf else 0
                rb = self._restore_bg
                if render and rb is not None:
                    rb()
                bg = _ticks_diff(_ticks_us(), _tb) if _perf else 0
                # Multiplayer (#65): deliver any inbound net.* messages to the cart's
                # on_net handler BEFORE its _update runs (incoming shared state applied
                # first -- the lockstep-friendly order). Every frame, drawn or
                # not. No-op when the cart has no net permission.
                if self._net is not None:
                    self._net.pump()
                # The Editor's flag edits since the last frame, into the run's
                # own table (E-02: the cart's fset stays the run's).
                if self._flag_put is not None:
                    self.run.project.flag_edits(self._flag_put)
                # MICROSECONDS, not ms. These three brackets and the backdrop one
                # above feed DRAWBRK's split, and `chrome` is what is left after
                # subtracting them from the frame -- on a ms clock every one of
                # them truncated toward zero and the residual collected the whole
                # error. ws._pf_* are microsecond ints; _frame_perf_end divides
                # once, at the EMA, so every public number stays in ms.
                _ts = _ticks_us() if _perf else 0
                if np is not None:
                    self._run_ticks(0 if stalled else 1, dt, render)
                elif self.tick_ms:
                    self._run_ticks(self.n_ticks, self._tick_dt, render)
                else:
                    self._run_ticks(1, self._loop_dt(dt), render)
                _tm = _ticks_us()
                if render and self.draw:
                    self.draw()
                    if self.ui is not None and getattr(self, "lua", None) is None:
                        self._ui_close()
                _cart_text(_was)
                _td = _ticks_us()
                self.us_draw += _ticks_diff(_td, _tm)
                if ws.audio_out is not None and not self.detached:
                    ws.audio_out.tick(dt)  # pull the mix where no feeder task does (#16)
                if _perf:
                    upd = _ticks_diff(_tm, _ts)           # cart _update -> game LOGIC
                    cart = _ticks_diff(_td, _tm) + bg     # cart _draw + backdrop -> RENDERING
                    # A runtime that runs the WHOLE cart frame inside update()
                    # (moycore: _update and _draw back to back in C) makes those
                    # two clocks read `logic = everything, render = 0`. It can
                    # still say where its own time went, in microseconds, so ask
                    # -- otherwise every logic/render pair this project has
                    # recorded since #67 becomes incomparable to the next one.
                    _fs = self._lua_split
                    if _fs is not None:
                        _sp = _fs()
                        if _sp is not None:
                            # frame_split keeps its ms contract (the glue's);
                            # this side is us, so convert rather than widening it.
                            # The draw half is the last tick's; the rest of the
                            # bracket is every logic tick this frame ran.
                            _dr = int(_sp[1] * 1000.0)
                            cart = _dr + bg
                            upd = upd - _dr if upd > _dr else 0
                    aud = _ticks_diff(_ticks_us(), _td)   # audio_out.tick (the pull)
                    ws.pf_upd = upd
                    ws.pf_cart = cart
                    ws.pf_audio = aud
                    ws.pf_bg = bg        # the backdrop's share of cart, for DRAWBRK
                    self._maybe_diag_slow_logic(upd, cart, aud)
                # CRASH GUARD heal (#160): a rendered frame that got here ran the
                # cart's body, its _init, its _update and its _draw without
                # raising. Outside the perf brackets above on purpose -- the
                # HEALING frame pays one small settings write, and charging that
                # to the cart's render slice would put a flash hitch in DRAWBRK
                # under the cart's name.
                if render and self._app_id is not None:
                    ws.app_guard.frame()
            except Exception as exc:  # noqa: BLE001
                _cart_text(_was)
                if self.capture_frame_error(ws, exc):
                    return
        self._tick_tail(ws, render)

    def _tick_keys(self, ws):
        """The keyboard edge for the cart's key()/keyp(), or for the focused
        editor handle that owns the keyboard instead."""
        # key() is last_key, the byte held this loop frame (0 when nothing
        # is down); keyp() is the table's press (`typed`), which LATCHES
        # until a logic tick takes it (#217).
        i = ws.input
        k = i.last_key
        e = i.typed
        if self.netplay is not None or self.deaf:
            k = e = 0                   # no key() in a match (only buttons cross the air), nor deaf
        fed = self.focus_editor
        if fed is None:
            i.cart_key = k
            if e:
                self.keyp_latch = e
        else:
            # A FOCUSED editor handle owns the keyboard: the byte goes to
            # it and the cart's own key()/keyp() read nothing, so a skin
            # never has to filter the text its editor is eating.
            i.cart_key = 0
            if e and not fed.key(e):
                self.keyp_latch = e    # a key it did not take (Esc) is the cart's
            # a held key types again at the input table's repeat: the
            # handle's, never the cart's key()/keyp()
            n = 0 if self.deaf else getattr(ws.input, "key_repeats", 0)
            if n:
                r = ws.input.repeat_key
                if r in (8, 9, 10, 13, 127) or 32 <= r < 127:
                    for _ in range(n):
                        fed.key(r)

    def _lockstep_step(self, ws, dt):
        """The #65 lockstep gate for this frame: (dt, stalled, session). With
        no session dt is the loop's own, nothing stalls and session is None."""
        # LOCKSTEP (#65 Phase 2): the session owns the clock. `dt` becomes
        # the fixed tick -- a variable dt diverges the two sims on frame
        # one -- and a frame whose peer input has not arrived does NOT
        # simulate, and draws nothing: the screen holds the last agreed
        # frame, which is what "waiting for player" looks like on hardware
        # that cannot extrapolate safely.
        stalled = False
        np = self.netplay
        if np is not None and not self._np_plan:
            # The plan saw no tick due and none stalled, so this frame draws
            # nothing, and it starts no tick even if the clock has reached
            # one since: a tick it simulated would never be drawn. It keeps
            # sending between ticks.
            self._np_plan = True
            if np.gate(_ticks_ms(), False):
                np.resend()
            return np.dt_at(np.frame), True, np
        if np is not None and self.play is not None:
            # A run in the kernel's Player: its lockstep is C -- the drain, the
            # due tick or the stall's retry, the advance, the resend between
            # ticks (moy_play_lockstep).
            run = _moy_play.current()
            if not run:
                # The run ended in the kernel's front (`end`):
                # nothing simulates, and this frame's Player takes the route.
                return np.dt_at(np.frame), True, np
            n = _moy_play.lockstep(run)
            if n is not None:
                return np.dt_at(np.frame - 1), n == 0, np
        if np is not None:
            # Drain the radio BEFORE deciding whether this tick can
            # advance: the boards drain in the frame TAIL, so without
            # this the peer's input is up to a whole loop frame stale
            # by the time advance() looks for it. Measured on glass
            # (P4<->T-Deck, 2026-08-24): 8.0% -> 6.4% stalled ticks at
            # DELAY=2 from this call alone. Input-priority and
            # mid-frame-safe by contract -- see EspNowLink.drain_input.
            _di = self._drain_input
            if _di is not None:
                try:
                    _di()
                except Exception:  # noqa: BLE001 -- radio must not kill a frame
                    pass
            dt = np.dt_at(np.frame)
            # The session owns the CLOCK as well as the order: it ticks
            # at its fixed rate, not at whatever the board's frame loop
            # manages, or a fixed dt would silently run the game fast.
            _nowp = _ticks_ms()
            _due = np.due(_nowp)
            if not np.gate(_nowp, _due):
                stalled = True          # within LOCK_MS of the last send
            elif _due or np.waiting:
                # A STALLED tick retries every loop frame instead of
                # waiting out a whole tick: the missing input usually
                # lands a few ms after it was first needed, and the
                # wait-for-the-next-due schedule made a 5ms miss cost
                # 34ms (wait_med, on glass 2026-08-24). due() still
                # owns the cadence of healthy ticks.
                stalled = not np.advance(
                    _netplay_mask(ws.input, _NET_BUTTONS), _nowp)
            else:
                # Between ticks: do not simulate, but KEEP SENDING. The
                # frame loop is faster than the lockstep clock, so these
                # frames are free redundancy against a radio whose ack
                # lies -- and they serve a stalled peer sooner.
                stalled = True
                np.resend()
        return dt, stalled, np

    def capture_frame_error(self, ws, exc):
        """A cart that raised mid-frame: capture it, stop the cart, persist
        pmem; the stop card then opens over its last frame (`_tick_tail`).
        Returns True when the screen left the run (a parked console goes
        home)."""
        # A cart that raises mid-frame must NOT escape the loop (the
        # device would hang silently). Capture it, stop running the
        # broken cart, and fall through to the stop card, whose See why
        # opens the code on the line.
        # mark the line on EDIT (#24): a Lua cart's line comes from the
        # error text's `cart:N:` position (#67 Phase 5); a Python cart's
        # from the traceback, mapped back through the nativize insert.
        if self.lua is not None and _compiled(ws.cart):
            # A trap: the run already cleared its canvas, and there is no
            # source line to mark (native/moy_wasm/README.md).
            self.cart_error = _lua_err_text(exc)
            self.crash_file, self.crash_line = None, None
        elif self.lua is not None:
            self.cart_error = _lua_err_text(exc)
            self.crash_file, self.crash_line = _lua_cart_where(
                self.cart_error, ws.cart)
        else:
            self.cart_error = _err_text(exc)
            self.crash_line = self._map_crash_line(_exc_cart_line(exc))
            self.crash_file = None
        self._crashed(exc if self.lua is None else None, self.cart_error)
        self.update = None
        self.draw = None
        self.pace.disarm()  # the crash panel is a console screen
        # Print the _err_text-guarded string, never the raw `exc`: a
        # cart exception whose __str__ itself raises would otherwise
        # escape here -> the silent device hang the panel exists to prevent.
        print("Moybyte frame error:", self.cart_error)
        # The crash must not eat saved progress -- the cart stops ticking
        # now, so this is its last chance to persist. Guarded: the panel must
        # paint even if SD is gone.
        self._pmem_flush()
        ws.reset_canvas_state()
        if ws.web.parked:
            return ws.crash_to_code()
        return False

    def _pmem_flush(self):
        """Save the run's pmem now and wait out any save in flight: exit,
        pause and a crash. A live Lua or compiled run's image is read first.
        Never raises: the exit and the crash panel go on without the card."""
        pm = getattr(self.ws.run.project, "pmem", None)
        if pm is None:
            return
        try:
            sync = getattr(self.lua, "flush_pmem", None)
            if sync is not None:
                sync()
            pm.flush()
        except Exception:  # noqa: BLE001
            pass

    def _pmem_tick(self, ws):
        """The frame boundary's pmem save (widgets.Pmem.tick): handed to the
        store's saver at most every Pmem.SAVE_MS while it moved. A Lua or
        compiled run that saves itself (moycore_glue's `pmem_saves`) is left
        to; one that does not has its image read every Pmem.SAVE_MS."""
        pm = getattr(ws.run.project, "pmem", None)
        if pm is None:
            return
        now = _ticks_ms()
        if self.lua is not None:
            read = self._pmem_read
            if read is None or _ticks_diff(now, self._pmem_sync) < pm.SAVE_MS:
                return
            self._pmem_sync = now
            read()
        pm.tick(now)        # a method call, never a bound one: a frame allocates nothing

    def _tick_tail(self, ws, render):
        """After the cart's frame: the pmem save, a quit() exit, the text-mode
        sync, the canvas-state reset and the chrome a drawn frame paints over
        the cart."""
        if self.cart_error is None:
            self._pmem_tick(ws)
        # A cart ENDS ITSELF by calling quit() (make_api), which sets input.cart_quit.
        # Honor it now that this frame's _update has run: pop to the run caller and stop
        # (screen leaves "desktop", so this cart won't tick again -- skip the
        # chrome below). Any cart may call quit(); the console's own key (`start`)
        # opens the pause menu whatever the cart does. Reset the
        # cart-set camera/clip/pal first so whatever paints underneath is clean.
        if self.cart_error is None and getattr(ws.input, "cart_quit", False):
            ws.input.cart_quit = False
            ws.reset_canvas_state()
            if self.detached:
                self.quit_asked = True      # the running set closes it
                return
            ws.spine.exit_to_caller()
            return
        if self.detached:
            ws.reset_canvas_state()
            return
        # Clear any cart-set camera/clip/pal/palt (#11) before the console paints
        # its own UI overlays, so they're never offset/clipped/recoloured. A
        # compiled cart draws under libmoy's own state, never the canvas's.
        if not self._wasm:
            ws.reset_canvas_state()
        # A logic-only frame (#217): nothing below draws pixels this frame --
        # the crash panel/tool bar all repaint on the next drawn frame
        # (a crash disarms the pacing itself, so the panel is never starved).
        if not render:
            return
        if self.cart_error is not None or self._tool:
            ws.settle_cart_frame()          # the chrome below draws over the frame
        # The bar auto-hides while a cart PLAYS (Stage 5): the game owns the full
        # 320x240 with NO chrome (the #71 pause frame is gone). A run that
        # stopped opens the stop card over its last frame (the pause card's
        # sibling, drawn by its layer on the system canvas). It opens inside
        # this draw, past the stack the frame is painting, so the frame owes
        # the next one, which paints it: a stopped run animates nothing else.
        if self.cart_error is not None:
            if not ws.pause.open:
                ws.pause.stop()
                ws.request_frame()
        elif self._tool and not self.run.app_window_canvas:   # #75: cached at start()
            # Part 4: a TOOL/APP runs WITH a minimal bar (back + title + status) so
            # it's exitable -- a GAME stays fullscreen-bar-hidden. The bar-visibility-by-
            # type rule keys on the running cart's manifest type (ws.running_cart_shows_bar).
            ws.draw_tool_bar()
        # (The FPS chip + perf HUD is the game-domain _PerfLayer, drawn right after
        # this content -- still on the GAME canvas, before the composite.)

    def handle_input(self, i):
        # A running cart owns the full 320x240 and every button and key,
        # BACKSPACE included: its own btn()/key() calls in tick() read them in
        # parallel, and the Player takes none. The console's one key is `start`
        # (Super, a T-Deck's Mic), which the router reads before any layer and
        # which opens the pause menu over a game (`Workstation.system_key`). A
        # cart ends itself with quit(); a tool/app runs WITH a minimal bar and
        # exits through its context-X.
        return True

    def handle_pointer(self, px, py, click):
        # A running cart + the editors live in the 320x240 GAME viewport, so translate
        # the panel pointer into game coords (#39; identity in the degradation case).
        ws = self.ws
        sx, sy = px, py
        gx, gy = ws.game_xy(px, py)
        px, py = gx, gy
        # While a GAME plays the bar is hidden, so the game owns the full 320x240 and
        # every tap belongs to the cart (published as the game pointer by the router before
        # this runs; the pause menu and the stop card, a layer above, take them while they
        # show). One chrome case DOES hit-test a tap: a running TOOL/APP's minimal bar
        # (Part 4: its context-X / wifi / ≡), through a thin ws helper so this file never
        # reaches the bar surface directly.
        if click and self._tool and self.cart_error is None:
            if not self.run.app_window_canvas and ws.tool_bar_tap(px, py, sx, sy):   # ‹ exits
                # The bar consumed the tap -> clear the published game pointer's tap so the
                # tool doesn't ALSO act on it this frame (mirrors the overlay-suppress rule).
                gp = ws.input.game_pointer
                ws.input.game_pointer = (gp[0], gp[1], False, False)
            # else the tap falls through to the tool (game pointer already published)
        elif click:
            if ws.show_fps and _in(px, py, ws.perf_ui._fps_tap_rect()):
                # Tapping the FPS readout toggles the frame-time breakdown HUD
                # (#43/#44 perf). Deliberate, no keyboard, doesn't fight game
                # input -- the touch lands on a small bottom-right corner box.
                ws.perf_hud = not ws.perf_hud
        return True

    def window_event(self, name, *args):
        """Tell a Python cart its window was minimised or restored, or that
        a notification it posted was acted on: its `_minimise()`,
        `_restore()` or `moy_action(id, key)`, where it defines one
        (docs/os_2026-11/processes.md §1 and §7). What it raises is its
        crash, as a frame's is. False when the run crashed there."""
        ns = self.ns
        fn = ns.get(name) if ns and self.cart_error is None else None
        if fn is None:
            return True
        ws = self.ws
        _was = _cart_text(self._boundary)
        t0 = _ticks_us()
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 -- the cart's, as a frame's is
            _cart_text(_was)
            self.capture_frame_error(ws, exc)
            return False
        finally:
            self.us_upd += _ticks_diff(_ticks_us(), t0)
        _cart_text(_was)
        return True

    # -- crash chrome (the Player's own UX) -----------------------------------

    @property
    def notice(self):
        """A notice's text while it is the panel up, else None: a cart
        refused before it loaded, for a format level above this console's, or
        a compiled one for want of room, of a signature or of an import this
        console has."""
        n = self._notice
        return n if n is not None and n == self.cart_error else None

    @property
    def notice_detail(self):
        """The notice up's cause exact, for the stop card's Details (a
        parent's or the cart's author's), else None."""
        return self._detail if self.notice is not None else None

    @property
    def newer(self):
        """True while the notice up is the newer-console one."""
        return self.notice is not None and self.notice_title == _ch.say_title(_ch.SAY_NEWER)
