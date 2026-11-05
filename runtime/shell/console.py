# Map (grep -n a name to jump there):
#   draw_splash                      paint the boot splash (splash_pal: its palette)
#   Workstation                      the console: canvases, the process stack, the store, every frame
#   Workstation._init_canvases       the system and game canvases
#   Workstation._init_components     the collaborators every surface reaches through ws
#   -- the layer stack               the compositor and router's layer list
#   -- WEB CONSOLE                   forwards to the web collaborator
#   -- top-bar system menu           the bar's menu groups
#   -- open-cart workspace forwards  Project forwards
#   -- cart-run forwards             Player forwards
#   -- run / exit                    open, run_script, open_in_editor (run and exit: console_spine)
#   -- the app bar contract          the bar every app gets
#   -- frame + drawing               frame: one console frame
#   -- two-domain composite          the game viewport and its coordinates
#   -- per-run cart canvas           the run's own game canvas
#   -- redraw-on-change              the redraw gate
#   -- content-layer draw bodies     what frame() routes each layer to
#   wire_workstation_core            the board-agnostic service wiring
"""The shared Moybyte v0.4 console UI -- launcher + desktop + cards/code/paint
editors + the trackball/touch Pointer. Backend-agnostic: it draws through an
injected `canvas` (host Canvas or device DeviceCanvas -- identical TIC-80 API +
petme128 font) and persists through an injected cart store + make_api, so the
host sim and the T-Deck render the SAME pixels from this one file.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/
tree so the device freezes it (same pattern as the editor cores). Keep it dependency-
free apart from the shared editor cores below.
"""

# The cores the shell itself needs, each from its own module, so a start that
# never opens the Editor loads none of the others.
try:
    from editors_code import CodeEditor
    from editors_sheet import _SheetSprite
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_code import CodeEditor
    from runtime.editors_sheet import _SheetSprite
# (the #111 op-history core is history_router.py's import now, not this file's --
# #209 landing E took the code tab's History and its typing-burst codec with it)
# The glass (native/moy_glass): the frame gate folds a dirty frame into the
# surface table's epoch and reads the kernel's own epoch as a dirty leg
# (docs/surface_model_v1.md §15). The host reaches the same C by ctypes.
try:
    import moy_glass as _glass
except ImportError:  # pragma: no cover - host: the ctypes binding
    from runtime import glass_binding as _glass

# THE EDITOR SURFACES ARE IMPORTED AT THEIR FIRST USE (docs/history/kernel_cartpath_2026-10.md
# section 5.5): the block, map, scene and music editors' UIs and the sprite/icon
# PAINT surface (paint_layer.py) and the theme mode's two tabs (theme_editor.py)
# are modules a start that never opens the Editor does not load.
# Each is a Workstation property (`block_ui`, `map_ui`, `scene_ui`, `music_ui`,
# `paint_layer`, `theme_config_layer`, `theme_sprites_layer`) that imports its
# module and builds the one
# instance when something first reads it; `built(attr)` answers without
# building. Each module stays the single source of its geometry constants; the
# names tests reach as `console.X` resolve through the module's __getattr__
# below. block_editor_ui.py's docstring says why the UIs take NAMES/_err_text/
# _clamp_scroll as constructor args instead of importing them back from here.
_LAZY_MODULES = {
    "block_editor_ui": ("BlockEditorUI", "BlockLayout", "_BLK_MENU_ROWS", "_NEW_VAR_ITEM",
                        "_NEW_VAR_LABEL", "_NEW_LIST_ITEM", "_NEW_LIST_LABEL",
                        "_NUM_LITERAL_ITEM", "_NUM_LITERAL_LABEL"),
    "map_editor_ui": ("MapEditorUI", "_MV_ZOOMS"),
    "scene_editor_ui": ("SceneEditorUI",),
    "music_editor_ui": ("MusicEditorUI",),
    "paint_layer": ("PaintLayer", "_PG_X0", "_PG_Y0", "_PG_SPAN",
                    "_SW_X0", "_SW_Y0", "_SW", "_SW_COLS"),
}


def _lazy_module(name):
    try:
        return __import__(name)
    except ImportError:  # pragma: no cover - host fallback when not yet aliased
        return getattr(__import__("runtime." + name), name)


def __getattr__(name):
    for mod, names in _LAZY_MODULES.items():
        if name in names:
            return getattr(_lazy_module(mod), name)
    raise AttributeError(name)


def _build_block_ui(ws):
    ui = _lazy_module("block_editor_ui").BlockEditorUI(ws, NAMES, _err_text,
                                                       _clamp_scroll)
    ui.relayout(ws.sys_canvas.w, ws.sys_canvas.h, ws.look.effective_font_scale(),
                ws.look.effective_chrome_scale())
    return ui


def _build_map_ui(ws):
    return _lazy_module("map_editor_ui").MapEditorUI(ws, NAMES)


def _build_scene_ui(ws):
    return _lazy_module("scene_editor_ui").SceneEditorUI(ws, NAMES)


def _build_music_ui(ws):
    return _lazy_module("music_editor_ui").MusicEditorUI(ws, NAMES)


def _build_paint_layer(ws):
    return _lazy_module("paint_layer").PaintLayer(ws, NAMES)


def _build_theme_config_layer(ws):
    return _lazy_module("theme_editor").ConfigLayer(ws, NAMES)


def _build_theme_sprites_layer(ws):
    return _lazy_module("theme_editor").SpritesLayer(ws, NAMES)


def _first_use(attr, build):
    """A Workstation property over `_<attr>`: built by `build(ws)` when first
    read, assignable like the attribute it replaces."""
    slot = "_" + attr

    def get(self):
        obj = getattr(self, slot)
        if obj is None:
            obj = build(self)
            setattr(self, slot, obj)
        return obj

    def put(self, obj):
        setattr(self, slot, obj)

    return property(get, put)


# The perf HUD's rendering layer (#43/#44): the bottom-right FPS chip + optional
# frame-time breakdown + its tap target. The perf *query* API
# (perf_sample/perf_breakdown/...) is the Workstation's, in console_perf.py (the
# device diag's measurement contract). Same bare-or-package fallback as above.
try:
    import iconsets as _iconsets
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import iconsets as _iconsets
try:
    from perf_hud import PerfHud
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.perf_hud import PerfHud

# The firmware-update (OTA) SCREEN's UI layer (#53): the
# confirm/download/install/done lifecycle + its pump + drawing. The update
# *queries* (_update_available/_online_update_available) stay below, and the
# channel choice (_ota_channel/_cycle_channel) is the SettingsToggles mixin's
# (console_settings.py). Same bare-or-package fallback as the editors above.
try:
    from update_ui import UpdateUI
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.update_ui import UpdateUI

# The WEB CONSOLE switch (#197, web_console.py): wasm mode's own object -- the
# pin, the paired url, the park/unpark of the glass, and the connection screen
# (web_console_ui.py) it holds. The first Workstation collaborator (#209).
try:
    from web_console import WebConsole
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.web_console import WebConsole

# The OS's own names for a Python app (moybyte.py): its events read the rings
# of the input this Workstation is built over.
try:
    import moybyte as _moybyte
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moybyte as _moybyte

# The cooperative scheduler (scheduler.py): the services this console starts
# at its first drawn frame.
try:
    import scheduler as _scheduler
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import scheduler as _scheduler

# The hardware leases (leases.py): the `pins` verbs' local service, and the
# grants a lease and a device API (devices.py) check; devfs.py's process rows.
try:
    import leases as _hwleases
    import devfs as _devfs
    import devices as _devices
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import leases as _hwleases
    from runtime import devfs as _devfs
    from runtime import devices as _devices

# CoverCache (cover_cache.py): ws.covers -- the shelf's cover + icon caches,
# their budgets, warmers and #186 frees, plus _CoverImage/_CoverJob.
try:
    from cover_cache import CoverCache
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cover_cache import CoverCache

# CartManager (cart_manager.py): ws.carts -- the scanned roster `carts.all`, the
# store-writing verbs (new/dup/delete), the sync re-scan, the #66 live-set diet
# and the #105 favorites/recents. What a shelf CARD looks like (the pseudo
# tiles, the search filter, the app-claim hiding) stays kernel, below.
try:
    from cart_manager import CartManager
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cart_manager import CartManager

# system.json's owner (#209 landing B, system_store.py): the settings rows
# `ws.system` aliases (written through one persisting setter), and the
# achievements list's store halves -- over a StoreHandle that reads the
# store/root/can_manage/_with_sd guard through `ws` per call. What APPLIES the
# settings (load_system's cascade) stays kernel policy, below.
try:
    from system_store import (StoreHandle, SystemStore)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.system_store import (StoreHandle, SystemStore)

# The #111 undo ROUTER (#209 landing E, history_router.py): the bar UNDO/REDO
# pair, the code tab's typing burst, the tab-scoped journal walk they fall
# through to, and the idle-typing autosave the frame loop ticks. Takes the same
# StoreHandle -- the walk re-derived that guard by hand until this landing.
try:
    from history_router import HistoryRouter
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.history_router import HistoryRouter

# The ≡ system menu's rows and actions (#52); its drawing is the kernel's chrome
# (console_notices.py has the class). The sysmenu Popup, _about flag, reboot_hook
# and toggle_sysmenu() stay on Workstation (tested ws. surface + device).
try:
    from notifications import NOTES as _NOTES
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.notifications import NOTES as _NOTES
try:
    from procs import Procs
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.procs import Procs
try:
    from console_notices import SystemMenuUI, PauseMenu, DeleteConfirm
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.console_notices import SystemMenuUI, PauseMenu, DeleteConfirm

# The Easter eggs (#21): the 3 hidden eggs and their trigger state. The
# badges are the achievements process's (`ach`, its link); starting it and the
# unlock's effects (load_achievements/_achievement_unlocked) are on the Notices
# mixin (console_notices.py), and the banner's
# DEADLINE the unlock pushes into at event time is created in _init_overlays
# below (#209 landing B). Same bare-or-package fallback.
try:
    from achievements_ui import AchievementsUI
    from achievements import Link as _AchLink
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.achievements_ui import AchievementsUI
    from runtime.achievements import Link as _AchLink

# The Layer protocol + the self-contained surface adapters (extracted from this
# file -- see layers.py): the router builds the stack from these. layers.py is a
# dependency-free leaf (every class references only its self.ws back-ref), so there
# is no circular import back into console. Same bare-or-package fallback as above.
try:
    from layers import (_LegacyLayer, _PlayerLayer, _BlocksLayer, _UpdateLayer,
                        _MapLayer, _MusicLayer, _SceneLayer, _PerfLayer,
                        _SysMenuLayer, _AboutLayer, _WebConsoleLayer,
                        _PauseLayer, _ConfirmLayer)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layers import (_LegacyLayer, _PlayerLayer, _BlocksLayer, _UpdateLayer,
                                _MapLayer, _MusicLayer, _SceneLayer, _PerfLayer,
                                _SysMenuLayer, _AboutLayer,
                                _WebConsoleLayer, _PauseLayer, _ConfirmLayer)

# The unified top bar surface (#46 -- see bar_layer.py). bar_layer.py is the
# SINGLE SOURCE of the bar geometry constants (_STATUS_H, _BAR_*); the ones
# tests reference as console._X are imported back here rather than
# duplicated. NAMES is injected into the one BarLayer a Workstation builds
# (circular-import dodge).
try:
    from bar_layer import BarLayer, _BAR_ICON, _STATUS_H
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import BarLayer, _BAR_ICON, _STATUS_H

# The Editor's Config tab ("Make it mine", #3/#15 -- see cards_layer.py). CART
# STATE stays on Workstation: ws.config / ws.apply; the tab's page mutates
# ws.config in place.
try:
    from cards_layer import CardsLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cards_layer import CardsLayer

# The Settings app surface (#28/#39/#53 -- see settings_layer.py). The
# aggregator: rows + scroll + drawing, owning NO config -- it reads ws state
# (system/ws.look/diag_live) and dispatches every mutation to the ws setters
# (the SettingsToggles mixin); the wallpaper cluster is single-sourced on
# ws.look (the launcher shares that backdrop). settings_layer.py is the single
# source of the _SET_* geometry constants; the two tests reach as console._X are
# imported back here.
try:
    from settings_layer import SettingsLayer
    from tasks_ui import TasksLayer
    from popover import component_for as _component_for
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.settings_layer import SettingsLayer
    from runtime.tasks_ui import TasksLayer
    from runtime.popover import component_for as _component_for

# The Python code editor surface (#24/#39 -- see code_layer.py). CodeLayer owns
# the full-screen text view + drawing + code-UI state; the shared ws.editor
# handle (like ws.paint) + save_code/run_code + the code-error state +
# code_layout stay on ws. code_layer.py is the single source of the code
# geometry constants + the MicroPython-safe syntax highlighter; the constants
# tests reach as console._X are imported back here.
try:
    from code_layer import CodeLayer, _CODE_X0, _CODE_Y0, _CODE_LH, _CODE_SYMBOLS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import (CodeLayer, _CODE_X0, _CODE_Y0, _CODE_LH,
                                    _CODE_SYMBOLS)

# Self-contained support widgets (see widgets.py): the Achievements milestone
# tracker (+ its ACHIEVEMENTS catalog), Pmem (cart persistent RAM) and the
# Popup, the one popover, and the Pointer cursor (moy_input.py). Leaves;
# imported back here so
# console.Pointer / console.Popup / console.ACHIEVEMENTS / ... resolve for
# Workstation + host_app + tests.
try:
    from widgets import (Achievements, Pmem, Popup,
                         ACHIEVEMENTS, _PLAY_GOAL, _POPUP_X, _POPUP_Y, _POPUP_W)
    from moy_input import Pointer, ball_coast as _ball_coast
    from audio_session import console_volume
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import (Achievements, Pmem,
                                 Popup, ACHIEVEMENTS, _PLAY_GOAL, _POPUP_X,
                                 _POPUP_Y, _POPUP_W)
    from runtime.moy_input import Pointer, ball_coast as _ball_coast
    from runtime.audio_session import console_volume

# The desktop wallpaper backdrop component (#28, extracted -- see wallpaper.py). The
# SHARED backdrop the launcher home + Settings both draw (ws.wallpaper.draw). It owns
# the rendering + the compiled-cart cache; the CHOICE is the theme's, and it and
# the picker/query API are ws.look's (appearance.py drives the component).
try:
    from wallpaper import Wallpaper
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wallpaper import Wallpaper

# The narrowed shell interface a SYSTEM APP is handed (ui_refactor_2026-08
# Phase 6). An app no longer holds `ws`; it holds an AppContext carrying only
# the roles its NEEDS tuple declares -- see runtime/shell/app_context.py.
try:
    from app_context import AppContext
    import shell_servers as _shell_servers
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_context import AppContext
    from runtime import shell_servers as _shell_servers
import moy_app as _moy_app

# Crash isolation for content the shell runs on the kid's behalf (#160,
# ui_refactor_2026-08 Phase 8): three failed runs and an app cart, or the
# wallpaper, stops being run -- see runtime/shell/crash_guard.py for why an
# in-process except cannot do this job.
try:
    from crash_guard import CrashGuard, WALLPAPER_KEY
    import app_perms as _app_perms
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.crash_guard import CrashGuard, WALLPAPER_KEY
    from runtime import app_perms as _app_perms

# Appearance (appearance.py): `ws.look`, the LOOK collaborator (#209 landing D)
# -- theme + variant, the system font scale, the theme's wallpaper, `custom`
# (the theme a change makes) and the top-bar icon sheet. It also installs the theme's shape (`ui.use_shape`): the
# choice is a persisted setting and the installed shape is process-wide state
# inside `ui`. Every surface just draws through `ui`.
try:
    from appearance import Appearance
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.appearance import Appearance

try:
    import layout_base as _layout_base
    import shell_layout as _shell_layout
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _layout_base
    from runtime import shell_layout as _shell_layout

# USER APPS (#181, ui_refactor_2026-08 Phase 7): the permission-keyed filter
# over AppContext that a `type: "app"` CART is handed. The shell needs only its
# identity helper here; the Player is what builds the namespace.
try:
    import system_api
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import system_api


# The home's layers (docs/theme_and_shell_2026-11.md §8.10, see
# launcher_layer.py): LauncherHomeLayer (the "launcher" content Layer -- the
# tab row, the cards and their input) and HomeWindowLayer, the same home in a
# desk window. The card models and the home's verbs are console_home's.
try:
    from launcher_layer import LauncherHomeLayer, HomeWindowLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.launcher_layer import LauncherHomeLayer, HomeWindowLayer
# The layers that draw a menu holding the keys (`Workstation._modal_layer`):
# the system menu's own, and a desk popover frame's that paints it alone.
_PAUSE_LAYERS = ("pause", "popover_over")
_CONFIRM_LAYERS = ("confirm",)
_SYSMENU_LAYERS = ("sysmenu", "popover_over")
# What a text field says where the input in use cannot type (`can_type`).
NO_KEYBOARD_TEXT = "Pair a keyboard to type"

try:
    import ui_draw as _ui_draw
    import ui as _uiv
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui_draw as _ui_draw
    from runtime import ui as _uiv

# The loading pill's icon by the kind of cart being opened (GAPS 51).
_LOADING_ICON = {"game": "game", "app": "app", "tool": "app", "wallpaper": "wallpaper",
                 "theme": "palette"}
_FLAT = {"flat": True}


# Project (project.py): the open cart's DATA + commit_* verbs; its six data
# fields are forwarding properties here so the surface files + tests keep the
# ws.<X> spelling.
try:
    from project import Project
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.project import Project

# Player (player.py): the run-loop black box; its cart-run fields
# (cart_error/crash_line/ns/...) are forwarding properties here for the same
# reason. A game is left through the pause menu (the console's key, `start`),
# a tool through the bar X; the #71 BACKSPACE pause is retired, do not
# reintroduce it.
try:
    from player import (Player, BAR_TYPES, SCRIPT_TYPE, _SIG_FPS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.player import (Player, BAR_TYPES, SCRIPT_TYPE, _SIG_FPS)

# The TEXT CONSOLE (text_console.py): the surface a `type: "script"` cart runs
# on -- scrollback + prompt, drawn by the shell as the run's `_draw` so a
# script cannot paint over the line it is reading from.
try:
    from text_console import TextConsole
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.text_console import TextConsole

# The editor MODE table (text_modes.py): here for `script_runtime`, which is
# what says a vault file is a program and which tier runs it.
try:
    import text_modes as _modes
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import text_modes as _modes

# EditorApp (editor_app.py): the tab ladder + PLAY. ws.menu_view is a
# forwarding projection of EditorApp.tab; ws.set_menu_view/_open_*/_leave_menu
# stay one-line forwards (tested surface).
try:
    from editor_app import EditorApp
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editor_app import EditorApp

# FullscreenStackWM (wm.py): viewport composite (#39), the back-stack `screen`
# projects onto, and the MEMOIZED visible/draw stack (rebuilt only on push/pop
# or overlay-gate change -- a static top-of-stack allocates no per-frame list).
# ws._composite_game/_game_xy/_viewport stay one-line forwards (tested surface).
try:
    from wm import FullscreenStackWM
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wm import FullscreenStackWM

# The unified multiplayer input router (#65): PlayerRouter maps input SOURCES to
# player SLOTS behind btn(name, player)/players(). Slot 0 is the local console
# (untouched); extra slots stay empty until a transport (USB pad / phone / ESP-NOW
# peer) registers one. Attached to the InputState in wire_workstation_core so host +
# both boards share it. Same bare-or-package fallback as the extracted modules above.
try:
    from players import PlayerRouter
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.players import PlayerRouter



# The console's stateless base layer -- the MOY64 palette (NAMES), the
# responsive Layout/CodeLayout geometry (#39), the themeable top-bar IconSheet slot map + art
# (_ICON), the cursor sprite (CURSOR), and the small pure helpers
# (_clamp_scroll/_cursor_delta/_ticks_*/_err_text) -- lives in chrome.py.
# Imported back so every bare reference below and every `console.X` a test
# still makes resolves (`_in` is one of those: nothing here calls it). chrome.py is a leaf (it imports only editors/widgets +
# surface geometry constants, none of which import back), so there is no cycle.
# Same bare-or-package fallback as the surfaces above.
try:
    from chrome import (_ticks_ms, _ticks_us, _ticks_diff, _err_text, CURSOR,
                        RESIZE_CURSORS, NAMES, THEME_VARIANTS,
                        _CURSOR_BASE, _CURSOR_ACCEL, Layout, CodeLayout,
                        _ICON, chrome_scale_floor,
                        chrome_scale_choices, _cursor_delta,
                        _clamp_scroll, _in, _SPLASH_MS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (_ticks_ms, _ticks_us, _ticks_diff, _err_text, CURSOR,
                                RESIZE_CURSORS,
                                NAMES, THEME_VARIANTS, _CURSOR_BASE, _CURSOR_ACCEL, Layout, CodeLayout,
                                _ICON,
                                chrome_scale_floor, chrome_scale_choices,
                                _cursor_delta, _clamp_scroll, _in, _SPLASH_MS)


# The Workstation's mixins -- what a kernel does not need to be in the same
# file with: the input routers, pointer and trackball (console_input), the
# home's verbs and cards (console_home), typing, text mode and the Editor's
# tab landings (console_text), a run's own canvas bound and released
# (console_canvas), the
# perf meters + query API (console_perf), the Settings toggles + OTA channel
# (console_settings), the Editor's save/PLAY verbs (console_saves), the
# notices, toast and achievements wiring (console_notices) and the run/exit
# spine (console_spine). Each is a bag of methods over `self`; the class below
# is where the state they touch is created. A method the class does not define
# is looked up base by base in the order listed, so the bases that hold the
# per-frame verbs come first, and the verbs `frame` itself calls stay on the
# class.
try:
    from console_input import InputVerbs
    from console_home import HomeVerbs
    from console_text import TextVerbs
    from console_canvas import CanvasBinding
    from console_perf import PerfMeters, _ema
    from console_settings import SettingsToggles
    from console_saves import SaveVerbs
    from console_notices import Notices
    from console_spine import SpineVerbs
    from moy_spine import AppRegistry, Leases, Returns
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.console_input import InputVerbs
    from runtime.console_home import HomeVerbs
    from runtime.console_text import TextVerbs
    from runtime.console_canvas import CanvasBinding
    from runtime.console_perf import PerfMeters, _ema
    from runtime.console_settings import SettingsToggles
    from runtime.console_saves import SaveVerbs
    from runtime.console_notices import Notices
    from runtime.console_spine import SpineVerbs
    from runtime.moy_spine import AppRegistry, Leases, Returns


def _kernel_tables():
    """(apps, back, returns, leases) as views of the kernel's own tables where
    the console runs on a kernel (a board: `moy_kernel` is in the image), so a
    VM stop leaves its routes and leases for the next VM
    (docs/history/kernel_cartpath_2026-10.md section 5.4); None elsewhere, where each
    console owns its own. A return start keeps them as they were; every other
    start puts them back as they are made."""
    try:
        import moy_kernel
        import moy_spine
    except ImportError:
        return None
    kernel = getattr(moy_spine, "kernel", None)
    if kernel is None:
        return None
    start = getattr(moy_kernel, "start", None)
    returning = start is not None and start() == "return"
    tables = kernel(not returning)
    if returning:
        # The route the stop recorded: a run that stopped the VM was HOME's
        # (moy_play_launch refuses any other), so its exit is the launcher.
        tables[1].goto("launcher")
        tables[2].spend()
    return tables


def _app_abi(rows):
    """The app ABI's state (native/moy_app): the kernel's where the console
    runs on a kernel (a board), so a VM stop leaves its grants and clipboard
    for the next VM, its prefs the kernel's settings rows; elsewhere this
    console's own, its prefs written into `rows`. A return start keeps the
    kernel's as it was but for the stopped run's grant, which it ends; every
    other start ends its run grants."""
    try:
        import moy_kernel
    except ImportError:
        return _moy_app.App(rows)
    start = getattr(moy_kernel, "start", None)
    returning = start is not None and start() == "return"
    app = _moy_app.kernel(not returning)
    if returning:
        # A compiled app's run that stopped the VM held its grant across the
        # stop (Player._stop_grant); that run is over now.
        app.wasm_end()
    return app


def _mix565(a, b, k):
    """moy_scr_mix: a + (b - a) * k / 256 per channel of two RGB565 words."""
    out = 0
    for sh, m in ((11, 31), (5, 63), (0, 31)):
        x, y = (a >> sh) & m, (b >> sh) & m
        out |= (x + (((y - x) * k + 128) >> 8)) << sh
    return out


def _lum565(c):
    """An RGB565 word's relative luminance (WCAG's)."""
    out = 0.0
    for sh, m, wt in ((11, 31, 0.2126), (5, 63, 0.7152), (0, 31, 0.0722)):
        v = ((c >> sh) & m) / m
        out += wt * (v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return out


def splash_pal(th):
    """The boot splash's palette for theme roles `th` (a theme_colors dict),
    as moy_scr.h's six RGB565 words in bytes: the ground, the ink, the dim
    ink, the value, and ui.progress's track -- the border mixed a quarter
    toward the dim ink, or where the value would not keep 3:1 on it the
    ground edged in the dim ink (Contrast's)."""
    from array import array
    try:
        from chrome import rgb565_of
    except ImportError:  # pragma: no cover - host package lane
        from runtime.chrome import rgb565_of
    bg, ink, dim = (rgb565_of(th[k]) for k in ("panel", "ink", "ink_dim"))
    value = rgb565_of(th.get("value", th["accent"]))
    rest = _mix565(rgb565_of(th["border"]), dim, 64)
    lo, hi = sorted((_lum565(value), _lum565(rest)))
    if (lo + 0.05) * 3 <= hi + 0.05:
        track = edge = rest
    else:
        track, edge = bg, dim
    return bytes(array("H", (bg, ink, dim, value, track, edge)))


def _kernel_splash():
    """The palette the kernel's first light drew with (moy_kernel.splash),
    or None off a console board."""
    try:
        import moy_kernel
    except ImportError:
        return None
    get = getattr(moy_kernel, "splash", None)
    return get() if get is not None else None


def keep_splash(th):
    """Keep theme roles `th`'s splash palette for the kernel's next first
    light, so the next boot lights the glass on the stored theme's ground;
    whether it wrote (a board keeps it once, in NVS)."""
    try:
        import moy_kernel
    except ImportError:
        return False
    keep = getattr(moy_kernel, "splash", None)
    if keep is None:
        return False
    try:
        return bool(keep(splash_pal(th)))
    except Exception:  # noqa: BLE001 -- a lost palette costs one boot's ground
        return False


def draw_splash(cv, frac=None, status=None, pal=None):
    """Paint the boot splash (docs/history/look_2026-10.md step 33): Moy at a whole
    scale over the `moybyte` wordmark on the theme's ground, and with
    `frac` (0..1) a track under it, with `status` a line in the dim ink.
    Drawn on the SYSTEM canvas so it fills the real panel.

    The picture is moy_scr's splash (native/moy_gfx/moy_scr.c), the C the
    kernel lights the glass with before any VM, so the two are one picture
    or the machine appears to start twice. `pal` (splash_pal's bytes)
    defaults to the one the kernel drew with -- the stored theme's, kept in
    NVS -- and to Moy Day's off a board. A free function over a canvas: the
    P4 spends most of a full-erase boot seeding carts, long before a
    Workstation exists.
    """
    if pal is None:
        pal = _kernel_splash()
    if pal is None:
        try:
            from chrome import theme_colors
        except ImportError:  # pragma: no cover - host package lane
            from runtime.chrome import theme_colors
        pal = splash_pal(theme_colors("moy", "light"))
    permille = -1 if frac is None else int(1000 * min(max(frac, 0.0), 1.0))
    cv.splash(pal, permille, status or "")


class Workstation(InputVerbs, PerfMeters, SettingsToggles, SaveVerbs, Notices,
                  SpineVerbs, HomeVerbs, TextVerbs, CanvasBinding):
    # The editor surfaces, built at their first use (_LAZY_MODULES above).
    block_ui = _first_use("block_ui", _build_block_ui)
    map_ui = _first_use("map_ui", _build_map_ui)
    scene_ui = _first_use("scene_ui", _build_scene_ui)
    music_ui = _first_use("music_ui", _build_music_ui)
    paint_layer = _first_use("paint_layer", _build_paint_layer)
    theme_config_layer = _first_use("theme_config_layer", _build_theme_config_layer)
    theme_sprites_layer = _first_use("theme_sprites_layer", _build_theme_sprites_layer)
    # The board's inputs as the hints know them (`use_inputs`): None until a
    # board declares them, and then the source names' kinds.
    input_kinds = None
    # The battery's gauge (device/battery.py): None until a board that reads
    # one sets it.
    battery = None
    _news = None
    _cart_keys = False              # what the input table was told last
    _menu_over = False              # a menu holds the keys over the surface (`_menu_keys`)
    _text_back = None               # `_text_sets` when that menu took text mode away
    _text_sets = 0                  # `_set_text_mode` calls, counted
    _pf_home = None                 # the home frame's split, under perf_capture (home_band)
    # A soft keyboard the board offers beside its inputs (the browser page's).
    soft_keyboard = False
    no_keyboard_text = NO_KEYBOARD_TEXT

    def __init__(self, comp, canvas, input, carts=None, sys_canvas=None,
                 font_scale=1, panel_diagonal_in=None):
        # Built in five ordered stages (each a method so the constructor reads
        # as a table of contents). The ORDER is load-bearing: the WM must exist
        # before anything reads/writes `screen`, Project/Player/EditorApp before
        # anything sets their forwarded fields, and the layer stack last.
        self._init_canvases(comp, canvas, sys_canvas, font_scale,
                            panel_diagonal_in)
        self._init_components(input, carts)
        self._init_state()
        self._init_perf()
        self._init_overlays()
        # The compositor/router layer stack (docs/history/shell_layers_refactor_v1.md). Built
        # once here; the WM's visible_stack()/draw_stack() z-order + gate them per frame.
        self._build_layers()

    def _init_canvases(self, comp, canvas, sys_canvas, font_scale,
                       panel_diagonal_in=None):
        """The two-domain canvas seam (#39), the WM, the theme and the responsive layouts."""
        self.comp = comp
        # The board's glass, in inches, or None when it did not say (#203). The
        # ONE fact behind the tap-target floor, and the reason the floor is an
        # opt-in: millimetres are the only unit that can answer "is this icon big
        # enough to hit", and only a board knows its own. Resolved to an integer
        # here, from the PANEL's size -- never from a window's, which is what
        # `chrome_floor` being a constructor-time field buys on the windowed tier.
        self.panel_diagonal_in = panel_diagonal_in
        _panel = sys_canvas if sys_canvas is not None else canvas
        # The metric class the glass lays out on (layout_base.class_of): a
        # declared glass of a desk's size is a desk, every other a handheld.
        # One per process, as the bars are.
        self.metric_class = _layout_base.class_of(panel_diagonal_in)
        _layout_base.place_class(self.metric_class)
        # The hints draw the desk keyboard's keys until the board declares its
        # inputs (`use_inputs`). One per process, as the metric class is.
        _ui_draw.input_kind("keys")
        # The glass's own canvas, and the half-size surface a `render.scale: 2`
        # desk theme draws on instead (`set_render_scale`); None at 1.
        self.glass = sys_canvas
        self.render_scale = 1
        self._half = None
        self.chrome_floor = chrome_scale_floor(_panel.w, _panel.h,
                                               panel_diagonal_in)
        # What Settings' CHROME SIZE may pick on this panel, resolved with the
        # floor and for the same reason: from the panel, never a window.
        self.chrome_scales = chrome_scale_choices(
            _panel.w // _layout_base.CLASSES[self.metric_class]["unit"], self.chrome_floor)
        # Two rendering domains (#39). The GAME canvas is the fixed 320x240 indexed
        # surface the cart + cart API draw on -- carts are UNCHANGED. The SYSTEM
        # canvas is the panel/window surface the desktop/launcher/settings + status
        # strip render on, responsive to its size + the system font scale; a
        # running cart (and, for step 1, the editors) draw on the game canvas and are
        # composited as a fixed-aspect, integer-scaled, centered viewport into it.
        # When sys_canvas is None (or the same size, the T-Deck default) the system
        # canvas IS the game canvas -- one object, so everything is pixel-identical
        # to today (graceful degradation), and the composite step is a no-op.
        self.canvas = canvas
        # A distinct SYSTEM canvas, or None for the degradation case where the system
        # canvas IS the game canvas. Kept as a separate field (not a hard reference to
        # `canvas`) so the property tracks `self.canvas` even if a backend swap
        # reassigns it later (the web console does `ws.canvas = CommandCanvas(...)`).
        self._sys_canvas = sys_canvas if (sys_canvas is not None
                                          and sys_canvas is not canvas) else None
        # Per-run cart canvas (SPEC.md 1/3.1): a cart declaring a smaller raster
        # plays on its own small canvas, bound by bind_run_canvas for the run and
        # restored at run death (Player.release_world). make_game_canvas is the
        # backend attach point (like make_api): a factory (w, h) -> canvas, or
        # None on a tier that can't build one yet -- Player then refuses cleanly.
        self.make_game_canvas = None
        # A compiled cart's frame that goes to the glass from the cart's own
        # memory (device/moycore_glue.CartFrame), set by the run for its life
        # on a system canvas that `presents_frames`; None everywhere else.
        self.cart_frame = None
        self._run_canvas = None            # the bound small canvas, while a run holds it
        self._run_canvas_stock = None      # what self.canvas was before the bind
        self._run_canvas_shared = False    # True when the bind promoted stock to system
        # A RESPONSIVE app cart (#181) draws on the SYSTEM canvas instead of the
        # fixed game one; a plain attribute (never a property -- the app-context
        # perf convention) so the chrome can read it once per frame. False for
        # every game, every fixed app cart, and every shipped system app, which
        # is what keeps the pixel goldens where they are.
        self.app_full_canvas = False
        # ...and in the windowed desk its window is its surface: a canvas the
        # desk's work area large, of which the window's content shows
        # (`app_window_size`), so a resize moves its content rect and no
        # pixels. No host strip is drawn on it; the window's title strip is
        # the way out.
        self.app_window_canvas = False
        # The LOOK (#209 landing D, appearance.py): theme + variant, the widget
        # skin, the requested system font scale, the theme's wallpaper and the
        # top-bar icon sheet. Built FIRST of the collaborators because the two
        # lines under it already need it -- the system canvas takes the font
        # scale and the responsive Layout takes the EFFECTIVE one.
        # `ws.theme_colors` is the one piece of that cluster's state that stays
        # HERE: ~70 surface sites read the token dict per draw (doc 3e's "token
        # reads stay flat"), and `look.set_theme` is its only author -- the
        # constructor on the next line writes it for the first time.
        self.look = Appearance(self, font_scale)
        if self._sys_canvas is not None:
            self._sys_canvas.set_font_scale(self.look.font_scale)
        # The window manager (Stage 6, wm.py): owns the game<->system viewport composite
        # (#39) + -- from Stage 6b/6c -- the process back-stack `screen` projects onto and
        # the memoized layer stack. Built here (before anything reads/writes screen or
        # composites) with a `ws` back-ref to the console's canvases + layer instances.
        self._kernel_tables = _kernel_tables()
        # The launcher's RUN in progress (open()): the run's route is HOME,
        # which the Player hands the kernel's stop verdict.
        self._home_launch = False
        self.wm = FullscreenStackWM(self)
        # windowed_chrome is a PROPERTY (mode-aware): it is True only in the
        # windowed WM's DESKTOP mode -- see the property below the screen
        # projection.
        # The bars start as the default's until the theme's layout resolves
        # (Appearance.apply_layout): they are one per process, as the glass is.
        _layout_base.place_bars(_layout_base.DEFAULT_BARS)
        self.layout = Layout(self.sys_canvas.w, self.sys_canvas.h,
                             self.look.effective_font_scale(),
                             self.look.effective_chrome_scale())
        # Responsive editor geometry (#39 step 2): the code + block editors now draw
        # on the SYSTEM canvas at native size, so their layout (visible cols/rows,
        # button rects, palette/menu) derives from (w, h, font_scale), as Layout's
        # does for the desktop. Rebuilt by _relayout on a size/font change.
        self.code_layout = CodeLayout(self.sys_canvas.w, self.sys_canvas.h,
                                      self.look.effective_font_scale(),
                                      self.look.effective_chrome_scale(),
                                      symbols=_ui_draw.input_kind() == "touch")
        # The block editor's UI (issue #29 Part 2, extracted from this class -- see
        # block_editor_ui.py): one instance, built at its first use (`block_ui`)
        # and delegated to from handle_input/handle_pointer/frame's menu_view ==
        # "blocks" branches plus set_menu_view/_relayout/_leave_menu/go_home/open.
        self._block_ui = None

    def _init_components(self, input, carts):
        """Injected-service attach points + the shell processes (Project/Player/
        EditorApp) + the extracted editor/HUD UIs."""
        self.input = input
        if input is not None:
            _moybyte.bind(input)
        # The (store, root, can_manage, with_sd) guard 4-tuple as ONE object
        # (#209 landing B, system_store.py). FIRST, because everything that
        # touches storage takes it: both store-owning collaborators below AND
        # the storage roles' servers. It captures nothing; every field is read
        # through `self` at the moment of use, because none of them is wired yet.
        self.store = StoreHandle(self)
        # system.json (#209 landing B): the store owns the rows and the hook
        # that writes them; `self.system` is a plain ALIAS of the rows and
        # neither name is ever rebound -- prefs.load() replaces them in place,
        # which keeps the launcher's per-paint favorites read, the prefs role
        # and the guards honest. Reads are `system.get`, writes are
        # `system.set`, and a write persists itself.
        self.prefs = SystemStore(self, self.store)
        self.system = self.prefs.rows
        # The app ABI's state (native/moy_app): the grants every AppContext
        # holds, the damage flags the frame gate folds, the clipboard, and
        # prefs written into the rows above. The kernel's on a board, so a VM
        # stop leaves it; this console's own elsewhere.
        self.app_abi = _app_abi(self.system)
        self._damage_take = self.app_abi.damage_take
        self._files_end_all = self.app_abi.files_end_all
        self._damage_drop = self.app_abi.damage_drop
        # The servers of the roles' shell rows (runtime/shell/shell_servers.py), and
        # the live token table the look writes at every switch. The surface
        # rows' pointer is bound by `set_pointer`; until then they answer none.
        _shell_servers.serve_all(self)
        self.look.publish()
        # The system clipboard (#132): the one buffer every editor writes
        # through (the code tab, a cart's editor handle), so copy in one app
        # pastes in another; the code tab's lane over a grant of its own.
        # Console-side end-to-end -- works identically over the web
        # transport, never touches a host OS clipboard (parity trap).
        self.clipboard = _moy_app.Clipboard(
            self.app_abi, self.app_abi.grant("editor", ("clipboard",)))
        self.make_api = None       # injected: make_api(canvas, input, cfg, sheet, audio, tilemap, pmem, wifi)->ns
        # Paint's document model (runtime/apps/artwork.py's ArtworkService), which
        # Paint builds over its own grant: the backdrop's My Art, the image
        # door and the Paint cart's identity reach it here. None until the
        # apps register, and in a build that carries no Paint.
        self.artwork = None
        self.audio_out = None       # injected where no feeder task plays the mix: a PcmPump
        self.audio = None           # the open cart's AudioSession (built on a run, #16)
        # WiFi (#38): a SYSTEM service shared across carts, not per-cart.
        # run_desktop/build_workstation injects the backend here; it's exposed
        # to a cart's namespace ONLY when the cart's manifest permissions
        # include "network" (capability-gated -- see _start). THE RADIO IS A
        # LEASE (console_spine's wifi_hold/wifi_release): the tags holding it,
        # and the last one out powers it down.
        self.wifi = None            # injected wifi backend (host FakeWifi / device WLAN)
        kt = self._kernel_tables
        self.leases = kt[3] if kt is not None else Leases()
        # Multiplayer message service (#65): the transport-neutral net.* seam (a
        # players.LoopbackNet in the host sim, None on the device until the ESP-NOW
        # radio lands). A SYSTEM service like wifi -- exposed to a cart's namespace
        # ONLY when its manifest permissions include "multiplayer" (see player.start).
        self.net = None
        # PHYSICAL PINS (#9): the transport-neutral gpio seam, and the THIRD
        # capability service beside wifi and net -- exposed to a cart's namespace
        # ONLY when its manifest permissions include "pins" (see player.start).
        #
        # Transport-neutral is the point, because there will be two backends. The
        # browser tier injects a `gpio_link.GpioLink`, which QUEUES writes and
        # answers reads from the last batch because a frame may not wait on a
        # network round trip; a board whose [hardware] has a gpio lease row
        # gets the lease table's direct driver (leases.LocalPins). The cart verbs
        # are the same two names either way.
        # The CONTRACT is written at the remote backend's level (a write is
        # queued, a read may be one round trip stale), so a native backend is a
        # strict improvement and no cart has to know which it got.
        self.gpio = _hwleases.local_pins()
        # #65 Phase 2: the live two-console LOCKSTEP session (players.LockstepSession,
        # a view of the kernel's moy_match), set by the link once two consoles have
        # agreed on a cart and a seed, and torn down with the run. None = solo, and
        # every cart behaves as it always has.
        self.netplay = None
        # The RADIO that forms those sessions (players.EspNowLink over the kernel's
        # link), injected by run_desktop on the boards that have one. None on the
        # host and the browser -- and absence is the whole interface: nothing here
        # probes for a radio, it is simply never armed. The kernel's loop polls the
        # link in C; the console's frame takes what it left for the console.
        self.link = None
        # The board's front (device/desktop_spine.py): hands the kernel's Player
        # the compositor a VM-free game's run is the loop's foreground on. None
        # on the host and the browser, which keep the console's frame.
        self.front_bind = None
        self.carts_store = None     # injected: cart store module (moy_carts API)
        # The cart-runtime seam (#67, native/moy_wasm/README.md): a
        # manifest's "runtime" name -> factory(ns, src) returning a running cart
        # handle (.init/.update/.draw callables + .close()). build_workstation
        # maps "lua" to moycore_glue's run (over runtime/host/moycore.py) and "wasm"
        # to runtime/wasm_host's; the device maps moycore_glue's. A runtime this build lacks is an
        # ABSENT KEY, and a cart naming it opens the error panel.
        self.runtimes = {}
        # OTA firmware updater (#53): injected by the device (moy_ota.OtaUpdater); None
        # on the host. When present AND the build is OTA-capable, Settings grows an
        # "UPDATE FW" row that flashes a new image from /sd/update to the inactive slot.
        self._updater = None
        self.c6_updater = None   # P4 only: the radio co-processor's updater (#7/#58)
        # The network Get Carts fetches carts through (#124, runtime/carts/cart_index.py):
        # `online()` and `open(url)`, injected by a board (device/wire_links.CartNet),
        # by the live simulator (host_app.HostCartNet) and by a browser page
        # that keeps its own carts (firmware/web_runner/carts_link.py). None
        # elsewhere -- the app then says this console has no way to fetch.
        self.cart_net = None
        # The rest of the browser's half of Get Carts, None everywhere else:
        # `cart_keep`, the keeper that makes an install durable in OPFS;
        # `cart_pick(name, size, host)`, the page's file picker for an external
        # file the page cannot fetch; `cart_home`, "board" or "headless" on a
        # page a console serves, whose carts that console gets itself.
        self.cart_keep = None
        self.cart_pick = None
        self.cart_home = None
        # Serve the web console FROM this console (moycore plan 3.4 pull half):
        # injected by the device (moy_webhost.WebHost); None on the host and on a
        # build without it, which is what makes the Settings row appear only where
        # it can work. Contract, deliberately four members so the shell needs to
        # know nothing about sockets: `.serving` (bool), `.start()`, `.stop()`,
        # `.url()`.
        #
        # NOT the old web view. That one streamed PIXELS and was sunset (plan
        # 3.2); this hands the browser the wasm console once and then only cart
        # data crosses. The name differs for the same reason the sunset test
        # pins the old one's absence -- they are opposite designs, and a reader
        # who conflates them will re-derive the wrong bug list.
        self.webhost = None
        self._updater_ok = None     # cached updater.available() (cheap, but not per-frame)
        self._online_ok = None      # cached updater.online_available() (#53 Phase 3)
        # The firmware-update SCREEN (#53, extracted from this class -- see
        # update_ui.py): its confirm/download/install/done lifecycle + pump +
        # drawing, delegated to from handle_input/handle_pointer/frame's
        # screen == "update" branches and from _activate_settings_action. The
        # transient screen state (_upd_phase/_upd_msg/_upd_bin/...) lives on it;
        # the queries + channel config above/below stay here.
        self.update_ui = UpdateUI(self, NAMES, _err_text)
        # WASM MODE (#197): the pin, the paired url, the connection screen and
        # the parked flag, all on one object (web_console.py). The webhost above
        # stays a flat Workstation attribute -- the kernel loop's service upcall
        # polls it at the frame tail while it serves -- and this reads it
        # through `self`.
        self.web = WebConsole(self, NAMES)
        # The shelf's cover + icon pipeline (#209 landing C, cover_cache.py): the
        # bounded caches, the per-frame build budget, the idle warmers and the
        # #186 off-heap frees, all on one object. FRAME-HOT -- the grids below
        # take `covers.cover_for` as a bound method, and `frame()` calls exactly
        # `begin_frame` at its top and `take_deferred` at its tail. `covers.gen`
        # is the shelf's repaint key and has no `ws` mirror.
        self.covers = CoverCache(self)
        # The shelf's ROSTER (#209 landing C, cart_manager.py): the scanned cart
        # list plus everything that changes it -- new/duplicate/delete, the sync
        # re-scan, the #66 slim/rehydrate diet and the #105 favorites/recents.
        # `carts.all` is the single source both grids derive from and has no `ws`
        # mirror: a re-scan REBINDS the list, so an alias could not stay honest.
        self.carts = CartManager(self, self.store, carts)
        self._fat_cart = None         # #66 live-set diet: the one rehydrated cart
        # Launcher search (#105): plain-text substring filter over the run-grid,
        # entered via the sysmenu SEARCH item (mirrors the wifi-password typing
        # idiom -- _set_text_mode swaps the keyboard to clean ASCII while typing).
        # search_query persists once typing stops (so the filtered grid stays up
        # for d-pad/trackball browsing); search_typing is just the keystroke-
        # capture sub-state. See _launcher_view_items/open_search/close_search.
        self.search_query = ""
        self.search_typing = False
        # The home's carousel over the Games tab; each tab remembers its card.
        self.launcher = self._home_grid("carousel",
                                        self._launcher_items(self.carts.all, "games"))
        # The home window's card model (the desk's PROJECTS, `open_projects`):
        # minted on its first open, so a console-only board never holds one.
        self.projects_grid = None
        # Screen states (#28): "launcher" is now the DESKTOP home (wallpaper + cart
        # icon grid); "desktop" is a running cart; "menu" is the cards/code/
        # paint/map editors; "settings" is the Settings app.
        # (Stage 6b) `screen` is now a read-only PROJECTION of the WM back-stack top
        # (self.wm.top_kind() -- see the property below), not a plain attribute: the
        # back-stack (built with the WM above, root "launcher") is the state of record.
        # Every `self.screen = X` / `ws.screen = X` still works -- the setter routes it
        # into the stack via wm.goto (the same read-write shim ws.menu_view is over
        # editor_app.tab). No initial assignment needed: the WM inits the stack to
        # ["launcher"], so the projection already reads "launcher" here.
        self._splash_until = None     # boot logo deadline (_ticks_ms); None = no splash
        # The open cart's live WORKSPACE (Stage 1, project.py): the DATA of the cart
        # currently open -- cart/config/sheet/tilemap/images/pmem. Those six are
        # forwarding properties (below) delegating to self.project, so every surface
        # file + test that reads/writes ws.cart/ws.sheet/... is unchanged. Constructed
        # idle here (all fields None -> the boot launcher state) BEFORE anything can set
        # ws.cart, and rebuilt per open() when a cart is opened.
        self.project = Project(self)
        # The cart PLAYER (Stage 2, player.py): the run-loop object. Built idle here
        # (BEFORE anything can set the forwarded cart-run fields below), reused across
        # runs (start() re-inits it). The fields it owns -- ns/_update/_draw,
        # cart_error/crash_line, _cart_start_ms/_cart_key_prev + the Stage-5 exit-gesture
        # state -- live on it now and the cart-run ones are exposed back as forwarding
        # properties (below), so every surface file + test reading ws.cart_error/
        # ws._update/... is unchanged. (Stage 5 retired the #71 cart_paused/_bks_prev.)
        self.player = Player(self, NAMES)
        # The running set beyond the front run: the games stepped beside it
        # (runtime/shell/procs.py, docs/os_2026-11/processes.md §1).
        self.procs = Procs(self)
        _hwleases.LEASES.grants = self.procs.granted
        _devices.grants = self.procs.granted
        _devices.console = self
        _devfs.RUNS = self.procs.runs
        # The TEXT CONSOLE (text_console.py): a script's screen, and #115's
        # terminal scrollback after it. One instance for the console's life
        # (its ring is preallocated, so a run costs no allocation); `run_script`
        # below is what starts a script on it.
        self.script_console = TextConsole(self, NAMES)
        # The EDITOR app (Stage 3, editor_app.py): owns the tab ladder + the active-tab
        # state (EditorApp.tab). Built idle here (BEFORE anything can set menu_view,
        # which is now a forwarding projection of editor_app.tab -- see below). The tab
        # machine (set_menu_view/_open_*/_leave_menu) moved onto it; ws keeps one-line
        # forwards so every surface file + test is unchanged. NAMES is injected
        # (Stage 4, docs/history/shell_ux_technical_plan_v1.md): the Editor lends the top
        # bar's left zone (draw_zone/zone_tap, bar_layer.py) so it needs the shared
        # draw toolkit, like the other zone-owning surfaces.
        self.editor_app = EditorApp(self, NAMES)
        # The registered system apps (filled by _init_apps) and the return
        # records: a run's caller kind, popped by _exit_to_caller / exit(), and
        # the app an app-to-app jump left, popped by _go_home_or_back.
        kt = self._kernel_tables
        self.apps = kt[0] if kt is not None else AppRegistry()
        self.returns = kt[2] if kt is not None else Returns(self.apps)
        # (The cards menu's selection/scroll state -- msel/mtop -- lives on
        # self.cards_layer now, built in _build_layers with the rest of the stack.)
        # (The active menu sub-view -- "cards"|"code"|"paint"|"map"|"blocks"|"music"|
        # "theme" -- lives on self.editor_app.tab now (Stage 3); ws.menu_view is a
        # forwarding projection of it, so every reader/writer is unchanged.)
        self.editor = None            # CodeEditor while menu_view == "code"
        self.code_file = None         # WHICH script that editor holds (SPEC.md 4);
                                      # None = the cart's main file, which is every
                                      # cart that has one -- see code_sources()
        # The document the console has been asked to open, parked for the text
        # cart's namespace build (take_text_request). One slot: it is set and
        # consumed inside a single launch.
        self._text_request = None
        # The project a text run must return INTO (open_project_file): set with
        # the request, popped by _exit_to_caller, which re-reads the folder on
        # the way back so the loader sees what was written; HOME ends it.
        self._project_return = None
        # A RUN an editor handle asked for (`ask_run`): the handle, parked
        # until the frame that asked is drawn (`_run_asked`).
        self._editor_run = None
        # The cart a script run from its editor handle must return INTO
        # (`_return_to_runner`): (cart, kind, name, mode, where its view
        # stood, the cart's own caller kind), popped by _exit_to_caller.
        self._script_return = None
        # Where an Editor the console-mode home handed to the desk goes back
        # to when its window closes (`open_in_editor`, `editor_closed`):
        # (tab, the card's path, the desk's windows then), or None.
        self._editor_from = None
        # The #111 UNDO ROUTER (#209 landing E, history_router.py): the bar
        # UNDO/REDO pair over both undo mechanisms (each Editor tab's in-RAM op
        # stack, then the tab-scoped durable journal walk), the code tab's typing
        # burst, and the idle-typing autosave `frame()` ticks. Built here, where
        # its state used to sit -- it reaches the project, the editor and the
        # store through `self` per call, so nothing it needs has to exist yet.
        # `history.edit_ms` is the ONE piece the kernel writes: handle_input
        # stores the keystroke tick straight onto it (doc 3e -- an attribute
        # store per keypress, never a call).
        self.history = HistoryRouter(self, self.store)
        # (cart/config/sheet/tilemap/images/pmem live on self.project now -- Stage 1;
        # ns/_update/_draw/cart_error/crash_line/_cart_start_ms/_cart_key_prev live on
        # self.player now -- Stage 2; both exposed as forwarding properties, so
        # ws.sheet/ws.cart_error/... are unchanged.)
        self.paint = None             # PaintEditor while menu_view == "paint"
        # The map (tilemap) editor's UI (#32, extracted from this class -- see
        # map_editor_ui.py): one instance, delegated to from handle_input/
        # handle_pointer/frame's menu_view == "map" branches plus set_menu_view/
        # _open_map/open/go_home.
        self._map_ui = None             # built at its first use
        # The scene placement editor's UI (#85 Stage 2 -- see scene_editor_ui.py):
        # one instance, delegated to from the "scene" content layer plus
        # set_menu_view/_open_scene/open/go_home, the exact map_ui lifecycle.
        self._scene_ui = None             # built at its first use
        # Block editor (#29 Part 2) state now lives on self.block_ui (built above).
        # The music/sound editor's UI (#50, extracted from this class -- see
        # music_editor_ui.py): one instance, delegated to from handle_input/
        # handle_pointer/frame's menu_view == "music" branches plus set_menu_view/
        # _open_music/open (NOT go_home -- see music_editor_ui.py's docstring).
        self._music_ui = None             # built at its first use
        # The perf HUD's rendering (#43/#44, extracted from this class -- see
        # perf_hud.py): the FPS chip + frame-time breakdown drawn in frame() and
        # the tap target hit-tested in handle_pointer. Named perf_ui (NOT
        # perf_hud -- that stays the tested boolean flag for "breakdown shown?",
        # set in _init_perf). The perf query API (perf_sample/perf_breakdown/...)
        # is the PerfMeters mixin's (console_perf.py, the device diag contract).
        # Pure read-only consumer of the timing fields.
        self.perf_ui = PerfHud(self, NAMES)

    def _init_state(self):
        """Store/settings/wallpaper/cache/status fields (the mutable shell state)."""
        self.keyboard = None          # set by run_desktop (for raw/text mode toggle)
        # (The code editor's keyboard-edge tracker (_ekey_prev) + drag-scroll origin
        # (_drag) + its text view (_tv) live on self.code_layer now.)
        # (The paint drag-stroke origin -- _paint_drag -- lives on self.paint_layer.)
        # (The launcher's cursor-hover state (_lhover) lives on self.launcher_layer.)
        self.pointer = None           # set by run_desktop
        # The trackball's steps where a surface takes the keys (`ball`): its
        # source, the pulses toward a step on each axis and when the last
        # came, the click's press held now, and the coast (BALL_COAST): its
        # direction, and its motion, a fling on the toolkit's scroller
        # (COAST_UNIT a step) with the steps it has delivered.
        self._ball_src = None
        self._ball_ax = self._ball_ay = 0
        self._ball_at = 0
        self._ball_axis = 0             # the axis a roll is stepping on: 1 x, 2 y
        self._ball_axis_at = 0          # its last pulse
        self._ball_down = None
        self._coast, self._coast_rate = _ball_coast()
        self._coast_dir = None
        self._coast_r = _ui_draw.ScrollRegion()
        self._coast_r.set((0, 0, 0, 0), 1 << 24)
        self._coast_got = 0
        self._coast_fed = False
        self._ball_rate_at = 0
        self.ball_owed = False        # a press is held or a coast moves: `ball` runs next frame
        self.ball_stepped = 0         # every step a roll delivered (the bench's count)
        # Crash isolation (#160 / Phase 8): the rows themselves, because they
        # stay the same object across a load. One ledger per role -- see
        # runtime/shell/crash_guard.py, "Two roles, two ledgers".
        self.app_guard = CrashGuard(self.system)
        self.consent = _app_perms.Consent(self.system,
                                          lambda: _app_perms.services(self))
        self.wallpaper_guard = CrashGuard(self.system, key=WALLPAPER_KEY)
        # Desktop wallpaper (#28): a chosen wallpaper-type cart compiled into its
        # own namespace and run (its _draw, optionally _update) as the BACKDROP each
        # home/settings frame -- the Picotron "wallpaper is a cart" model. The
        # component owns the RENDERING + the compiled-cart cache; the CHOICE
        # (`ws.look.wallpaper_id`, the theme's, D6) and the picker verbs are
        # the look's.
        self.wallpaper = Wallpaper(self, NAMES)
        # The idle ladder's SAVER rung (idle_ladder.py): the screen it shows
        # while the kernel's ladder sits there, None otherwise.
        self._saver = None
        # Expensive-event counters (2026-07-26). See note_cost.
        self.costs = {}
        self._quiet_frames = 0        # consecutive frames the redraw gate skipped
        # The kernel's own draws (§15 of the surface model): the epoch this
        # console last painted under, and how many more frames repaint because
        # a buffer the backend retains may still hold the kernel's picture.
        self._kepoch = _glass.kernel_epoch()
        self._kframes = 0
        self._idle_warned = False     # the idle branch's backstop has spoken once
        # Unified top bar (Stage 1): _bar_img_cache memoises tile_image(slot) per
        # kind so the SAME _SheetSprite is reused every frame -- on the device that
        # keeps its per-Image RGB565 blit cache alive (one cached blit per icon),
        # the whole point of moving the bar to sprites. It backs ws._icon (the
        # shared draw toolkit) and stays here; the SHEET itself is the look's
        # (`ws.look.icon_sheet`), and `look.set_icon_sheet` clears this.
        self._bar_img_cache = {}      # icon kind -> cached _SheetSprite (or None)
        # The unified top bar (#46) is its own surface now (BarLayer, Phase 2
        # of docs/history/shell_layers_refactor_v1.md): the running-cart strip cache (#43), the
        # per-second clock cache (#66) and the bar tap slices live
        # on self.bar_layer; a look change reaches its strips through ws.look.look_gen.
        self.bar_layer = BarLayer(self, NAMES)
        # (Settings' focus and page scroll live in self.settings_layer's UI
        # context, built in _build_layers with the rest of the stack.)
        self.carts_root = None        # SD carts dir (reads); set by run_desktop
        # (cart_error + crash_line live on self.player now -- Stage 2; forwarding
        # properties below, so ws.cart_error / ws.crash_line are unchanged.)
        self.code_err = None          # short inline syntax-error message (#24)
        self.code_err_row = None      # 0-based row the syntax error is on (#24)
        self.crash_popup = None       # full error text of the popup the code
                                      # editor shows after a crash-to-code throw
                                      # (owner ask 2026-07-23); dismissed by a
                                      # tap or the first edit
        # (The Stage-7 idle-typing debounce -- edit_ms + edit_debounce_ms -- lives
        # on self.history now; #209 landing E.)
        self.paint_status = None      # last sprite-reuse (GET/PUT) result text (#18)
        self.can_manage = True        # writes enabled? run_desktop sets this from
                                      # whether SD is the cart source (carts_root)
        # SD session wrapper: mounts SD for the duration of fn(), then releases it
        # so the render loop's flushes never collide on the shared bus. On device
        # run_desktop swaps in moybyte_sd.with_sd_live (native moy_sd attach). The
        # default is a host passthrough.
        self._with_sd = lambda fn: fn()

    def _init_overlays(self):
        """Achievements/eggs (#21), the system menu (#52), device hooks, and the
        #44 redraw gate."""
        # THE BANNER'S DEADLINE (#209 landing B, rev 3). An unlock PUSHES it
        # at event time, and the per-frame gates (`_animating`, the WM's
        # `_overlay_sig`) read this plain int and nothing else. Same shape as
        # `_notice_until` below; 0 means down, and `ach` keeps only the PAYLOAD
        # the banner draws, which is read only while its deadline is up.
        self._toast_until = 0          # _ticks_ms the unlock banner hides at
        self._toast_at = 0             # _ticks_ms it was armed (a secret's confetti bursts from it)
        # Achievements (#21): a small set of fun milestones + the hidden Easter-egg
        # rewards, held by the achievements process (runtime/system/achievements.py),
        # which load_achievements() starts over the store's document.
        self.ach = _AchLink(self)
        # The three Easter eggs (#21, achievements_ui.py): their trigger state
        # (_konami_pos/_clock_taps/_secret_taps), reset on screen changes
        # (go_home/settings/desktop tap) so a stray sequence never carries
        # between contexts.
        self.ach_ui = AchievementsUI(self.app_context("eggs", AchievementsUI.NEEDS))
        # A transient SYSTEM banner (#53): something the machine did on its own and
        # the kid should hear about without going looking. Distinct from the
        # achievement toast, which celebrates something the KID did. First user is
        # the firmware-update verdict -- an update lands during a reboot, so there
        # is no screen it could otherwise report to.
        self._notice = None            # (title, sub, kind) while up
        self._notice_until = 0
        # An app's notifications (runtime/system/notifications.py): the
        # banner over whatever is up, and what acting on one does.
        self._init_notes()
        self._init_status()
        # The popover (#52, docs/theme_and_shell_2026-11.md §8.6): ONE Popup owns
        # its open/selected state and what it holds -- the system menu's rows
        # (the SYSTEM group always, a CART group prepended while a cart is
        # open) or a bar widget's component. `_about` is a tiny dismissible
        # info modal the About row pops. `popover` names the same object.
        self.sysmenu = Popup()
        self.popover = self.sysmenu
        self._about = False
        # The ≡ dropdown / system menu's UI (#52, console_notices.SystemMenuUI):
        # the row builder + per-item actions; the drawing is the kernel's chrome.
        # toggle_sysmenu() + the sysmenu Popup + _about flag stay here (tested ws.
        # surface); the menu just delegates item-building/drawing to this.
        self.menu_ui = SystemMenuUI(self)
        # The pause menu over a fullscreen game (docs/history/look_2026-10.md step 25a):
        # Super holds the run and shows it; Esc or Super again gives it back.
        self.pause = PauseMenu(self)
        # the layers a modal's hold keeps what is under it in, and their
        # holders (ui_draw.Hold, `held_layer`)
        self.held_pages = [None, None, None, None]
        self.confirm = DeleteConfirm(self)   # the question before a cart is deleted
        self._marks_held = False      # a modal turned the focus marks off
        # Reboot hook: the device injects a callable (machine.reset via the OTA
        # updater); None on the host -> the Reboot row is a safe no-op (go_home).
        self.reboot_hook = None
        # The process the keyboard reaches (`wm.input_holder`), as the frame
        # last saw it, and the one that last set the keyboard's text mode.
        self._holder = None
        self._text_by = None
        # Redraw-on-change (#44 step 1): a static UI screen costs ~0 -- frame() only
        # redraws + flushes when something visible changed. `_dirty` is the "redraw
        # this frame" flag; it starts True so the very first frame always paints, and
        # is set whenever input/state could have changed the picture (mark_dirty()).
        # `_last_ptr` snapshots the pointer state actually drawn so a cursor move/hide/
        # click triggers exactly one redraw. A running cart and a live wallpaper /
        # overlay effect animate every frame -> always dirty -> unchanged full-redraw
        # behaviour for them. `frame_state.frames_drawn` counts the frames that
        # actually painted (idle frames are skipped) -- a host-testable witness
        # of the win.
        self._dirty = True
        self._last_ptr = None         # (x, y, visible, down, click) last drawn, or None
        self.ui_moving = False        # a verbs page the last painted frame drew moves on its own
        # what the last painted frame's change was, as its shift frames said:
        # "shift", "rows", None (whole, or nothing spoke); tools/input_bench.py
        self.frame_change = None
        self._dirty_in = True         # a change reported before this input (handle_input)
        self._press_owed = False      # a pointer press frame() marks dirty (handle_input)
        self._services_due = True     # the services start at the first drawn frame
        self._press_taken = False     # ...unless a popover's change took it (popover_touch)
        self._frame_requested = False # a draw asked for another frame (request_frame:
                                      # a coasting fling re-arms the gate the
                                      # covers.take_deferred way -- set DURING a draw,
                                      # consumed after the gate cleared _dirty)

    # -- the layer stack (compositor / router) -------------------------------

    def _build_layers(self):
        """Register the console's surfaces as Layers (Phase 0: `_LegacyLayer` shims
        over the existing `_draw_*`/input methods, so behavior + draw order are
        unchanged). `_content_layers` is keyed by screen/menu_view (the active content
        is a registry lookup, not a 12-arm branch); the overlays + cursor are separate
        instances gated by `_overlay_stack()`."""
        L = lambda id, domain, draw=None, kbd=None, ptr=None: _LegacyLayer(
            self, id, domain, draw=draw, kbd=kbd, ptr=ptr)
        # The cards ("Make it mine") surface is its own Layer now (#3/#15), owning its
        # selection/scroll state (msel/mtop) + draw + taps; cart config/apply stay on ws.
        self.cards_layer = CardsLayer(self, NAMES, _err_text)
        # The PAINT editor surface (#4/#30) over the cart sheet ("paint"). It reads
        # ws.paint / ws.sheet + dispatches SAVE/GET/PUT/CLOSE to ws. The theme mode's
        # tabs ("theme_config", "theme_sprites") are runtime/editors/theme_editor.py's.
        self._paint_layer = None      # built at its first use
        self._editor_desk = None      # the desk Editor, at its first use on a desk
        self._theme_config_layer = None
        self._theme_sprites_layer = None
        # The Settings app (#28/#39/#53): the aggregator screen. Owns its page of
        # rows and panels; reads ws config/system state + dispatches every
        # mutation to ws setters (it owns NO config).
        self.settings_layer = SettingsLayer(self, _ui_draw.AppHost)
        # The task manager (runtime/system/tasks_ui.py): the running set and
        # the services, their CPU and memory, Switch to and Close.
        self.tasks_layer = TasksLayer(self)
        self._tasks_return = None     # the kind a handheld's task manager goes back to
        # The system APPS are constructed AND registered below, from the
        # declarations (`_init_apps`) -- Paint's indexed document + reflowing
        # chrome, Storybook's compiling decks, Files' user-files gallery and
        # Calc. Each is a `system_carts/<folder>/manifest.json` "app" block,
        # not a line here.
        # The Python code editor (#24/#39): the full-screen text view. Owns the drawing
        # + code-UI state (keyboard edge / drag / highlight memo); the shared ws.editor
        # handle + save_code/run_code + code-error state + code_layout stay on ws.
        self.code_layer = CodeLayer(self, NAMES)
        # The desktop home / launcher (#28): the home composition + grid nav. The Launcher
        # GRID instance stays ws.launcher (the single source); this Layer draws it.
        self.launcher_layer = LauncherHomeLayer(self, NAMES)
        # The same home in a desktop-mode window (the desk's PROJECTS) over
        # ws.projects_grid.
        self.projects_layer = HomeWindowLayer(self, NAMES)
        # Content layers (exactly one active per frame, chosen by screen/menu_view). Every
        # surface is its own Layer/component -- the running-cart "desktop" is the
        # _PlayerLayer adapter over ws.player; the only _LegacyLayer entries are the four draw-only overlays
        # (splash / toast / notice / cursor, built by `L` above).
        # SYSTEM APPS (docs/app_api_v1.md) are NOT listed here -- register_app below
        # adds each one's kind to this table.
        self._content_layers = {
            "launcher": self.launcher_layer,
            "projects": self.projects_layer,
            "settings": self.settings_layer,
            "tasks": self.tasks_layer,
            "update": _UpdateLayer(self),
            "webconsole": _WebConsoleLayer(self),   # #197: the parked wasm mode
            "desktop": _PlayerLayer(self),   # Stage 2: the run loop is ws.player

            "code": self.code_layer,
            "blocks": _BlocksLayer(self),
            "music": _MusicLayer(self),
            "map": _MapLayer(self),
            "scene": _SceneLayer(self),
            "cards": self.cards_layer,
        }
        # -- SYSTEM APPS (docs/app_api_v1.md): a cartridge identity backed by a
        # responsive system process. ONE registration wires everything an app
        # needs -- the launcher dispatch (is_app claims the cart), the router
        # entry (kind -> layer), the back-stack/window kind, keyboard text mode,
        # the windowed resize minimum, and the relayout fan-out. Insertion order
        # is dispatch precedence.
        self._apps = []
        self._apps_by_id = {}
        self._init_apps()
        # The boot logo is a draw-time takeover of the screen content (input still
        # routes to the underlying screen), so it's not in _content_layers.
        self._splash_layer = L("splash", "system", draw=lambda dt: self._draw_splash())
        # Transient overlays (gated in _overlay_stack) + the cursor. The perf HUD is
        # a game-domain overlay (drawn before the composite, riding the viewport); the
        # rest are system-domain, on top of the composited screen.
        self._perf_layer = _PerfLayer(self)
        self._toast_layer = L("toast", "system", draw=lambda dt: self._draw_toast())
        self._notice_layer = L("notice", "system", draw=lambda dt: self._draw_notice())
        self._note_layer = L("note", "system", draw=lambda dt: self._draw_note(),
                             ptr=self._note_pointer)
        self._rec_layer = L("rec", "system", draw=lambda dt: self._draw_rec())
        self._sysmenu_layer = _SysMenuLayer(self)
        self._pause_layer = _PauseLayer(self)
        self._confirm_layer = _ConfirmLayer(self)
        self._about_layer = _AboutLayer(self)
        self._cursor_layer = L("cursor", "system", draw=lambda dt: self._draw_cursor())

    def _content_layer(self):
        """The active content layer, keyed by the back-stack top / menu_view (never the
        splash -- the boot logo is a draw-time takeover, see draw_stack). This is the
        layer keyboard input routes to. Stage 6d: the router asks the WM stack top (the
        source of truth) directly rather than the `screen` projection string -- same
        answer, one fewer production reader of the string. Still ONE string-keyed
        registry lookup: the stack is a data structure it reads, not a second router."""
        kind = self.wm.top_kind()
        if kind == "menu":
            return self.editor_layer()
        return self.layer_of(kind) or self._content_layers["launcher"]

    def editor_layer(self):
        """The Editor's content layer: the desk Editor's workspaces where they
        show (runtime/editors/editor_desk.py), else the open tab's surface."""
        desk = self.editor_desk
        if desk is not None and desk.active():
            return desk
        return self.layer_of(self.menu_view) or self._content_layers["cards"]

    @property
    def editor_desk(self):
        """The desk Editor (`EditorDesk`), built at its first use on a desk;
        None on a handheld, which never loads it (the S3 images do not carry
        it: their board.toml denies `editor_desk.py`)."""
        desk = self._editor_desk
        if desk is None and _layout_base.metric_class() == _layout_base.DESK:
            try:
                from editor_desk import EditorDesk
            except ImportError:  # pragma: no cover - host fallback
                try:
                    from runtime.editor_desk import EditorDesk
                except ImportError:  # an image without the desk tier
                    return None
            desk = self._editor_desk = EditorDesk(self, _ui_draw.AppHost)
        return desk

    def built(self, attr):
        """The editor surface `attr` names (`block_ui`, `map_ui`, ...) when it
        has been built, else None: what a reset or a check reads without
        building one."""
        return getattr(self, "_" + attr, None)

    def layer_of(self, key):
        """The content layer for router key `key`, None for none. The paint and
        theme surfaces join the table at their first use."""
        layer = self._content_layers.get(key)
        if layer is None and key in ("paint", "theme_config", "theme_sprites"):
            layer = self._content_layers[key] = getattr(self, key + "_layer")
        return layer

    @property
    def windowed_chrome(self):
        """True in DESKTOP mode (the windowed WM's desk, docs/theme_and_shell_2026-11.md
        §8.1): in-window app bars suppress the OS widgets, and the bar's wifi
        icon deep-links to the Settings window. False in console mode -- on
        every board, a P4 included -- where the fullscreen chrome rules apply
        verbatim."""
        wm = getattr(self, "wm", None)
        return wm is not None and wm.desk_open()

    # The overlay/visible/draw stacks are now built + MEMOIZED by the WM (Stage 6c,
    # wm.py) -- rebuilt only on a back-stack push/pop, a menu_view tab switch, or an
    # overlay-gate/splash flip, so a static top-of-stack allocates no per-frame list
    # (the #66 perf-recovery win). frame() walks self.wm.draw_stack() and
    # handle_input/pointer walk the cached reversed visible stack
    # (wm.visible_stack_rev()) directly, so the hot path never allocates.
    def _overlay_stack(self):
        return self.wm.overlay_stack()

    @property
    def sys_canvas(self):
        """The SYSTEM canvas the desktop chrome + overlays render on (#39). A distinct
        SystemCanvas when one was supplied, else the GAME canvas itself (degradation:
        one surface, pixel-identical to today). Reading through `self.canvas` keeps it
        correct even if a backend swaps the game canvas (e.g. the web CommandCanvas)."""
        return self._sys_canvas if self._sys_canvas is not None else self.canvas

    @property
    def glass_canvas(self):
        """What the glass shows: the panel's canvas, which a `render.scale: 2`
        frame is doubled onto; the system canvas where the shell draws on the
        glass itself."""
        return self.glass if self.glass is not None else self.sys_canvas

    def set_render_scale(self, n):
        """Draw the shell on a surface 1/`n` of the glass, doubled onto it at
        the end of every drawn frame (`render.scale`, the 2026-10 look's
        GAPS 11). Only a desk with a system canvas of its own takes 2; every
        other glass draws at 1. The layouts keep the desk's glass metrics on
        the smaller surface (`layout_base.place_class`), the pointer lands in
        its pixels (`Pointer.div`), and the windows rebuild at its size."""
        glass = self.glass
        if (n not in _layout_base.RENDER_SCALES or glass is None
                or self.metric_class != _layout_base.DESK):
            n = 1
        if n == self.render_scale:
            return
        half = self._half
        wm = getattr(self, "wm", None)
        if n == 1:
            root = glass
            glass.set_font_scale(half.font_scale)
        else:
            root = glass.new_layer(glass.w // n, glass.h // n)
            root.set_font_scale(glass.font_scale)
            # the meaning of blit_game the tier chose for the glass (a desk's
            # composite fills no bezel: DeviceCanvas.letterbox_composite)
            root.letterbox_composite = glass.letterbox_composite
        self._half = None if n == 1 else root
        self.render_scale = n
        if wm is not None and getattr(wm, "_root_canvas", None) is not None:
            wm._root_canvas = root
        self._sys_canvas = root
        if half is not None:
            rel = getattr(half, "release", None)
            if rel is not None:
                rel()
        p = self.pointer
        if p is not None:
            was = p.div if p.div > 0 else 1
            p.w, p.h, p.div = root.w, root.h, n
            p.x = min(p.x * was // n, root.w - 1)
            p.y = min(p.y * was // n, root.h - 1)
        _layout_base.place_class(self.metric_class, n)
        bl = getattr(self, "bar_layer", None)
        if bl is not None:
            bl.invalidate()
            bl.publish_strips()
        if getattr(self, "layout", None) is not None:
            self._relayout()
        drop = getattr(wm, "_drop_backdrop", None)
        if drop is not None:
            drop()
        if n == 1:
            glass.cls(0)
        self._dirty = True

    def _present_half(self):
        """The half-size surface, doubled onto the glass: the frame's one
        present at `render.scale: 2`."""
        half = self._half
        half.flush_batch()
        self.glass.blit565(half._buf, half.w, half.h, 0, 0, self.render_scale)

    def _cart_has_perm(self, name):
        """True iff the open cart's manifest permissions include `name` (#38).
        moy_carts.load() carries the manifest "permissions" list onto the cart;
        an embedded/legacy cart with none simply never matches, so it gets no
        gated APIs. One the run holds absent (runtime/shell/app_perms.py) is
        not held."""
        perms = self.cart.get("permissions") if self.cart else None
        return bool(perms) and name in perms and name not in self.player.absent

    def cart_editor_focus(self, handle):
        """A cart's editor handle takes (`handle`) or releases (None) the
        keyboard -- `editor_handle.EditorHandle.focus`.

        The flip is the shell's and not the cart's because `_set_text_mode`
        does two things no cart can: it puts the T-Deck keyboard into clean
        ASCII, and it seeds the typed-key edge with the byte already down, so
        the key that opened the note is not typed into it (`b2ff7de`)."""
        self.player.set_editor_focus(handle)
        self._set_text_mode(handle is not None)

    def take_text_request(self):
        """POP the `(kind, name, mode)` document the console was asked to open,
        or None. Read once, by the namespace build of the run that answers it,
        so a re-run never re-opens what the person has since navigated away
        from."""
        req = self._text_request
        self._text_request = None
        return req

    def open_text_cart(self, name, kind=None, mode=None):
        """RUN the text app on one document -- the Files router's door for
        anything textual (docs/theme_and_shell_2026-11.md §10).

        The text app is a CART, claimed by the `editor` marker permission
        (`system_api.is_text_app`), so this is an ordinary launch with the
        request parked for its namespace. False when this build carries no
        such cart, which the router reports on its status line."""
        cart = None
        for c in self.carts.all:
            if system_api.is_text_app(c):
                cart = c
                break
        if cart is None:
            return False
        self._text_request = (kind or _moy_app.kinds()[0],
                              str(name), mode)
        # Back to whoever asked, not home: Files opened this, so its X returns
        # to the shelf the person was standing on, and the Editor's Config tab
        # (step 5's Files) gets back the tab it left.
        caller = (self.editor_app if self.wm.top_is("menu")
                  else self._apps_by_id.get(self.wm.top_kind()))
        if not self._open_workspace(cart):
            self._text_request = None
            return False
        self.run(self.project, caller or self.launcher_layer)
        return True

    def open_project_file(self, cart, name, mode=None):
        """Open one of a PROJECT's own files in the text app -- the Config tab's
        Files, and the Files router's door for a project file reached
        from the PROJECTS root (step 5 of docs/theme_and_shell_2026-11.md §10).

        It goes through the EDITOR, not straight into the handle, and that is
        the whole point of the door: the project is opened first and returned to
        on exit, so leaving re-READS the folder. That is what keeps the loader in
        the loop over a file JSON mode is allowed to write invalid -- a manifest
        the kid just broke comes back with its banner on the Config tab instead
        of quietly leaving the shelf."""
        path = (cart or {}).get("path")
        if not path:
            return False
        if self.project is None or self.project.cart is not cart:
            self.open_in_editor(cart)
            if self.project is None or self.project.cart is not cart:
                return False
        self.editor_app.set_tab("cards")     # the tab the return lands back on
        if not self.open_text_cart(name, self.carts_store.project_kind(path),
                                   mode):
            return False
        self._project_return = cart
        return True

    def _return_to_project(self, cart):
        """Come back from a project-file edit to that project's Editor, THROUGH
        THE LOADER (`open_project_file`'s contract). Both WM tiers land here:
        the windowed one closes the playtest window first, exactly as the
        ordinary exit below does."""
        _cp = getattr(self.wm, "close_player", None)
        if _cp is not None and self.wm.desk_open():
            _cp()
        self.carts.reload(cart)
        self.open_in_editor(cart)
        if self.project is not None and self.project.cart is cart:
            self.editor_app.set_tab("cards")
        self._dirty = True

    def app_content_rect(self):
        """What the exitable "tool" strip leaves of a running app cart's
        surface, (x, y, w, h) -- what `content_rect()` reports to a USER APP
        so it lays out around the chrome instead of hardcoding 18."""
        return self.bar_layer.content_rect("tool")

    def app_bar_h(self):
        """`bar_h()`: the rows of `app_content_rect()` above the content."""
        return self.app_content_rect()[1]

    def place_bars(self, bars):
        """Put the host's bars on every surface and relayout the shell around
        them (layout_base.place_bars). The theme layout's resolver
        (`Appearance.apply_layout`) calls it."""
        _layout_base.place_bars(bars)
        self.bar_layer.invalidate()
        self._relayout()

    def place_bar(self, edge):
        """Today's one bar, moved to `edge` ("top" or "bottom")."""
        self.place_bars(((edge,) + _layout_base.DEFAULT_BARS[0][1:],))

    def set_pointer(self, pointer):
        """The console's pointer (a `moy_input.Pointer`, or None): what
        `ws.pointer` names and the app ABI's surface rows read, live. Every
        assignment comes through here, so `ctx.surface.pointer()` never reads a
        cursor the console dropped."""
        if pointer is not None and self._half is not None:
            pointer.w, pointer.h = self._half.w, self._half.h
            pointer.div = self.render_scale
        self.pointer = pointer
        self.app_abi.bind_pointer(pointer)

    def _surface_write(self, ox=0, oy=0):
        """Write what an app's surface shows NOW into every surface grant's row
        (native/moy_app): the system canvas, its size, the effective scales,
        the world's window flag, the content rect the host strip leaves a
        running app (`app_content_rect`) and the origin the pointer is offset
        by. Called wherever one of them changes -- a
        relayout, a layout context's install (wm_windowed), the canvas promote
        and degrade, a responsive app cart's canvas, a new grant -- so a row
        always answers what the shell would."""
        abi = getattr(self, "app_abi", None)
        if abi is None:
            return
        cv = self.sys_canvas
        look = self.look
        content = (self.app_content_rect()
                   if getattr(self, "bar_layer", None) is not None
                   else (0, 0, cv.w, cv.h))
        abi.surface_write(cv, cv.w, cv.h, look.effective_font_scale(),
                          look.effective_chrome_scale(), self.windowed_chrome,
                          content, ox, oy)

    def _relayout(self):
        """Rebuild the responsive layout from the live system-canvas size + the
        EFFECTIVE font scale and re-push it into the launcher (so its grid reflows).
        Called on a font-scale change (and could be called on a resize)."""
        self._surface_write()             # what the apps' relayouts read
        w, h, fs = self.sys_canvas.w, self.sys_canvas.h, self.look.effective_font_scale()
        cs = self.look.effective_chrome_scale()
        self.layout = Layout(w, h, fs, cs)
        self.launcher.set_layout(self.layout)
        self._relayout_code()             # editor layouts reflow too (#39 step 2)
        # The step-3 responsive editors (#39): each converted layer owns its layout;
        # an editor surface not built yet takes the live one when it is, and
        # cards_layer is guarded because _relayout is first called before
        # _build_layers registers it.
        for _lyr in ("block_ui", "paint_layer", "map_ui", "scene_ui", "music_ui"):
            _obj = self.built(_lyr)
            if _obj is not None:
                _obj.relayout(w, h, fs, cs)
        _obj = getattr(self, "cards_layer", None)
        if _obj is not None:
            _obj.relayout(w, h, fs, cs)
        for _app, _t in getattr(self, "_apps", ()):   # registered system apps
            _relay = getattr(_app, "relayout", None)
            if _relay is not None:
                _relay(w, h, fs, cs)
        self._surface_write()             # the new layout's bar
        _bl = getattr(self, "bar_layer", None)
        if _bl is not None:
            _bl.publish_strips()          # the bar strips' rects, on a rotated root
        # The windowed WM (wm_windowed.py, big-screen tier) re-anchors its layout
        # contexts after any relayout; a no-op hook on the fullscreen-stack WM.
        _hook = getattr(self.wm, "on_relayout", None) if hasattr(self, "wm") else None
        if _hook is not None:
            _hook()

    def _relayout_code(self):
        """Rebuild the Code tab's geometry and let an open editor adopt it live (so a
        font/size change reflows without losing the buffer). The ONE author of
        `code_layout` after __init__ -- _relayout above and EditorApp._relayout_tab
        (entering the tab, #216) both come through here.

        Adopting is gated on the LAYOUT's window actually changing, and compares
        LAYOUTS, not the editor's live window, because the line-number gutter
        narrows that window itself (code_layer._apply_gutter). A real reflow
        shows the caret again; re-deriving the same layout, and the desk's panes
        setting theirs each frame (`set_view_size` alone), keep the view where
        it was scrolled to."""
        prev = getattr(self, "code_layout", None)
        lay = CodeLayout(self.sys_canvas.w, self.sys_canvas.h,
                         self.look.effective_font_scale(),
                         self.look.effective_chrome_scale(),
                         symbols=_ui_draw.input_kind() == "touch")
        self.code_layout = lay
        ed = self.editor
        if ed is not None and (prev is None
                               or (prev.cols, prev.rows) != (lay.cols, lay.rows)):
            ed.set_view_size(lay.cols, lay.rows)
            ed._scroll()


    # -- WEB CONSOLE (#197): forwards to the `web` collaborator ---------------
    #
    # The switch itself lives in web_console.py; these keep the names its
    # callers already speak. Fixed signatures, one delegation each (#209).

    def web_pin(self):
        """`web.pin()` -- moy_webhost's start-time lambda + the tests."""
        return self.web.pin()

    def web_console_url(self):
        """`web.url()` -- the dev channel's `web` and the tests."""
        return self.web.url()

    def saver_state(self, on):
        """The kernel's ladder entered or left its SAVER rung. Never over a
        running cart: the game keeps the glass."""
        if on and not self.wm.top_is_player():
            try:
                from idle_ladder import Saver
            except ImportError:  # host: the runtime package
                from runtime.idle_ladder import Saver
            self._saver = Saver(self)
        else:
            self._saver = None
            self._dirty = True

    def park_web_console(self):
        """`web.park()` -- the layers.py contract comment and the tests."""
        return self.web.park()

    def stop_web_console(self):
        """`web.stop()` -- the Guition on-glass suite's serial vocabulary."""
        return self.web.stop()

    def webhost_serving(self):
        """`web.serving()` -- settings_layer, tools/push_cart_wifi.py."""
        return self.web.serving()

    def webhost_label(self):
        """`web.label()` -- the Settings row, tools/push_cart_wifi.py."""
        return self.web.label()

    def toggle_webhost(self):
        """`web.toggle()` -- settings_layer, the dev channel, push_cart_wifi."""
        return self.web.toggle()

    def rescan_carts(self):
        """`carts.rescan()` -- moy_webhost captures this name in its `on_sync`
        lambda at construction, and the Guition on-glass suite's sync test
        depends on the shelf following a browser batch with no reboot."""
        return self.carts.rescan()

    # `updater` is a PROPERTY so that injecting one INVALIDATES the two cached
    # availability answers below. Four places inject an updater -- three boards
    # at boot and web_boot's update_enable -- and that last one binds LATE, from
    # the worker, once the /update probe has answered. Anything that asked
    # "is there an updater" before that moment cached False for the whole
    # session, and the Settings rows it gates could then never appear no matter
    # what bound afterwards. A setter cannot be forgotten by the next injector;
    # a `remember to clear the cache` comment can.
    @property
    def updater(self):
        return self._updater

    @updater.setter
    def updater(self, u):
        self._updater = u
        self._updater_ok = None
        self._online_ok = None

    def _update_available(self):
        """True when an OTA updater is injected AND this build is OTA-capable (the
        running app is ota_0/ota_1, not a legacy single-`factory` image). Cached: the
        answer is fixed for a boot, and the check reads a partition (cheap, no SD)."""
        if self._updater_ok is None:
            u = self.updater
            try:
                self._updater_ok = bool(u is not None and u.available())
            except Exception:
                self._updater_ok = False
        return self._updater_ok

    def _online_update_available(self):
        """True when the updater can also fetch firmware over WiFi (#53 Phase 3):
        OTA-capable build + an injected wifi service. Cached like _update_available."""
        if self._online_ok is None:
            u = self.updater
            try:
                self._online_ok = bool(u is not None and u.online_available())
            except Exception:
                self._online_ok = False
        return self._online_ok

    def open_settings(self):
        if self.wm.bring_forward("settings"):
            return                     # the desk's Settings window, as it was
        if not self.wm.top_is("settings"):        # Stage 6d: ask the stack top
            self._settings_return = self.wm.top_kind()  # resume here on exit (cart vs home)
        self._dirty = True             # screen change repaints (#44)
        self.settings_layer.reset()    # reset the selection + scroll window (#53)
        self.wm.goto("settings")       # Stage 6e: push Settings onto the back-stack
        self.ach_ui._secret_taps = 0              # fresh secret-door run each visit (#21)
        self._set_text_mode(False)

    def open_log(self, pid=None):
        """Settings' System log page (runtime/system/log_ui.py), on one
        process's records where `pid` names it: what a crash's "details"
        open."""
        self.open_settings()
        self.settings_layer.open_page("log", pid)

    def open_tasks(self):
        """The task manager (runtime/system/tasks_ui.py): from the system
        menu and a held console key. On a handheld it goes on top, holding
        a front game while it shows; on the desk it is a window. Opened while
        a notification's banner is up, it takes the banner down and opens on
        the Notifications tab with that one current."""
        self.close_popover()
        if self.pause.open:
            self.pause.close()
        if self.confirm.open:
            self.confirm.cancel()
        self._dirty = True
        layer = self.tasks_layer
        note = _NOTES.up()
        if note is not None:
            _NOTES.dismiss()
        if self.wm.top_is("tasks"):
            if note is not None:
                layer.open(note)
            return
        if self.wm.bring_forward("tasks"):
            if note is not None:
                layer.open(note)
        else:
            layer.open(note)
            pl = self.player
            layer.held = False
            if self.procs.handheld():
                self._tasks_return = self.wm.top_kind()
                if self.wm.top_is_player() and not pl.paused \
                        and self.cart_error is None:
                    pl.pause()
                    layer.held = True
            self.wm.activate("tasks")
        self._set_text_mode(False)

    def close_tasks(self, home=False):
        """The task manager goes: back where it was opened from (a held
        game resumed), or `home`."""
        self._dirty = True
        layer = self.tasks_layer
        _ck = getattr(self.wm, "close_window_kind", None)
        if _ck is not None and self.wm.desk_open():
            _ck("tasks")
            if home:
                self.home_key()
            return
        back = self._tasks_return
        self._tasks_return = None
        if layer.held:
            layer.held = False
            if back == "desktop" and not home:
                self.player.resume()
        if home or back is None or back == "tasks" or not self.wm.stack.has(back):
            if self.wm.stack.has("desktop") and self.procs._detachable():
                self.procs.detach()     # the run that played joins the running set
            self.go_home()
            return
        self.wm.goto(back)

    def tasks_switch(self, key):
        """The task manager's Switch to: run `key` (a Proc's, or the front
        run's) shown and given the keys."""
        procs = self.procs
        p = procs.by_key.get(key)
        if procs.handheld():
            layer = self.tasks_layer
            if p is None:
                self.close_tasks()
                return
            if layer.held:
                layer.held = False
                self.player.resume()
            self._tasks_return = None
            procs.switch(p)
            return
        if key == "desktop":
            self.wm.bring_forward("desktop")
        elif p is not None:
            self.wm._bring_forward(key)
        self._dirty = True

    def tasks_close(self, key):
        """The task manager's Close on run `key`, the task manager staying."""
        procs = self.procs
        p = procs.by_key.get(key)
        if p is not None:
            procs.close(p)
            return
        if key != "desktop":
            return
        if procs.handheld():
            self.tasks_layer.held = False
            self.player.release_world()
            self.pause.dismiss()
            if self.wm.stack.remove("desktop"):
                self.wm._on_nav()
            if self._tasks_return == "desktop":
                self._tasks_return = self.wm.root_kind()
            procs.gen += 1
            return
        _ck = getattr(self.wm, "close_window_kind", None)
        if _ck is not None:
            _ck("desktop")

    def _exit_settings(self):
        # Windowed WM (#73): close JUST the Settings window -- whatever else is
        # open (a running game, the Make window) stays. Desktop mode only:
        # console-mode Settings is fullscreen and takes the resume-or-home rules.
        self.settings_layer.commit_name()     # a Name still being typed is kept
        if self.settings_layer.wifi_view:
            self.settings_layer.close_wifi()   # its radio lease ends with the visit
        _ck = getattr(self.wm, "close_window_kind", None)
        if _ck is not None and self.wm.desk_open():
            self._dirty = True
            _ck("settings")
            return
        # Close Settings back to wherever it was opened from: resume the running cart
        # if we came from one (the gear on the in-cart/crash bar), else the launcher home.
        back = self.settings_layer.wifi_return
        self.settings_layer.wifi_return = None
        if back is not None and back in self._apps_by_id:
            self.wm.goto(back)         # the app that opened the Wi-Fi page, as it was
            self._dirty = True
        elif getattr(self, "_settings_return", "launcher") == "desktop" and self.cart is not None:
            # Resume the running cart WITHOUT clobbering the run's caller (the Stage-3
            # review fix): Settings was opened OVER a cart the Editor may have launched via
            # PLAY, so the cart's exit must still return to that caller (the Editor tab),
            # not be reset to the launcher here. run() would overwrite it; popping
            # Settings off the stack reveals the cart below + preserves who it pops to.
            self.wm.goto("desktop")    # Stage 6e: pop Settings, resume the cart below
            self._dirty = True
        else:
            self.go_home()

    # -- the input in use (the hints' keys) ---------------------------------

    def use_inputs(self, kinds):
        """The board's inputs: `kinds` pairs an input source's name (the
        input table's -- a board's "kbd", "touch" and "ble", the host's
        "local") with the input it is to the hints (`ui_draw.INPUTS`), and the
        first pair's is the input in use until another one is used. Every
        hint strip draws the keys of the last input with keys used
        (`ui_draw.hint_kind`): a touch after keys leaves them up, and a board
        whose first input is touch shows none until keys are used. A board
        that declares none (None) leaves them on the desk keyboard's."""
        self.input_kinds = None if kinds is None else dict(kinds)
        self._news = None if kinds is None else getattr(self.input, "news", None)
        if kinds:
            _ui_draw.input_kind(kinds[0][1])
            self._dirty = True

    def input_used(self, source):
        """Input source `source` was used: the input in use becomes its kind
        (a source the board did not declare changes nothing), and a change
        repaints. A key's input is the hints' too; a touch keeps theirs, and
        takes the keys off the bar and the home's tab row, as it shows no
        focus: the strips then name the surface's own keys."""
        kinds = self.input_kinds
        kind = kinds.get(source) if kinds is not None else None
        if kind is not None and kind != _ui_draw.input_kind():
            keys = _ui_draw.hint_kind()
            _ui_draw.input_kind(kind)
            if kind == "touch":
                _ui_draw.hint_kind(keys)
                if self.bar_layer.focus is not None:
                    self.bar_layer.focus_leave()
                self.launcher_layer._tab_focus = False
            self._dirty = True

    # -- top-bar system menu (#52) -------------------------------------------
    #
    # The ≡ dropdown built on the reusable Popup primitive. Contents are rebuilt each
    # open from the live state: a SYSTEM group always (Settings / About / Reboot), and
    # -- only when a cart is open -- a CART group PREPENDED (Restart / Delete). The
    # actions wire to the existing console flows (open_settings, apply = re-run, the SD
    # delete path). Selecting any row closes the menu (Popup.activate closes first).

    def _modal_layer(self):
        """The ids of the layers that draw the menu holding the keys over
        the screen, the pause menu's, the delete confirm's or the system
        menu's (on the desk a popover frame paints it from its own layer),
        or None."""
        if self.pause.open and not self.sysmenu.open:
            return _PAUSE_LAYERS
        if self.confirm.open:
            return _CONFIRM_LAYERS
        if self.sysmenu.open:
            return _SYSMENU_LAYERS
        return None

    def system_key(self):
        """Super -- the `start` button (a keyboard's GUI key), the host's
        Super, the browser's Meta tap -- from every surface: over a fullscreen
        game it opens the pause menu, anywhere else the system menu; pressed
        again it closes the one it opened (Esc closes either and steps back).
        Over the delete confirm it is Cancel."""
        p = self.pause
        m = self.sysmenu
        if self.confirm.open:
            self.confirm.cancel()         # the console's key answers Cancel
        elif p.open:
            p.close()
        elif m.open and m.widget == "menu":
            m.close()
            self.popover_touch()
        elif self.plays_fullscreen() or self._game_pauses():
            p.show()
        else:
            self.toggle_sysmenu()
        self._dirty = True

    def full_key(self):
        """F11 -- the `full` button of a keyboard, the host's and the page's
        F11: the focused desk window full screen, and back
        (`WindowedWM.full_key`)."""
        if self.wm.full_key():
            self._dirty = True

    def plays_fullscreen(self):
        """A game runs fullscreen, live: what Super pauses -- in console
        mode, or full screen on the desk (`WindowedWM.game_full`)."""
        return (self.wm.top_is_player() and self.cart_error is None
                and not self.player._is_tool
                and (not self.wm.desk_open() or self.wm.game_full()))

    def _game_pauses(self):
        """The desk's other play Super pauses: the game docked in the
        Editor's Run slot, holding the keys (`WindowedWM.game_pauses`)."""
        gp = getattr(self.wm, "game_pauses", None)
        return (gp is not None and self.wm.top_is_player() and self.cart_error is None
                and not self.player._is_tool and gp())

    def toggle_sysmenu(self):
        """≡ tapped (or its keyboard shortcut): open the dropdown if closed, close it
        if open. Rebuilds the item list so the cart group reflects the current state.
        The rows + their action callbacks live on self.menu_ui
        (console_notices.SystemMenuUI); this stays here as the tested ws. entry point."""
        m = self.sysmenu
        m.fs = self.look.effective_font_scale()   # rows hold fs-scaled text
        m.cs = self.layout.cs                     # ...and cs-scaled rows (#203)
        if m.open and m.widget == "menu":
            m.close()
        else:
            m.w = 0
            self._place_popover("menu", _POPUP_W * m.fs, 0)
            m.show(self.menu_ui._sysmenu_items())
        self.popover_touch()

    def _place_popover(self, widget, pw, ph):
        """Anchor the popover at `widget`'s cell, opening into the content side
        of its bar: down from a top bar, up from a bottom one, inward from a
        side bar. Across a horizontal bar the panel right-aligns to the cell
        so it hangs toward the glass's middle; every anchor is clamped to the
        canvas, a `ph`-tall panel kept on it; a component's card keeps the
        popover's gap off its bar and the glass's edges, and on a handheld
        hangs at the bar's end. The popover draws on the SYSTEM canvas, so it
        anchors to the responsive Layout's packing."""
        m = self.sysmenu
        bars = self.layout.bars
        got = _shell_layout.cell_of(bars, widget)
        i, (bx, by, bw, bh) = got if got is not None else (0, self.layout.zone_left)
        edge, (rx, ry, rw, rh) = bars[i][0], bars[i][1]
        cw, ch = self.sys_canvas.w, self.sys_canvas.h
        e = 0 if widget == "menu" else (8 if self.metric_class == _layout_base.DESK
                                        else 4 * self.layout.fs)
        if edge == "left":
            ax = rx + rw + e
        elif edge == "right":
            ax = rx - pw - e
        elif self.metric_class == _layout_base.DESK or widget == "menu":
            ax = rx + bx + bw - pw
        else:
            ax = rx + rw - pw - e
        m.anchor_x = max(e, min(ax, cw - pw - e))
        m.up = edge == "bottom"
        if edge == "top":
            m.anchor_y = ry + rh + e
        elif edge == "bottom":
            m.anchor_y = ry - e
        else:
            m.anchor_y = ry + by
            if ph:
                m.anchor_y = max(e, min(m.anchor_y, ch - ph - e))

    def toggle_popover(self, widget):
        """A bar widget's popover: open its component anchored
        at the widget's cell -- one at a time, so another one closes -- or, on
        the anchor tapped again, close it."""
        m = self.sysmenu
        if m.open and m.widget == widget:
            m.close()
            self.popover_touch()
            return
        comp = _component_for(self, widget)
        if comp is None:
            return
        m.fs = self.look.effective_font_scale()
        m.cs = self.layout.cs
        opened = getattr(comp, "opened", None)
        if opened is not None:
            opened()                  # first: its size counts what it found (a scan)
        w, h = comp.size()
        desk = self.metric_class == _layout_base.DESK
        h += 2 if desk else 2 * self.layout.fs   # the card's drop
        w = min(max(w, _POPUP_W * m.fs), self.sys_canvas.w)
        h = min(h, self.sys_canvas.h)
        self._place_popover(widget, w, h)
        m.show_component(comp, widget, (w, h))
        self.popover_touch()

    def close_popover(self):
        """Close the popover if one is open: its component's `closed` runs,
        so the Wi-Fi card's radio lease goes with it. Every surface switch
        calls it -- a switch made under an open popover lands under it."""
        if self.sysmenu.open:
            self.sysmenu.close()
            self.popover_touch()

    def popover_touch(self):
        """The popover's pixels changed (opened, navigated, closed): the WM
        paints its rect alone where it can, else the frame repaints. A
        pointer press this frame was the popover's (`_press_owed`)."""
        self.wm.popover_changed()
        self._press_taken = True

    def pause_touch(self):
        """The pause menu's pixels changed (opened, a row moved, its page,
        closed): the WM paints its card alone where it can, else the frame
        repaints. A pointer press this frame was the menu's."""
        self.wm.pause_changed()
        self._press_taken = True

    def widget_tap(self, widget):
        """A bar widget's tap (docs/theme_and_shell_2026-11.md §8.6): the system
        menu is the popup it is; another widget opens its component in a
        popover hung from it, in both modes (docs/history/look_2026-10.md step 33)."""
        if widget == "menu":
            self.toggle_sysmenu()
        else:
            self.toggle_popover(widget)

    def open_wifi_panel(self):
        """An app's way to the Settings WIFI panel, the one wifi
        implementation (the `wifi` service): in desktop mode as the popover,
        in console mode as the Settings page opened at it. The bar's wifi
        widget opens the popover in both modes (`widget_tap`)."""
        if self.wm.desk_open():
            if not (self.sysmenu.open and self.sysmenu.widget == "wifi"):
                self.toggle_popover("wifi")
            return
        self.open_settings()
        self.settings_layer.open_wifi()

    def battery_level(self):
        """The battery's charge in percent, or None where the board reads none
        (no `battery` gauge: the glyph is the widget's whole value)."""
        return None if self.battery is None else self.battery.level()

    def battery_charging(self):
        """Whether a charger feeds the battery, or None where the board's
        gauge cannot tell (the T-Deck's has no charge-status line)."""
        b = self.battery
        f = getattr(b, "charging", None) if b is not None else None
        return None if f is None else f()

    # -- firmware update screen (#53) -----------------------------------------
    #
    # OTA flow: Settings -> UPDATE FW finds a .bin on /sd/update, the kid confirms,
    # and the injected updater flashes it to the INACTIVE OTA slot one chunk per frame
    # (so the progress bar animates through the normal frame/flush loop), then reboots
    # into the new image. The running slot is never touched, and the bootloader rolls
    # back if the new app doesn't confirm itself healthy -- so a bad/aborted update is
    # safe. Pure UI here; all SD + flash work lives in the device-only updater backend.

    # The update SCREEN (open_update / open_update_online / _start_download /
    # _exit_update / _confirm_update / _update_input / _update_pointer /
    # _pump_update / _draw_update / _draw_progress_bar) now lives on
    # self.update_ui (update_ui.py, UpdateUI). The update queries +
    # channel config (_update_available/_online_update_available/_ota_channel/
    # _cycle_channel) stay here.

    def _start(self):
        # The cart-run body moved to Player.start (Stage 2, player.py); this stays as
        # the tested ws. entry point (tools + apply/run_code/_leave_menu/open call it)
        # -- run() is the caller-recording wrapper around it (see below).
        if self.covers.diet:
            self.covers.diet_release()     # RAM-tight tier: the shelf's caches are
                                           # dead weight (and GC-pause fuel) mid-play
        return self.player.start(self.project)

    # -- open-cart workspace forwards (Stage 1, project.py) -------------------
    #
    # The open cart's six live-data fields live on self.project now; these forwarding
    # properties delegate reads AND writes to it, so every surface file + test that
    # reads/writes ws.cart/ws.config/ws.sheet/ws.tilemap/ws.images/ws.pmem is byte-for-
    # byte unchanged. A getter that returns the live object covers in-place mutation
    # (ws.cart["src"] = ...); the setter covers assignment (ws.cart = ..., ws.sheet =
    # self._build_sheet()). The plan's §1.2 seam: Project keeps a ws back-reference for
    # the toolkit/deps; only its DATA moves off ws here.
    @property
    def cart(self):
        return self.project.cart

    @cart.setter
    def cart(self, value):
        self.project.cart = value

    @property
    def config(self):
        return self.project.config

    @config.setter
    def config(self, value):
        self.project.config = value

    @property
    def sheet(self):
        return self.project.sheet

    @sheet.setter
    def sheet(self, value):
        self.project.sheet = value

    @property
    def tilemap(self):
        return self.project.tilemap

    @tilemap.setter
    def tilemap(self, value):
        self.project.tilemap = value

    @property
    def images(self):
        return self.project.images

    @images.setter
    def images(self, value):
        self.project.images = value

    @property
    def pmem(self):
        return self.project.pmem

    @pmem.setter
    def pmem(self, value):
        self.project.pmem = value

    @property
    def scenes(self):                     # #85: the open cart's placed-actor scenes
        return self.project.scenes

    @scenes.setter
    def scenes(self, value):
        self.project.scenes = value

    # -- cart-run forwards (Stage 2, player.py) ------------------------------
    #
    # The Player owns the running cart's live state now; these forwarding properties
    # delegate reads AND writes to it, so every surface file + test that touches
    # ws.cart_error / ws.crash_line / ws.ns / ws._update / ws._draw / ws._cart_key_prev
    # is byte-for-byte unchanged (the exact mirror of the Stage-1 project.* forwards
    # above). (_cart_start_ms + the Stage-5 exit-gesture state have no external reader --
    # they stay private on the Player, no forward. cart_paused was retired in Stage 5.)
    @property
    def cart_error(self):
        return self.player.cart_error

    @cart_error.setter
    def cart_error(self, value):
        self.player.cart_error = value

    @property
    def crash_line(self):
        return self.player.crash_line

    @crash_line.setter
    def crash_line(self, value):
        self.player.crash_line = value

    @property
    def ns(self):
        return self.player.ns

    @ns.setter
    def ns(self, value):
        self.player.ns = value

    @property
    def _update(self):
        return self.player._update

    @_update.setter
    def _update(self, value):
        self.player._update = value

    @property
    def _draw(self):
        return self.player._draw

    @_draw.setter
    def _draw(self, value):
        self.player._draw = value

    @property
    def _cart_key_prev(self):
        return self.player._cart_key_prev

    @_cart_key_prev.setter
    def _cart_key_prev(self, value):
        self.player._cart_key_prev = value

    # -- active-tab forward (Stage 3, editor_app.py) --------------------------
    #
    # The active editor view lives on self.editor_app.tab now; menu_view is a
    # forwarding PROJECTION of it (read AND write, the same shim ws.sheet got in
    # Stage 1), so the string-keyed router keeps routing on ws.menu_view unchanged and
    # every writer (open()/set_menu_view set the cart tabs, open_theme the theme mode's)
    # keeps working. EditorApp.tab is the source of truth; menu_view stays a faithful
    # tested-surface projection of it (like ws.screen over the WM back-stack -- plan
    # Section 6 keeps these shims as tested surface, the future OS-arch capability track
    # is what finally removes them; the router still consults it as the "menu" tab key).
    @property
    def menu_view(self):
        return self.editor_app.tab

    @menu_view.setter
    def menu_view(self, value):
        self.editor_app.tab = value

    # -- screen projection over the WM back-stack (Stage 6b, wm.py) ------------
    #
    # `screen` is now a PROJECTION of the WM process back-stack top (self.wm.top_kind()),
    # not a plain attribute -- the back-stack is the state of record (plan Section 6). The
    # getter reads the stack top; the setter routes a `screen = X` write into the stack
    # via wm.goto (a RETURN to an already-open screen pops back to it, a new screen is
    # pushed), the same read-write projection shim ws.menu_view is over editor_app.tab.
    # This keeps the single string-keyed router dispatching unchanged (_content_layer
    # still keys on ws.screen/ws.menu_view) -- the stack is a data structure it READS, not
    # a second dispatcher. Production readers migrate to wm queries over the next commits
    # (Stage 6d); the projection stays as tested surface (many tests assert ws.screen).
    @property
    def screen(self):
        return self.wm.top_kind()

    @screen.setter
    def screen(self, value):
        self.wm.goto(value)

    # -- run / exit (Stage 2: the run/return stack discipline) ----------------

    def defer(self, fn, toast=True, paints=True):
        """#184: schedule a heavy transition (cart start, editor open, PLAY)
        instead of running it inside the pointer walk. The tap frame paints its
        acknowledgment (selection highlight + the LOADING toast) and PRESENTS
        it (comp.flush() runs inside frame()); the queued `fn` then runs at
        that same frame's tail -- so the 1-2s a cart start costs happens
        behind a frame that already shows the tap landed, not behind a frozen
        stale shelf. defer() marks dirty so the acknowledgment frame always
        paints (the redraw gate can't skip it).

        `toast=False` queues work the kid is NOT waiting on -- the Editor's
        owed commit (#154), which rides the same tail because the frame has
        already painted the destination. It gets the same drain and the same
        DEFER line, and no pill: SAVE IS INVISIBLE (spec Section 7), and a
        'LOADING...' over a tab that has already finished switching would
        announce the one thing the shell promises never to show.

        `paints=False` is work whose result shows nothing (that same commit,
        which marks dirty itself when it writes): the drain then owes no
        frame for it, where a transition's result must paint."""
        self._deferred.append((fn, bool(toast), bool(paints)))
        self._dirty = True             # the acknowledgment frame must paint

    def _toast_owed(self):
        """Does a queued transition want the LOADING pill? A loop, because a
        generator expression is three heap objects and frame() asks on every
        painted frame."""
        for _fn, toast, _paints in self._deferred:
            if toast:
                return True
        return False

    def _run_deferred(self):
        """Run the deferred transitions queued BEFORE this drain started
        (frame()'s tail, after the flush presented the acknowledgment). A
        transition that defers another action leaves it for the next frame --
        its own result must paint first."""
        q = self._deferred
        paint = False
        for _ in range(len(q)):
            fn, _toast, paints = q.pop(0)
            paint = paint or paints
            _t0 = _ticks_ms() if self.perf_capture else 0
            fn()
            if self.perf_capture:
                print("DEFER ms=%d fn=%s"
                      % (_ticks_diff(_ticks_ms(), _t0),
                         getattr(fn, "__name__", "?")))
        if paint and not self._dirty:
            self._dirty = True         # the transition's result must paint

    def _crash_to_code(self):
        """The stop card's See why (docs/history/look_2026-10.md step 33): the code
        editor on the line that stopped the run, the popover saying what
        stopped it hung under the line (the crash-to-code throw,
        now a row of the card). Returns True when it navigated; False (no
        open project/cart -- nothing to edit; a compiled cart) keeps the
        card. The dead run's world is released exactly like a normal exit;
        cart_error/crash_line survive it (the editor's inline marker + the
        popover read them). A parked console (#197) goes home instead."""
        if self.project is None or self.cart is None:
            return False
        # #197: while WASM MODE is on, the glass is PARKED and the browser owns
        # authoring. A cart played by PLAY ON DEVICE that crashes must return to
        # the connection screen, NOT open the Editor on the glass -- an editor
        # here would edit the same store the browser is syncing, which is exactly
        # the two-writer case the parked switch is designed to prevent. go_home
        # releases the dead run and re-parks (its own tail lists "a crash" as one
        # of the doors it funnels).
        if self.web.parked:
            self.cart_error = None
            self.crash_line = None
            self.go_home()
            return True
        # A compiled cart has no source to open: its trap stays on the stop
        # card, which offers no See why (native/moy_wasm/README.md).
        if self.cart.get("runtime") == "wasm":
            return False
        err = self.cart_error or "crashed"
        line = self.crash_line
        # THE FILE THAT RAISED, not the file the tab was left on (SPEC.md 4,
        # #89). On a ported cart the raise is as likely to be in p8.lua or one
        # of the PICO-8 tabs as in main.lua, and throwing the kid at main.lua's
        # line N is throwing them at somebody else's line N.
        # ...read straight off the player: the legacy ws-> player forwards are a
        # set that may only shrink (tests/test_console_facade.py), and this is a
        # new name with two readers.
        self.code_file = self.player.crash_file or None
        self.player.release_world()
        # Windowed desk world (#73/#105): the crashed playtest window closes
        # like a normal exit (never truncating windows stacked above it);
        # the fullscreen tiers just re-route the back-stack below.
        _cp = getattr(self.wm, "close_player", None)
        if _cp is not None and self.wm.desk_open():
            _cp()
        self.editor_app.open(self.project)
        self.editor = None            # rebuild over the file that raised
        self.set_menu_view("code")
        if line is not None:
            # Land the caret on the line that raised (set_menu_view only marks
            # it when it BUILDS the editor -- an editor kept open across the
            # PLAY needs the explicit jump).
            self._mark_code_error(line - 1, err[:32])
        self.crash_popup = err
        self._dirty = True
        return True

    def _running_cart_shows_bar(self):
        """True while a TOOL/APP cart is PLAYING (screen "desktop", not crashed): it runs
        WITH a minimal bar (back + title + status) so it's EXITABLE (Part 4), unlike a
        GAME which owns the full 320x240 with NO chrome and is left through the pause menu.
        This is the bar-visibility-by-type rule: game -> hide the bar; tool/app -> show it.
        The Player reads it to decide whether to draw/route the tool bar. False for
        games, the crash frame (the crash bar handles that), and every non-play screen.

        A GAME that reads text (textmode(True), e.g. the typing game Letter Blitz) does NOT
        get this bar -- forcing it over a game was rejected as a hack (it overlays the game's
        own top strip). The console's key (`start`) reaches it all the same and opens the
        pause menu, whose Quit leaves; a cart may also call quit() (make_api) from a
        key/affordance it draws."""
        return (self.wm.top_is_player() and self.cart_error is None  # Stage 6d
                and self.cart is not None
                and self.cart.get("type") in BAR_TYPES)

    def _draw_tool_bar(self):
        """Draw the minimal TOOL bar over a running tool/app (Part 4). Shell-owned;
        the Player asks for it via this thin helper so
        player.py never reaches the bar surface directly. Where the bar is the
        compact band on the glass (`BarLayer.tool_band`) the frame draws it
        after the composite instead (`_draw_tool_band`)."""
        if self.bar_layer.tool_band() is None:
            self.bar_layer._draw_status_strip("tool")   # minimal bar: title + status + X

    def _draw_tool_band(self):
        """A running tool cart's compact bar, on the system canvas over the
        composited game's top rows: True where it drew."""
        p = self.player
        if not p._is_tool or p.cart_error is not None or self.app_window_canvas \
                or not self.wm.top_is_player() or self.bar_layer.tool_band() is None:
            return False
        self.bar_layer._draw_status_strip("tool")
        return True

    def _tool_bar_tap(self, px, py, sx=None, sy=None):
        """Route a running-TOOL tap (px, py in GAME coords, (sx, sy) on the
        system canvas) to the minimal bar: the bar's back exits the tool, the
        Moy tile opens the system menu, the clock its calendar. Returns True
        iff the bar consumed the tap (so the tool underneath doesn't also act
        on it). Reuses handle_bar_tap("tool") -- owner is None for "tool", so
        a non-button tap falls through to the tool. The compact band
        (`BarLayer.tool_band`) is on the system canvas, so it takes (sx, sy)."""
        if sx is not None and self.bar_layer.tool_band() is not None:
            px, py = sx, sy
        return self.bar_layer.handle_bar_tap("tool", px, py)

    def _open_workspace(self, cart=None):
        # Build a fresh Project for `cart` (default: the launcher selection) + start it,
        # shared by open() [RUN, from a launcher tap, uses the launcher selection] and
        # open_in_editor() [EDIT, from the project-picker, which passes the PICKED cart].
        # Leaves the cart STARTED so PLAY can run it and the editors have live data.
        # Returns True iff the workspace opened; False leaves ws.cart None with
        # cart_error set (see the rehydrate guard below).
        # Deferred pmem (#66): persist the OUTGOING project's unsaved cells before
        # the fresh Project replaces it -- a re-open otherwise reloads pmem.json
        # over progress that only ever reached RAM.
        _old = getattr(self, "project", None)
        if _old is not None and _old.pmem is not None:
            try:
                _old.pmem.flush()
            except Exception:  # noqa: BLE001
                pass
        # (#111) autosave-only: a workspace swap is an exit path too -- hard-commit
        # the OUTGOING project's active Editor tab (code/sprites/map/scene/sounds/
        # config) before it's replaced below, exactly like the removed SAVE icon
        # used to on a tap. Only meaningful when the Editor was actually open on
        # `_old` (editor_app.project is the SAME object -- a plain RUN from the
        # launcher never opened the Editor, so this is a no-op there). Reset
        # editor_app.tab to a sentinel afterward so the NEW project's landing tab
        # switch (EditorApp.open -> set_tab, below/in open_in_editor) doesn't see a
        # stale tab name and re-commit the fresh (already-current) workspace it
        # just built -- save_current() no-ops on an unrecognized tab.
        _editor_app = getattr(self, "editor_app", None)
        if _editor_app is not None and _old is not None and _editor_app.project is _old:
            _editor_app.save_current()
            _editor_app.tab = None
        self.project = Project(self)   # a fresh workspace for the cart being opened
        if cart is None:
            cart = self.launcher.selected()
        # INVARIANT: a workspace only ever opens a REAL cart. The "+ New" pseudo card
        # is dispatched to new_cart_and_edit (launch_selected), never run -- but guard
        # the RUN/EDIT path anyway so a stray open() on it resolves to the first real
        # cart instead of crashing on a non-cart (path/cfg-less) tile.
        if cart is None or cart.get("path") is None:
            cart = next((c for c in self.launcher.items if c.get("path")), cart)
        prev = getattr(self, "_fat_cart", None)
        if prev is not None and prev is not cart:
            self.carts.reslim(prev)            # at most ~one fat cart stays live (#66)
        self.carts.rehydrate(cart)
        # FAIL CLOSED (#66): a cart whose SOURCE did not come back is not
        # openable, and the refusal belongs here -- everything below assumes
        # cart["src"], so a slim dict that got this far reached Player.start and
        # died on a KeyError two frames later, leaving ws.cart on the half-loaded
        # dict. rehydrate() leaves a cart slim when the store said no: a missing
        # folder, or (the case this was written from) a heap with no block big
        # enough for the source. The kid gets the panel a crashing cart gets --
        # open()/play() run() straight to it -- and the launcher stays usable.
        if cart is None or "src" not in cart:
            self._fat_cart = None
            self.player.release_world()    # the outgoing run's world is dead too,
                                           # and this is the heap that had no room
            self.cart_error = ("Couldn't open %.24s. Try again."
                               % ((cart or {}).get("title") or "that cart"))
            self.crash_line = None
            return False
        self._fat_cart = cart
        self.cart = cart
        self.config = dict(self.cart["cfg"])
        self.cards_layer.reset()      # no file list or dialog carried to the new cart
        self.editor = None
        self.code_file = None         # a new cart opens on ITS main file, never on
                                      # the name the last one was left showing
        self.paint = None
        for _ui in ("map_ui", "scene_ui", "music_ui", "block_ui"):
            _ui = self.built(_ui)        # one never built is fresh already
            if _ui is not None:
                _ui.reset()
        self.cart_error = None
        self.sheet = self._build_sheet()
        self.tilemap = self._build_tilemap()
        # No ws.flags projection: the legacy property-forward set can only
        # shrink (tests/test_console_facade.py), so the one reader -- the
        # Player, building the cart namespace -- goes through self.project.
        self.project.flags = self._build_flags()
        self.images = self.cart.get("images") or {}   # paint-image assets (#63)
        self.pmem = self._build_pmem()
        self.scenes = self._build_scenes()             # placed-actor scenes (#85)
        self._cart_key_prev = 0       # fresh cart: no stale key edge
        self.input.text_mode = False  # a fresh cart starts in game mode (#38/#42);
                                      # it opts into text input via textmode(True)
        self.input.cart_quit = False  # clear any prior cart's quit() flag so it can't
                                      # immediately pop the freshly opened cart (make_api)
        self.menu_view = "cards"
        self._set_text_mode(False)
        self._start()
        # Achievements (#21): opening a cart is "First Steps"; opening _PLAY_GOAL
        # distinct carts is "Cart Explorer". Key by the cart's path/title so it's
        # the SAME identity the launcher uses (distinct carts, not repeat opens).
        self.ach.note("open", self.cart.get("path") or self.cart.get("title"))
        return True

    def app_context(self, app_id, needs=(), prefs_ns=None, run=False, kind=None):
        """Build the narrowed shell interface for one system app
        (docs/app_api_v1.md, runtime/shell/app_context.py).

        `needs` is the app class's own `NEEDS` tuple, and `AppContext` attaches
        ONLY those roles -- so an app reaching for something it did not declare
        raises here rather than growing an invisible dependency. That filter is
        the whole point: Phase 7's `make_system_api(ctx, cart)` is this same
        call with a cart's manifest permissions in place of a class constant,
        its grant a RUN grant (`run`) scoped to its files `kind`."""
        return AppContext(self, app_id, needs, prefs_ns, run, kind)

    # -- the app bar contract: a HOST GUARANTEE, not a per-app ritual ---------
    #
    # On the fullscreen tiers a registered app shows the minimal exitable bar
    # (Moy, back, title and status, spec shell_ux_v1.md Section 9), the "app"
    # strip: the "tool" strip's drawing, on the SYSTEM canvas the app draws on
    # (bar_layer._zone_is_game). An app that drew or routed its own bar and
    # forgot either half became UNEXITABLE, silently, on device only.
    # The router already knows it is drawing a registered app, so it owns the
    # contract: frame()'s draw walk paints the strip AFTER the app's draw()
    # (chrome over content) and handle_pointer's walk routes the band BEFORE the
    # app's handle_pointer(). An app registered in future gets both for free,
    # including one that never heard of the bar -- pinned BEHAVIOURALLY (not by a
    # call-site count) in tests/test_app_api.py: a stub app that draws no strip
    # and routes no bar tap must still show the strip's pixels and still exit on
    # its back, and so must all seven shipped apps, parametrized.
    #
    # SCOPE, deliberately narrow: this owns the "app" strip for REGISTERED APPS
    # ONLY. The other strip kinds -- "menu" (the Editor surfaces), "settings",
    # "home"/"projects" (launcher_layer), "desk" (wm_windowed), "tool" (a running
    # TOOL CART's bar, _draw_tool_bar above) and "desktop" (the running cart's
    # crash chrome) -- stay with their surfaces. Collapsing the kinds would pick one and
    # silently break the way back on the rest.
    def _app_bar_route(self, app, px, py):
        """Route a click at (px, py) against registered `app`'s bar band.

        Returns None when the tap is off the bar (the app sees it as usual),
        True when the bar consumed it, and False when it did not -- the band
        belongs to the bar either way, so a miss inside it is swallowed rather
        than handed down, which is what each app's `return bool(...)` did.
        The band is the bar's own rects (`bar_layer.bar_rects("app")`), never
        the app's layout."""
        if not self.bar_layer.bar_at("app", px, py):
            return None
        # The back in that band is an EXIT path, so hard-commit first: an
        # app that persists on an idle debounce (#111) would otherwise lose the
        # last edit. Optional -- forgetting it costs an autosave, never the exit.
        commit = getattr(app, "commit", None)
        if commit is not None:
            commit()
        return bool(self.bar_layer.handle_bar_tap("app", px, py))

    def page_app(self, kind):
        """The app behind process `kind` whose pages its back steps through:
        a registered app, or Settings; None for any other kind."""
        app = self._apps_by_id.get(kind)
        if app is None and kind == "settings":
            app = self.settings_layer
        return app

    def app_has_page(self, kind):
        """Whether app `kind` (`page_app`) has a page of it open (its
        `has_page`), which its back steps out of: a desk window shows the
        back chevron in its title strip then. False for any other kind."""
        app = self.page_app(kind)
        has = getattr(app, "has_page", None) if app is not None else None
        return bool(has is not None and has())

    def app_back(self, app=None):
        """Back one level on registered `app` (None: the one on top) -- the
        bar's chevron, Esc and B (docs/history/look_2026-10.md step 27a): the app's
        own `back()` steps back a page of it and answers True; on its first
        page (False, or an app with no pages) the app is left for whoever
        opened it, committed first as the bar's exit always was."""
        if app is None:
            app = self._apps_by_id.get(self.wm.top_kind())
            if app is None:
                return
        self._dirty = True
        back = getattr(app, "back", None)
        if back is not None and back():
            return
        commit = getattr(app, "commit", None)
        if commit is not None:
            commit()
        self._exit_to_caller()

    def open(self, cart=None):
        # RUN landing (spec shell_ux_v1.md Section 2): build the workspace + run the
        # cart (`cart`, else the home's selected one) on the desktop, recording the
        # launcher home as the caller so QUIT pops home. Open to the desktop even if
        # the cart failed to start: frame() shows the error panel there so the kid
        # isn't stranded (a silent stay-on-launcher would be a dead end on the
        # device). Authoring is a separate app (the Editor), reached through the
        # home's Make tab or CHANGE, not a tap-mode on the played tabs.
        selected = cart if cart is not None else self.launcher.selected()
        self.carts.note_recent(selected)   # #105 desk_mru: every launcher-tap run counts
        self.search_typing = False     # a RUN always ends any in-progress query typing
        # SYSTEM APPS (docs/app_api_v1.md): a cartridge identity backed by a
        # responsive system process. Deliberately NOT the Player: the Player is
        # the fixed 320x240 contract, while an app reflows to a P4/web window.
        for _app, _text in self._apps:
            if _app.is_app(selected):
                self.open_app(_app, selected)
                return
        if (selected or {}).get("broken"):
            # A cart whose manifest will not parse cannot be RUN -- it has no
            # main, no fps and no permissions. A tap opens it where it can be
            # fixed instead (step 5 of docs/theme_and_shell_2026-11.md §10): the
            # Editor's Config tab, which shows the break and offers the file.
            self.open_in_editor(selected)
            return
        # Several games at once (runtime/shell/procs.py): the front one joins
        # the running set, a game already running comes forward, or the
        # admission names what to close.
        if not self.procs.launch(selected):
            return
        # The run's route is HOME (the launcher is its caller): what lets the
        # kernel stop the VM for it (docs/history/kernel_cartpath_2026-10.md 5.1).
        self._home_launch = True
        self.player.declined = False
        try:
            self._open_workspace(selected)
        finally:
            self._home_launch = False
        if self.player.declined:
            self.player.declined = False      # the shell asked first (ask_stop_music)
            return
        self.run(self.project, self.launcher_layer)   # activate desktop, record caller

    def run_script(self, kind, name):
        """RUN a SCRIPT: a bare `.py`/`.lua` file in the vault, which is a cart
        with NO FOLDER (step 6 of docs/theme_and_shell_2026-11.md §10).

        The file is read through the store, wrapped in a manifest synthesized
        HERE and written nowhere -- no folder is created, nothing is saved --
        and started down the ordinary Player path. `type: "script"` is
        tool-shaped (`player.BAR_TYPES`): one unpaced tick per loop frame, the
        minimal bar's X to leave. Its screen is `self.script_console`, and the
        SHELL keeps the pen: the console's draw becomes the run's `_draw` and
        its driver wraps the script's `_update`, so a script cannot paint over
        the prompt it is reading from, and a raise lands in the scrollback
        (there is no folder for crash-to-code to open).

        THE CAPABILITY GATE (#120) is that manifest and nothing more: `files`,
        `prefs`, `console`. `carts` is ungrantable to any cart at all (no
        permission maps to it, `moy_app.perms()`) and the network is gated on a `network`
        permission this manifest does not carry, so a script naming either has
        no such NAME -- and the console says which one it wanted.

        `(True, "")` when the script is running; `(False, why)` -- a kid-facing
        line -- when there was nothing to run."""
        store = self.carts_store
        src = None
        if store is not None and self.store.ready():
            try:
                src = self.store.call(
                    lambda: store.load_file(kind, name, self.carts_root))
            except Exception as exc:  # noqa: BLE001 -- a bad card is not a crash
                print("Moybyte script read failed:", exc)
        if src is None:
            return (False, "CAN'T READ IT")
        runtime = _modes.script_runtime(name)
        if not runtime:
            return (False, "NOT A SCRIPT")
        title = str(name)
        cut = title.rfind(".")
        cart = {"title": title[:cut] if cut > 0 else title,
                # The FILE is the path: a script has no folder, and the loader
                # never opens this -- it is the run's identity, and what keeps
                # `_open_workspace`'s pathless-cart guard from substituting a
                # real cart for it.
                "path": store.file_path(kind, name, self.carts_root),
                "type": SCRIPT_TYPE,
                "runtime": runtime,
                "permissions": ["files", "prefs", "console"],
                "src": src,
                "cfg": {},
                "version": 0}
        con = self.script_console
        con.start(cart["title"], runtime)
        caller = self._content_layer()
        if not self._open_workspace(cart):
            return (False, "CAN'T RUN IT")
        player = self.player
        if player.cart_error is not None:
            # The body did not survive its own exec. The console IS this run's
            # error surface, so the traceback goes there and the run continues
            # as a readable, exitable screen instead of an error panel over a
            # cart with nothing to edit.
            con.crash(player.cart_error)
            player.cart_error = None
            player.crash_line = None
        player._draw = con.draw
        player._update = con.driver(player._update)
        self._set_text_mode(True)      # a script TYPES; this is the surface taking
        con.seed_key(getattr(self.input, "last_key", 0) or 0)
        self.run(self.project, caller)
        return (True, "")

    def ask_run(self, handle):
        """RUN the script an editor handle holds (`EditorHandle.run`), as a
        deferred transition (`defer`): the asking cart is mid-frame, and its
        window is still to be drawn from the world the run replaces."""
        self._editor_run = handle
        self.defer(self._run_asked)

    def _run_asked(self):
        """Start the run `ask_run` parked: the script runs down Files' path
        (`run_script`), and its exit comes back into the cart that asked, on
        the same document (`_return_to_runner`). A refusal before the world
        changed is the handle's badge; one after it (the workspace would not
        open) goes straight back. Nothing runs when the asking run is over."""
        ed = self._editor_run
        self._editor_run = None
        if ed is None or ed not in self.player._editors:
            return
        back = (self.cart, ed.kind, ed.name(), ed.mode(), ed._where(),
                self.returns.caller())
        ok, why = self.run_script(ed.kind, ed.name())
        if ok:
            self._script_return = back
        elif ed in self.player._editors:
            ed._refuse(why)
        else:
            self._return_to_runner(back)

    def _return_to_runner(self, back):
        """Come back from a script an editor handle ran to the cart that ran
        it: that cart started again with the document parked as its request
        (what its `open_editor()` answers), the caret and the view's top line
        and column put back (`EditorHandle._put`), and the cart's own caller
        restored, so leaving it still lands where it would have (Files' shelf,
        the Config tab, home). A list the cart keeps on the document's row
        shows that row: a placed table opens on its current row."""
        cart, kind, name, mode, where, caller = back
        self._text_request = (kind, name, mode)
        if not self._open_workspace(cart):
            self._text_request = None
            self._go_home_or_back()
            return
        self.run(self.project, caller)
        for ed in self.player._editors:
            if ed.kind == kind and ed.name() == name:
                ed._put(where)

    def open_image(self, name, kind=None, cart=None):
        """Open a PICTURE in Paint -- the ONE image door, taken by the Files
        router and by the Config tab's Files alike (#108).

        `cart` names a project whose OWN image this is (`images/<name>.moyimg`,
        or its `cover.png`): Paint then reads and writes it on that project's
        kind, in place, so an edited cover is the cover. Without one it is a gallery drawing. False
        when this build carries no Paint app, which each door reports on its
        own status line -- but a picture is never REFUSED for its shape: one
        Paint cannot edit opens read-only rather than not at all."""
        app = self._apps_by_id.get("artwork")
        if app is None:
            return False
        into_project = None
        if cart is not None:
            path = (cart or {}).get("path")
            if not path:
                return False
            kind = self.carts_store.project_kind(path)
            if self.project is not None and self.project.cart is cart:
                into_project = cart      # opened FROM this project's Editor
        self.artwork.open_named(name, kind)
        if not self.open_app(app):
            return False
        # Leaving Paint comes back to the Config tab THROUGH the loader, the
        # same contract a project TEXT file has: the folder is re-read, so an
        # edited cover is the cover the shelf shows.
        self._project_return = into_project
        return True

    def open_in_editor(self, cart=None):
        """Open `cart` (default: the launcher selection) in the Editor,
        landing on Config (spec Section 6). The cart is started (ready for
        PLAY) but not shown; the Editor owns its screen until PLAY runs it.
        Fullscreen where the board has no desk; in a window of its own where
        it has one -- opened from the console-mode home, console mode gives
        way to the desk first (owner, 2026-10-20), and closing that window
        gives the glass back to the home (`editor_closed`, R5-05)."""
        wm = self.wm
        back = None
        if wm.has_desk and wm.mode == "console" and self.screen == "launcher":
            sel = self.launcher.selected()
            back = (self.launcher.tab, sel.get("path") if sel else None)
            self.open_desk()
        self._editor_from = None
        if not self._open_workspace(cart):
            # No source, nothing to edit: land on the same error panel a crashed
            # cart lands on rather than opening the Editor over an empty project.
            self.run(self.project, self.launcher_layer)
            return
        self.editor_app.open(self.project)
        if back is not None:
            self._editor_from = back + (frozenset(wm.window_kinds()),)

    def editor_closed(self):
        """The Editor's window closed (its X): an Editor the console-mode
        home handed to the desk (`open_in_editor`) gives the glass back to
        that home, on the tab and the card it was opened from -- unless a
        window the desk did not have then is open on it now (a playtest
        included), which keeps the kid on the desk (owner, 2026-10-20)."""
        back, self._editor_from = self._editor_from, None
        wm = self.wm
        if back is None or not wm.desk_open():
            return
        tab, path, kinds = back
        if not set(wm.window_kinds()) <= kinds:
            return
        self.open_library()
        self.set_home_tab(tab)
        lw = self.launcher
        for j in range(len(lw.items)):
            if path and lw.items[j].get("path") == path:
                lw.sel = j
                break

    # The four builders moved VERBATIM onto Project (Stage 1, project.py); these stay
    # as one-line forwards so ws._build_sheet(cart)/... keep working (the wallpaper
    # runner calls ws._build_sheet(cart), _start calls _build_audio, and open()
    # calls all four -- all through self.project now).
    def _build_sheet(self, cart=None):
        return self.project._build_sheet(cart)

    def _build_pmem(self):
        return self.project._build_pmem()

    def _build_tilemap(self, cart=None):
        return self.project._build_tilemap(cart)

    def _build_flags(self, cart=None):
        return self.project._build_flags(cart)

    def _build_scenes(self, cart=None):
        return self.project._build_scenes(cart)

    def _build_audio(self):
        return self.project._build_audio()

    # -- frame + drawing -----------------------------------------------------

    def _reset_canvas_state(self):
        # Reset the canvas's TIC-80 draw state (camera/clip/pal/palt, #11) if the
        # backend supports it. Guarded so a backend without draw state (a test stub,
        # or a recording canvas) is a no-op. reset_state() also flushes any pending
        # auto-batch (#63), so cart sprites land before the console overlays draw.
        # The probe is cached by canvas identity: a `getattr` that finds a
        # method allocates a bound method, and this runs every cart frame.
        cv = self.canvas
        if cv is not self._rs_cv:
            self._rs_cv = cv
            self._rs_fn = getattr(cv, "reset_state", None)
        rs = self._rs_fn
        if rs is not None:
            rs()

    def _draw_loading_toast(self):
        """#184: the deferred-transition acknowledgment (GAPS 51) -- the
        toast's pill in flat fills, painted once on the frame between a tap
        and its heavy transition, which the panel retains through the load:
        the cart's kind as an icon and what opens ("Opening Sky Run..."),
        over the middle of the card being opened on a handheld and in the
        middle of the bar on the desk, the one band no window covers."""
        cv = self.sys_canvas
        lay = self.layout
        desk = self.metric_class == _layout_base.DESK
        cart = self.launcher.selected() if self.screen == "launcher" else None
        title = str((cart or {}).get("title") or "").strip()
        label = ("Opening %s\u2026" % title) if title else "Opening\u2026"
        kind = _LOADING_ICON.get((cart or {}).get("type"), "play")
        host = self._loading_host
        if host is None:
            host = self._loading_host = _ui_draw.AppHost()
        host.begin(cv, self.theme_colors, (0, 0, cv.w, cv.h), desk)
        h = (30 if desk else 20 * lay.fs)
        if desk:
            bx, by, bw, bh = lay.bars[0][1] if lay.bars else (0, 0, cv.w, lay.cy)
            _uiv.at(bx, by + (bh - h) // 2, bw, h)
        else:
            r = None
            if self.screen == "launcher":
                grid = self.launcher_layer.grid
                r = grid.tile_rect(getattr(grid, "sel", 0))
            cy = r[1] + r[3] // 2 if r is not None else lay.cy + lay.ch // 2
            _uiv.at(8 * lay.fs, cy - h // 2, cv.w - 16 * lay.fs, h)
            strip = self.menu_ui.about._strip()
            if strip is not None:         # a load cannot be cancelled: no keys
                cv.rect(strip[0], strip[1], strip[2], strip[3], self.theme_colors["panel"])
        self._loading_rect = _uiv.toast(label, kind, _FLAT)

    def _disarm_fn(self):
        """The compositor's disarm_scale_fold, or None: probed once per
        compositor, because a getattr that finds a method is a bound-method
        allocation and this is asked on every frame an overlay paints over the
        game."""
        comp = self.comp
        if comp is not self._dsf_comp:
            self._dsf_comp = comp
            self._dsf_fn = getattr(comp, "disarm_scale_fold", None)
        return self._dsf_fn

    def _flush_batches(self):
        # Draw any sprites still pending in a canvas's auto-batch (Fold 1, #63) before
        # the frame is composited / flushed to the panel, so nothing queued by the last
        # spr() in a cart's _draw() (or the chrome) is left unpainted. Guarded + covers
        # both the game and system canvas -- one probe when they are the same object
        # (the 320x240 device case; #75: no per-frame tuple, no duplicate probe).
        # Both probes cached by canvas identity (a found method is a bound-method
        # allocation per probe, and this runs every painted frame).
        cv = self.canvas
        if cv is not self._fb_cv:
            self._fb_cv = cv
            self._fb_cv_fn = getattr(cv, "flush_batch", None)
        fb = self._fb_cv_fn
        if fb is not None:
            fb()
        sc = self.sys_canvas
        if sc is not cv:
            if sc is not self._fb_sc:
                self._fb_sc = sc
                self._fb_sc_fn = getattr(sc, "flush_batch", None)
            fb = self._fb_sc_fn
            if fb is not None:
                fb()

    # -- two-domain composite + viewport coords (#39) ------------------------
    #
    # The viewport composite moved to FullscreenStackWM (Stage 6, wm.py); these stay as
    # the tested ws. entry points -- ws._game_xy is called from many surfaces (layers/
    # cards/code/paint + the Player), ws._viewport from tests, ws._composite_game from
    # frame(). Thin forwards to the one ws.wm.

    def _viewport(self):
        return self.wm.viewport()

    @property
    def game_view(self):
        """The running cart's declared logical viewport (the additive
        `view(w, h)` cart verb): a centered (sx, sy, w, h) source rect of the
        game canvas, or None for the full canvas. The fullscreen composite
        scales the VIEW to the surface instead of the container -- celeste's
        128x128 cart canvas (SPEC.md 3.1) concedes 8 rows via view(128, 120)
        and fills a 4:3 screen's height (2x on the handheld, 5x on the P4)
        instead of letterboxing the square -- and game_xy maps taps back
        through it. Lives on the shared InputState (the cart_quit pattern) so
        the verb needs no console handle; Player.start clears it per run."""
        v = getattr(self.input, "game_view", None)
        if not v:
            return None
        gc = self.canvas
        w = min(int(v[0]), gc.w)
        h = min(int(v[1]), gc.h)
        if w <= 0 or h <= 0 or (w == gc.w and h == gc.h):
            return None
        return ((gc.w - w) // 2, (gc.h - h) // 2, w, h)

    def _game_xy(self, px, py):
        return self.wm.game_xy(px, py)

    def _composite_game(self):
        # A compiled cart's frame still in its own memory is shown from there
        # when the WM can; otherwise it lands in the game canvas and the
        # composite reads it as ever.
        cf = self.cart_frame
        if cf is not None:
            view = cf.take()
            if view is not None:
                if self.wm.present_frame(cf, view):
                    return None
                cf.settle(self.canvas)
            cf.nrects = 0
        return self.wm.composite_game()

    def patch_cart_frame(self, x, y, w, h):
        """Before painting an OPAQUE rect on the game canvas over a compiled
        cart's frame -- the FPS chip, the perf HUD line: the flush takes that
        rect from the canvas and the rest from the frame, so the frame need
        not be written first. A rect the frame cannot take settles it."""
        cf = self.cart_frame
        if cf is not None and not cf.patch(x, y, w, h):
            cf.settle(self.canvas)

    def settle_cart_frame(self):
        """Before anything draws on the game canvas over a compiled cart's
        frame: put the frame there first, the bytes its blit would have
        written."""
        cf = self.cart_frame
        if cf is not None:
            cf.settle()

    # -- per-run cart canvas (SPEC.md 1/3.1) ---------------------------------
    #
    # Binding and releasing the run's canvas are console_canvas's; the window
    # size stays here, read on every frame of a window-bound app cart.

    def app_window_size(self):
        """(w, h) of a window-bound app cart's content: its window's content
        rect, which its canvas holds from the top left."""
        return self.wm.app_window_content()

    # -- redraw-on-change (#44 step 1) ---------------------------------------

    def request_frame(self):
        """Ask for one more frame from WITHIN a draw (#113: a coasting fling).
        mark_dirty() would be lost here -- the gate clears _dirty right after
        the draw -- so this sets a flag consumed AFTER that clear (the
        covers.take_deferred pattern)."""
        self._frame_requested = True

    def mark_dirty(self):
        """Request a redraw on the next frame(). Called whenever a visible change
        could have happened (input that mutates state, scrolls, edits, screen/menu
        switches, selection moves). Cheap + idempotent -- the actual draw is
        coalesced to one in frame()."""
        self._dirty = True

    def _ptr_state(self):
        """The pointer state that affects what's drawn: position, visibility, the
        held/click flags. A change here (cursor moved, auto-hid, tapped, drag) means
        the picture differs, so frame() must repaint. None when there's no pointer."""
        p = self.pointer
        if p is None:
            return None
        return (p.x, p.y, bool(p.visible), bool(p.down), bool(p.click))

    def _animating(self, dt, pages=True):
        """True when SOMETHING on screen changes every frame on its own, so the UI
        must keep redrawing even without input (`pages` False: but a verbs
        page moving on its own, which changes nothing outside its content --
        the windowed WM's word for a frame whose only change is a window's):
          - a running cart (games animate -> unchanged full-redraw behaviour),
          - a live wallpaper on the home/settings backdrop (its _update advances it),
          - the achievement toast / Konami confetti / Easter-egg popup while active.
        A static launcher/editor/menu with a still wallpaper hits none of these."""
        # The boot logo animates the frame loop until it expires (frame() clears it),
        # so it keeps painting + flushing for its whole hold without any input.
        if self._splash_until is not None:
            return True
        # Stage 6d: the animation gates ask the WM stack top (the source of truth), not
        # the `screen` projection string -- same answers, hoisted once per call.
        kind = self.wm.top_kind()
        # A running cart on the desktop draws every frame, unless it crashed (the
        # error panel is static) or its run is held (the pause menu's card is
        # drawn on a change, over the frozen frame; a hidden window, the desk
        # Editor's paused Run slot). An app on the verbs, drawn on change,
        # changes its own content alone, as a verbs page does (`pages`).
        player = self.player
        # (The Player's own fields: ws._update/_draw are property forwards to
        # exactly these.) An app on the verbs draws on change.
        if kind == "desktop" and player.cart_error is None and not self.pause.open \
                and not player.paused and (
                    player._update is not None or player._draw is not None) \
                and (pages or not player._on_change) and player.animates():
            return True
        # Windowed WM (#73): a running cart's WINDOW keeps animating even when
        # another window sits above it on the stack (Settings over a game, the
        # editor beside a playtest). No hook on the fullscreen-stack WM.
        _ka = getattr(self.wm, "keeps_animating", None)
        if _ka is not None and _ka(dt, pages):
            return True
        # A music-editor preview must keep ticking the mixer + redrawing the PLAY/STOP
        # button (and clearing the flag when the effect ends) without input (#50).
        if kind == "menu" and self.music_ui.music_preview is not None \
                and (self.menu_view == "music"
                     or (self._editor_desk is not None
                         and self._editor_desk.key == "sound")):
            return True
        # A live wallpaper animates the home/settings/desk backdrop.
        if kind in ("launcher", "settings", "desk") and self.wallpaper.is_animating(dt):
            return True
        # The P4 Bluetooth keyboard picker advances through scan/pair/discovery
        # asynchronously. A static wallpaper would otherwise close the redraw
        # gate after its first frame and hide newly-found devices/status changes.
        if kind == "settings" and self.settings_layer.bluetooth_animating():
            return True
        # The System log page follows the ring: a new record draws it again.
        if kind == "settings" and self.settings_layer.log_moved():
            return True
        # The task manager's rows moved: a sample, a state, a run.
        if kind == "tasks" and self.tasks_layer.moved():
            return True
        # A verbs page the last painted frame drew coasts on a fling or eases
        # on a glide (`api_moving`): its next frame is due with nothing dirty.
        if pages and self.ui_moving:
            return True
        # (The Appearance app's monitor shows a COMPUTED still on every tier,
        # so it needs no live-wallpaper redraw exception -- the gate closes
        # like any static UI and the web view idles at ~0 KB/s there.)
        # A firmware install (#53) advances a chunk per frame; "done" runs a short
        # reboot countdown; "checking"/"downloading" (Phase 3) step the online flow.
        # All must keep redrawing so progress animates and the work proceeds without
        # input -- and the C6 radio flow (#7/#58) pumps from the same draw, so its
        # working phases join the list (its confirm/uptodate phases wait on input
        # like everything else and are deliberately absent: the first on-glass run
        # of the flow stalled at "downloading" forever because the gate closed).
        if kind == "update" and self.update_ui._upd_phase in (
                "install", "done", "checking", "downloading",
                "c6_checking", "c6_downloading", "c6_flashing", "c6_done"):
            return True
        # The unlock banner redraws while it is up: a plain int read (#209
        # landing B) the unlock wrote at its event, so this gate never calls
        # into the achievements.
        now = _ticks_ms()
        if self._toast_until and _ticks_diff(self._toast_until, now) > 0:
            return True
        if self.notice_active():
            return True
        return _NOTES.banner is not None and _NOTES.up(now) is not None

    def _needs_redraw(self, dt):
        """Decide whether frame() must repaint+flush this frame. True when something
        marked the UI dirty, an animation source is live, or the pointer state the
        last frame drew has changed (cursor move/hide, tap, drag) -- or the
        kernel drew a frame of its own since this console last painted, which
        reads as dirty for as many frames as the backend retains, so no buffer
        keeps the kernel's picture."""
        k = _glass.kernel_epoch()
        if k != self._kepoch:
            self._kepoch = k
            self._kframes = max(1, getattr(self._sys_canvas or self.canvas,
                                           "RETAINED_FRAMES", 1))
        if self._kframes:
            self._kframes -= 1
            self._dirty = True
            return True
        if self._damage_take():
            # The app ABI's damage flags (native/moy_app): an app's `all` or
            # `again`, folded into the one dirty flag here.
            self._dirty = True
        if self._dirty:
            return True
        if self.wm.popover_owed():
            return True               # the popover's rect, owed to a buffer
        if self.wm.debt_owed():
            return True               # a buffer behind the last change, paid while calm
        if self._animating(dt):
            return True
        if self._ptr_changed():
            return True
        return False

    def _ptr_changed(self):
        """`_ptr_state() != _last_ptr`, field by field -- the same answer with
        no tuple built, because this and the tail's snapshot run every frame."""
        p = self.pointer
        last = self._last_ptr
        if p is None:
            return last is not None
        if last is None:
            return True
        return (last[0] != p.x or last[1] != p.y
                or last[2] != bool(p.visible) or last[3] != bool(p.down)
                or last[4] != bool(p.click))

    # -- content-layer draw bodies (routed from the frame() stack loop) -------
    #
    # (The running-cart content body -- the cart tick -- is Player.tick
    # (player.py). It fills the DRAWBRK perf split ws._pf_* and opens the stop
    # card over a run that stopped. The "desktop" content layer routes to it
    # via _PlayerLayer.)

    # (_draw_menu_backdrop -- the frozen-cart backdrop under the cards/paint/map
    # panels -- was removed by the #39 step-3 conversions: every Editor tab is
    # system-domain now and always fully covered the backdrop anyway, so the tabs
    # just reset the game canvas's draw state and paint their own opaque body.)

    def boot_services(self):
        """The services that start with the console: every service cart
        holding `autostart`, and those a VM stop paused (scheduler.boot)."""
        try:
            _scheduler.boot(self)
        except Exception as exc:  # noqa: BLE001 -- a service never stops the desk
            print("Moybyte services: %s" % (exc,))

    def frame(self, dt):
        # DEEP meters (the per-op canvas timers, the batch counters, the DRAWBRK
        # EMA tail) run ONLY under perf_capture -- the measurement-session mode,
        # which follows Settings -> PERF DIAG on device. perf_hud alone keeps
        # the LIGHT set (frame total, flush, fps): watching the fps chip must
        # not cost milliseconds.
        # A store session a role opened and left open (`files.begin`, the C
        # row's count; `carts.begin`, the handle's) ends here, at the next
        # frame: it holds no gate, so this is bookkeeping, never a flush held
        # off (native/moy_app/README.md).
        fs = self.frame_state
        if self._services_due and fs.frames_drawn:
            self._services_due = False
            self.boot_services()
        if _scheduler.SCHED.wakes.queued:
            _scheduler.SCHED.mail()   # messages the input sent: served before the draw
        if self.ach.pending is not None:
            self.ach.flush()          # the badges the achievements process handed back
        if self._press_owed:
            self._press_owed = False
            if not self._press_taken:
                self._dirty = True        # the press handle_input left owed
        # A store on the Workstation looks through all its classes for a
        # property (about 57 us on a T-Deck), so the frame path stores a
        # per-frame flag only when it changes, and what changes every frame
        # lives on `fs`, a plain object.
        if self._press_taken:
            self._press_taken = False
        st = self.store
        if st.open:
            st.end_all()
        else:
            self._files_end_all()
        lk = self.link
        if lk is not None and getattr(lk, "_m", None) is not None:
            lk.sync(self)             # the session the link formed, the cart it asked for
        if dt > 0:
            fs.since_draw += dt
            # The loop tick in ms for the input phase (which runs BEFORE frame()
            # and has no dt of its own): feeds the kinetic scroll velocity
            # (#113). Clamped so a hitch can't spike the physics.
            ms = min(dt * 1000.0, 100.0)
            fs.frame_dt_ms = ms
            # ... and the verbs engine's clock, the scrolling pages' motion
            _ui_draw.frame_dt(ms)
        # The cover collaborator's two per-frame touches: this budget reset and
        # the deferred drain at the tail. Bound once, here, so both cost a call
        # and no second lookup (#209 landing C -- nothing else in the loop
        # reaches into covers, and neither goes through a forward).
        _ui_draw.begin_frame()          # the one-primary check's frame
        covers = self.covers
        covers.begin_frame()          # the per-frame cover-build budget, which is
                                      # a TIME slice, not a count
        if self._pf_home is not None:
            self._pf_home = None      # home-frame split (launcher_layer, perf_capture)
        # Undo journal (Stage 7): the idle-typing autosave debounce runs BEFORE the
        # redraw gate below, so it fires even on a static (redraw-skipped) editor
        # frame. The router is called DIRECTLY here (#209 landing E, doc 3b: the
        # frame loop never goes through a forward). Unlike `covers` above it is not
        # bound to a local -- one call site, so a local would be one store more
        # than the two attribute loads it saves nothing on.
        self.history.idle_tick()
        # The look's change, applied once a frame, and `custom`'s autosave
        # debounce (theming phase 6 step 5): one attribute read when idle.
        self.look.tick(dt)
        # A banner over the system canvas this frame (the unlock's, a notice):
        # its soft edges blend over what is under them, so an app that paints
        # a part of its page (Paint's stroke frame) paints it whole meanwhile.
        cv = self.sys_canvas
        if cv is not None:
            now = _ticks_ms()
            cv.covered = bool((self._toast_until and _ticks_diff(self._toast_until, now) > 0
                               and self.ach.toast is not None)
                              or (self._notice is not None and self.notice_active(now))
                              or (_NOTES.banner is not None and _NOTES.up(now) is not None))
        # Input focus moved (a window clicked, a screen left): Settings stores
        # a Name still being typed and hands back the keyboard's text mode.
        h = self.wm.input_holder()
        if h != self._holder:
            was = self._holder
            self._holder = h
            if was == "settings":
                self.settings_layer.leave()
        # Boot logo: expire the splash before the redraw gate so THIS frame reveals the
        # launcher. While it's live it's an _animating source, so the loop keeps flushing
        # it; marking dirty on expiry guarantees the launcher paints on the next frame.
        if self._splash_until is not None and _ticks_diff(self._splash_until, _ticks_ms()) <= 0:
            self._splash_until = None
            self._dirty = True
        # The tick model (#217): while a GAME plays, the Player's scheduler
        # decides what this loop frame is -- which logic ticks run, and whether
        # it draws. A frame that does not draw ticks logic only and presents
        # nothing: the panel keeps the last frame, and the loop hands the time
        # back. Sits BEFORE the redraw gate so it never consumes dirty state. A
        # lockstep match (#65) owns its clock in there: the world moves only on
        # the shared tick, so a frame that tick is not due for holds the last
        # agreed frame instead of repainting it.
        procs = self.procs
        procs.frame(dt)               # the running set's games, by their states
        if (self.wm.top_is_player() and self.player.cart_error is None and not self.pause.open
                and not procs.front_minimised()):
            if not self.player.frame_plan(dt):
                self.player.tick(dt, render=False)
                return
        elif self.player.tick_ms:
            self.player.park()
        # The kernel's idle ladder is on its SAVER rung (idle_ladder.Saver): the
        # saver owns the glass until the ladder wakes, which moves the kernel
        # epoch so the gate below repaints what it covered.
        saver = self._saver
        if saver is not None:
            if saver.frame(dt):
                self._flush_batches()
                if self._half is not None:
                    self._present_half()
                self.comp.flush()
                fs.frames_drawn += 1
            return
        # Redraw-on-change (#44): a static UI screen (no animation, no pointer change,
        # nothing marked dirty) is skipped entirely -- no draw, no flush. The panel /
        # host window simply retains the last frame, so an idle UI costs ~0 and the
        # device saves the SPI flush + power. A running cart / live wallpaper / active
        # overlay always reports animating, so it redraws every frame as before.
        if not self._needs_redraw(dt):
            # Nothing to paint. Spend the frame bringing a window's buffer up
            # to the glass (the WM's `refill_step`, a copy), and warming the
            # next cover (#155) -- that after two quiet frames, so the gap
            # between two gestures is not spent on a 108ms flash read. That is
            # the idle branch's work. The warmers dirty nothing (the home's visible cards
            # are built by the draw path, the prefetch is for the ones
            # off-screen); an app's idle step asks for the frame that shows
            # what it made, through its own damage.
            self._quiet_frames += 1
            try:
                # The focused window's buffer, which direct render left
                # behind the glass, refilled from the last frame's buffer:
                # one copy, on a frame no input waits on.
                self.wm.refill_step()
            except Exception as exc:  # noqa: BLE001 -- an idle tick never ends a session
                if not self._idle_warned:
                    self._idle_warned = True
                    print("Moybyte idle work failed:", _err_text(exc))
            if self._quiet_frames > 2:
                # NOTHING HERE MAY RAISE. This branch runs on a frame with no
                # user waiting on it, which is exactly why it is the wrong place
                # to end a session: on 2026-09-07 both S3 boards reached the desk
                # after a flash and dropped to the REPL within minutes, because a
                # cover the prefetch was warming asked a fragmented heap for a
                # 76,800-byte raster and the MemoryError walked straight out of
                # the loop. Each warmer fences its own expected failures (a
                # cover it cannot build is skipped for the session); this is the
                # backstop for the unexpected one, and it says so once rather
                # than every idle frame forever.
                try:
                    covers.prefetch_tick()
                    # Mint the home retained-frame buffer off the paint path too
                    # (idempotent after the first call; the device new_layer
                    # pre-collects, ~150ms nobody should wait for).
                    self.launcher_layer.prealloc_retained()
                    self.pause.prealloc()       # ... and the pause card's frame
                    self.wm.prealloc()          # ... and a modal's scrim snapshot
                    desk = self._editor_desk
                    if desk is not None:
                        desk.warm_step()        # the desk Editor's next workspace
                    for app, _t in self._apps:  # ... and an app's owed work (My Art's pictures)
                        if getattr(app, "idle_owed", False):
                            app.idle_step()
                            break
                    if getattr(self.launcher_layer, "_cap_owed", None) is not None:
                        self.launcher_layer.capture_due()
                    if getattr(self.launcher_layer, "_debt_owed", None) is not None:
                        self.launcher_layer.debt_due()   # (a step's buffer behind)
                except Exception as exc:  # noqa: BLE001 -- an idle tick never ends a session
                    if not self._idle_warned:
                        self._idle_warned = True
                        print("Moybyte idle work failed:", _err_text(exc))
            return
        if self._quiet_frames:
            self._quiet_frames = 0
        # The fps the chip shows is the DRAWN rate (#217): an EMA over the
        # frames that reach the glass, so a paced cart reads its draw rate and
        # not the loop's spin between ticks.
        if fs.since_draw > 0:
            fs.fps = _ema(fs.fps, 1.0 / fs.since_draw)
            fs.since_draw = 0.0
        # Perf HUD (#43/#44): mark the start of this frame's draw work. Cheap (one
        # ticks call); only meaningful for a frame we actually paint, so it's after
        # the redraw gate. _flush_ms is filled around comp.flush() below; _draw_ms
        # is the rest (total span - flush). Both EMA-smoothed at frame end. Also
        # fires when perf_capture is set (device diag sampling) -- not just the HUD.
        _perf = self.perf_hud or self.perf_capture
        _deep = self.perf_capture
        # MICROSECONDS: every DRAWBRK bracket is on this clock, converted to ms
        # once at the EMA (_frame_perf_end).
        _frame_t0 = _ticks_us() if _perf else 0
        if _deep:
            _bc = getattr(self.canvas, "batch_reset", None)
            if _bc is not None:
                _bc()                  # #63: zero this frame's auto-batch profiling counters
            # The DRAWBRK scratch (Player.tick fills it on the cart path, in
            # us). Zeroed ONLY under capture -- the writer fills it under
            # capture too and the reads are capture-gated, so a kid-mode play
            # frame skips the four stores. Zeroed HERE, on every captured
            # frame, never under "capture just went off": a frame whose writer
            # did not fire would otherwise report the previous writer's value
            # (launcher frames once carried the last cart frame's logic/render
            # in every line that read them).
            self._pf_upd = 0    # cart _update(dt) (game LOGIC); 0 off the cart path
            self._pf_cart = 0   # cart _draw() (RENDERING)
            self._pf_audio = 0  # audio.tick(dt) (mixer feed)
            self._pf_bg = 0     # the backdrop restore's share of _pf_cart
        elif getattr(self.canvas, "_prof", False):
            # Perf capture just went off: clear the device canvas's DRAW2 timing
            # gate so its hot verbs stop paying the per-op ticks_us pair (~6us a
            # fill, and chrome issues them by the hundred -- see _fill's note in
            # device_canvas.py). One attribute read per frame here; the host
            # canvas has no _prof, so getattr returns False and this never fires.
            self.canvas._prof = False
        # Compositor / router (docs/history/shell_layers_refactor_v1.md §3): draw the z-ordered
        # visible stack bottom -> top. The active content draws first (game-domain
        # content on the fixed 320x240 game canvas); at the game->system domain boundary
        # the router composites that viewport into the system canvas ONCE (#39; the
        # launcher/settings + responsive code/blocks content are system-domain and skip
        # it); the chrome/overlays + cursor then draw on top on the system canvas. The
        # cursor is always the top system layer, so a game-domain content is always
        # composited before it -- reproducing the pre-refactor single composite step.
        _prev_domain = None
        # The canvas probes below are re-taken only when a canvas object
        # changes (#66 lever 1): a probe answers for the object, so keying on
        # identity is the same answer at a compare's cost instead of a getattr
        # with a default (~30us each on the S3, four of them every play frame).
        _sc = self.sys_canvas
        if _sc is not self._probe_sc:
            self._probe_sc = _sc
            self._probe_view = getattr(_sc, "view", None)
        # FULLSCREEN GAME-DOMAIN PLACEMENT on a COMMAND-ONLY game canvas (#175).
        # There the cart's frame has no pixels for _composite_game to scale up,
        # so its draw span must be BRACKETED instead -- otherwise every
        # fullscreen cart (and console mode's Library, which is game-domain
        # content) lands 1:1 at the desktop origin: the owner's "play menu loads
        # all games in the upper left corner". The bracket uses the WM's own
        # viewport geometry, i.e. exactly what the pixel composite would have
        # applied. Two getattr probes on a raw canvas -> None (the S3/device path
        # is byte-identical, and a raster game canvas keeps the composite).
        _view = None
        if self._sys_canvas is not None:
            _gc = self.canvas
            if _gc is not self._probe_gc:
                self._probe_gc = _gc
                self._probe_buf = getattr(_gc, "buf", None)
            if self._probe_buf is None:
                _view = self._probe_view
        # ...and only where the WM presents the game FULLSCREEN. In the windowed
        # desk world the player WINDOW brackets its own content, so letterboxing
        # here would black out the desktop -- and it would fire on the FPS
        # overlay too, which is a game-domain layer.
        _vp = None
        if _view is not None:
            _fs_game = getattr(self.wm, "game_is_fullscreen", None)
            if _fs_game is None or _fs_game():
                _vp = getattr(self.wm, "viewport", None)
        _game_open = False
        # RASTER TWIN of the letterbox above, for the tier where the system
        # canvas IS the game canvas: there composite_game short-circuits, so the
        # bezel it normally fills is never written at all and a cart-declared
        # view leaves stale pixels that FLASH on a double-buffered root (#58).
        # Same fix, same place in the order -- before the cart draws. The WM
        # decides whether it applies; a no-view cart pays one getattr.
        _lb = None
        if _vp is None:
            _wm = self.wm
            if _wm is not self._lb_wm:        # cached by WM identity: the probe
                self._lb_wm = _wm             # allocates a bound method a frame
                self._lb_fn = getattr(_wm, "letterbox_inplace", None)
            _lb = self._lb_fn
        _lb_done = False
        # #190: while a flush-bounce scale fold is armed (the device composite
        # SKIPPED writing the root fb -- the flush will synthesize it), any
        # layer that would paint the root ON TOP must first disarm, which makes
        # the comp perform the skipped composite. The cursor layer is always
        # stacked but only paints when the pointer is visible, so it disarms
        # only then. One compare per post-composite layer; None everywhere the
        # comp has no fold (host/P4/web).
        _fold_live = False
        # THE APP BAR CONTRACT, draw half (docs/app_api_v1.md): a REGISTERED
        # system app gets the minimal exitable "app" strip drawn over its
        # content by the router -- the app draws no bar of its own. Resolved
        # ONCE per frame (the walk cannot change either term): `_apps_by_id`
        # while the fullscreen chrome rules apply, None in the windowed desk
        # world, where the WM's title strip carries the close instead. See
        # `_app_bar_route` for the input half and the scope note there. An
        # app whose `glass` is true holds the whole glass for now (Storybook
        # presenting): no strip, and the band's taps are the app's; its back
        # is Esc and B, and the console's key opens the system menu as ever.
        _appbar = self._apps_by_id if not self.wm.desk_open() else None
        # One focus mark a screen: while the pause menu or the system menu
        # holds the keys, what draws under it shows its current, not its
        # focus (ui.marks); the menu's own layer and those above it mark.
        _modal = self._modal_layer()
        if _modal is not None or self._marks_held:
            self._marks_held = _modal is not None
            _ui_draw.marks(_modal is None)
        _stack = self.wm.draw_stack()               # memoized (Stage 6c) -- no per-frame alloc
        # The content's own ring vouches for its buffers where it is alone on
        # a full-screen tier (docs/history/scroll_and_text_2026-10.md §3.4), or alone
        # but for the FPS chip, which names where it drew (`overdrew`).
        _ring = self.wm.CONTENT_RING
        _alone = _ring and self.wm.content_alone()
        _ui_draw.paint_frame(fs.frames_drawn, _alone, self,
                             not _alone and _ring and self.show_fps
                             and self.wm.content_alone(_SIG_FPS))
        for layer in _stack:
            if _modal is not None and layer.id in _modal:
                _ui_draw.marks(True)
            if _prev_domain == "game" and layer.domain == "system":
                if _game_open:                      # close the placement span
                    _view()
                    _game_open = False
                self._composite_game()
                _fold_live = True
                if self.player._is_tool and self.bar_layer.tool_band() is not None:
                    _dsf = self._disarm_fn()        # #190: the band paints the root
                    if _dsf is not None:
                        _dsf()
                    self._draw_tool_band()
            if _fold_live and (layer is not self._cursor_layer
                               or (self.pointer is not None
                                   and self.pointer.visible)):
                _dsf = self._disarm_fn()
                if _dsf is not None:
                    _dsf()
                _fold_live = False
            if _vp is not None and layer.domain == "game" and not _game_open:
                _ox, _oy, _sc = _vp()
                # LETTERBOX FIRST. The pixel composite fills the bezel AFTER the
                # cart draws (it owns the framebuffer); a command-only canvas
                # cannot -- that fill would wipe the frame already recorded, so
                # composite_game bails and console mode showed the LIBRARY
                # behind the game (owner, 2026-07-31). Painting it here, before
                # the bracket opens, is the same pixels in the right order.
                self.sys_canvas.cls(0)      # _VIEWPORT_BEZEL: black
                _view(_ox, _oy, _sc, self.canvas.w, self.canvas.h)
                _game_open = True
            if _lb is not None and layer.domain == "game" and not _lb_done:
                _lb()
                _lb_done = True
            layer.draw(dt)
            if _appbar is not None and layer.id in _appbar \
                    and not getattr(_appbar[layer.id], "glass", False):
                self.bar_layer._draw_status_strip("app")    # host guarantee
            _prev_domain = layer.domain
        if _game_open:                              # game was the TOP layer
            _view()
        if self._deferred and self._toast_owed():
            # #184: the acknowledgment frame -- a transition queued this
            # iteration paints its LOADING toast on top of everything; the
            # flush below presents it, and the frame TAIL then runs the
            # transition. The panel retains this frame for the whole stall.
            # A `toast=False` entry (the Editor's owed commit, #154) takes the
            # same tail and no pill -- the kid is not waiting on it.
            if _fold_live:                          # #190: toast paints the root
                _dsf = self._disarm_fn()
                if _dsf is not None:
                    _dsf()
                _fold_live = False
            self._draw_loading_toast()
        # #63: nothing should be left in an auto-batch by the time we present. The cart
        # sprites were flushed at _reset_canvas_state; the console's own chrome draws
        # Images immediately (never queued). This final flush is the last-line guard so
        # a queued run can never survive to the next frame's cls().
        self._flush_batches()
        if self._half is not None:
            self._present_half()
        # Perf HUD (#43/#44): time the panel DMA flush in isolation, then back out
        # the draw span (everything before it this frame). On the host _NullComp the
        # flush is a near-zero no-op (no real panel), so flush reads ~0 and draw ~=
        # total -- the real flush-vs-draw split only shows on device. The flush call
        # is unconditional + identical either way; the timing is two cheap ticks
        # calls gated on perf_hud OR perf_capture (device diag sampling), so the
        # render path itself is unchanged.
        if _perf:
            self._frame_perf_end(_frame_t0)
        else:
            self.comp.flush()
        # We painted this frame: clear the dirty flag and snapshot the pointer state
        # we just drew, so the NEXT frame only repaints if something changes again.
        if self._dirty:
            self._dirty = False
        self._damage_drop()          # an app's `all` from this draw is spent
        mv = _ui_draw.moving_take()
        if mv != self.ui_moving:
            self.ui_moving = mv
        if _ui_draw.again_take():
            self._dirty = True        # a pass owes the frame after it (`AppHost.again`)
        ch = _ui_draw.change_take()
        if ch != self.frame_change:
            self.frame_change = ch
        if covers.take_deferred():
            # A shelf cover build was pushed past this frame's budget -- stay
            # dirty so the remaining covers land on the following frames (the
            # flag is set during the draw, AFTER the gate consumed _dirty).
            self._dirty = True
        if self._frame_requested:
            # A draw asked for a follow-up frame (a coasting kinetic fling,
            # #113) -- same pattern as the covers re-arm above.
            self._frame_requested = False
            self._dirty = True
        if self._ptr_changed():          # rebuilt only when it moved: no tuple
            self._last_ptr = self._ptr_state()   # on a static-pointer frame
        fs.frames_drawn += 1
        if self._deferred:
            # #184: the flush above PRESENTED this frame's LOADING
            # acknowledgment -- now run the queued transition(s) behind it.
            # Last thing in the frame so the stall sits outside every timing
            # bracket (the DEFER diag line names its cost instead).
            self._run_deferred()

    # -- boot logo ------------------------------------------------------------

    def arm_splash(self, ms=None):
        """Show the moybyte boot logo for the next `ms` (default _SPLASH_MS) before the
        launcher appears. Called by the boot entries (device run_desktop, interactive
        host), NOT by construction -- so unit tests that drive frame() see the launcher
        on the first frame."""
        self._splash_until = _ticks_ms() + (int(ms) if ms else _SPLASH_MS)
        self._dirty = True

    def _draw_splash(self):
        """Paint the boot logo on the system canvas (see draw_splash)."""
        draw_splash(self.sys_canvas, pal=splash_pal(self.theme_colors))

    # -- desktop shell drawing (#28) -----------------------------------------

    def _mini_btn(self, label, rect, fill, cv=None, label_rect=None):
        # Shared draw toolkit -- the implementation moved to ui.mini_btn (the
        # 2026-07 kernel-shrink direction); this stays the tested ws entry point.
        _ui_draw.mini_btn(cv if cv is not None else self.canvas, rect, label, fill,
                        label_rect)

    def _btn(self, label, rect, fill, cv=None):
        # Defaults to the GAME canvas (paint/map editors -- a 320x240 viewport); the
        # responsive code/block editors (#39 step 2) pass cv=self.sys_canvas. The
        # implementation (incl. the frozen scale-2 baseline quirk) moved to
        # ui.game_btn; this stays the tested ws entry point.
        _ui_draw.game_btn(cv if cv is not None else self.canvas, rect, label, fill)

    def _icon_btn(self, kind, label, rect, fill, cv=None):
        """A button that leads with an icon glyph (pre-literate) and keeps the
        word as a small secondary cue beside it -- so a reader still gets the
        label and a kid who can't read still gets the picture. Implementation:
        ui.game_icon_btn."""
        _ui_draw.game_icon_btn(cv if cv is not None else self.canvas, rect,
                             kind, label, fill, _ui_draw.glyph_draw)

    def _draw_cursor(self):
        # The pointer lives in SYSTEM-canvas space (it ranges over the panel size),
        # so the cursor draws on the system canvas, on TOP of the composited viewport
        # (#39). Scaled with the font so it stays visible on a big panel; at scale 1
        # / a 320x240 system canvas this is exactly today's 1x cursor on the canvas.
        if self._splash_until is not None:
            return                        # no cursor over the boot logo
        p = self.pointer
        if p is not None and p.visible:
            s = self.look.font_scale
            shape = self.wm.cursor_shape()
            if shape is None:
                self.sys_canvas.spr(CURSOR, p.x, p.y, s)
            else:                         # a resize's arrows, centred on it
                img = RESIZE_CURSORS[shape]
                self.sys_canvas.spr(img, p.x - img.w * s // 2, p.y - img.h * s // 2, s)

    def _glyph(self, kind, rect, c, cv=None, scale=None):
        # Draw a centered icon glyph in color `c`. Defaults to the GAME canvas (the
        # editors/cart-overlay callers); the desktop/system callers pass cv=
        # self.sys_canvas so the glyph follows the system font scale (#39). `scale`
        # overrides that -- what the bar passes so its glyphs fill a chrome-scaled
        # slot (#203). The raster is native/moy_ui's, through `ui.glyph`, which
        # Launcher (canvas-only) is handed too.
        _ui_draw.glyph(cv if cv is not None else self.canvas, kind, rect, c, scale)

    def _wifi_icon_kind(self):
        """The right-zone wifi STATUS glyph (Part 3): "wifi" when the injected wifi
        service reports a live link, "wifi_off" when there's no
        connection, which the look's bar shows as its own glyph and the pixel
        bar as no glyph (the crash strip as a slashed one). Reads the backend's status() -> (connected, ssid, ip); defaults to
        disconnected on no backend / any status error, so the host + tests are
        deterministic (FakeWifi boots disconnected -> "wifi_off"). Folded into the bar's
        cache key so a real connect/disconnect repaints the strip. Only ever called on a
        chrome/tool frame (the bar hides while a GAME plays), so it never touches the #66
        game frame budget."""
        w = self.wifi
        if w is None:
            return "wifi_off"
        try:
            return "wifi" if w.status()[0] else "wifi_off"
        except Exception:  # noqa: BLE001 -- a status hiccup must not blank the bar
            return "wifi_off"

    def _bar_image(self, kind):
        """The cached 16x16 _SheetSprite for top-bar icon `kind`, or None when the
        icon sheet/slot is missing. Memoised per (kind, light) so the SAME image
        object is blitted every frame -- the device caches its RGB565 copy on the
        image, so the bar costs one cached blit per icon (Stage 1's perf goal).

        LIGHT chrome (a light theme variant, `bar_light`): the sheet's untouched
        0 pixels -- invisible on the frozen black bar -- would read as a black
        PLATE on a light band, and white strokes would vanish on cream. So the
        light variant of every icon is derived at build time: the plate is
        remapped to a sentinel index (63 -- outside the 0-15 range icon art is
        authored in) and keyed transparent, white (7) strokes flip to ink-black
        and light-grey (6) detail to dim warm ink. The key CANNOT be 0 itself:
        strokes remapped to black would then erase themselves (the invisible-
        wifi bug, owner report 2026-07-23). The mascot ("moy") keeps its
        authored colors -- only its plate is keyed (via transparent=0, since
        its own outline must stay black and drawn)."""
        light = bool(self.theme_colors.get("bar_light", False))
        key = (kind, light)
        if key in self._bar_img_cache:
            return self._bar_img_cache[key]
        img = None
        sheet = self.look.icon_sheet
        if sheet is not None:
            slot = _ICON.get(kind)
            if slot is not None:
                if not light:
                    img = sheet.tile_image(slot)   # transparent -1
                else:
                    base = sheet.tile_image(slot, transparent=0)
                    if base is not None:
                        if kind == "moy":
                            img = _SheetSprite(base.w, base.h, base.pix, 0)
                        else:
                            pix = []
                            for p in base.pix:
                                if p == 0:
                                    pix.append(63)   # plate -> keyed sentinel
                                elif p == 7:
                                    pix.append(0)    # white strokes -> ink
                                elif p == 6:
                                    pix.append(53)   # grey detail -> dim ink
                                else:
                                    pix.append(p)
                            img = _SheetSprite(base.w, base.h, pix, 63)
        self._bar_img_cache[key] = img
        return img

    def _icon_image_keyed(self, kind):
        """Like _bar_image but with palette index 0 keyed TRANSPARENT -- for drawing
        a bar icon over a non-black surface (the Library shelf's pseudo cards). The
        sheet's untouched pixels are 0: invisible on the black bar, a black plate
        anywhere else. tile_image memoises per (slot, transparent), so this shares
        the sheet's own cache. A slot the theme paints is its own tile."""
        img = self.look.pixel_image(kind)
        if img is not None:
            return img
        sp = self.icon_spec()
        if sp is not None and sp[0] == "pixel":
            img = _iconsets.pixel_image(kind, sp[2])
            if img is not None:
                return img
        sheet = self.look.icon_sheet
        if sheet is None:
            return None
        slot = _ICON.get(kind)
        if slot is None:
            return None
        return sheet.tile_image(slot, transparent=0)

    def icon_spec(self):
        """The live theme's icon set, (kind, set, swap), or None where it
        names none or the image carries none of it (runtime/toolkit/iconsets.py,
        `carried`): the look's, set when it publishes."""
        return self.look.icons

    def draw_icon(self, kind, rect, ink, cv=None):
        """Draw icon `kind` of the live theme centred in `rect` in colour index
        `ink` (a glyph_draw for ui.button and ui.tab): the theme's own tile
        where its look paints the slot (`Appearance.pixel_draw`), else its icon
        set's. False, drawing nothing, where the theme names no set or its set
        has no such icon on this image."""
        cv = cv if cv is not None else self.sys_canvas
        if self.look.pixel_draw(cv, kind, rect):
            return True
        sp = self.icon_spec()
        if sp is None:
            return False
        return _iconsets.draw(cv, kind, rect, ink, sp)

    def _icon(self, kind, x, y, cv=None, scale=None, ink=None):
        """Draw the top-bar icon `kind` at (x, y), in a 16px box at the scale:
        from the live theme's icon set in `ink` (default `chrome_ink`) where it
        names one (draw_icon), else a 16x16 sprite of the bar's IconSheet. The
        sheet's is the themeable replacement for _glyph on the bar; falls back to _glyph (the 12x12
        bitmap, centered) when the icon sheet/slot is missing, so the bar never crashes
        on a half-wired theme. cv defaults to the system canvas (the bar lives there);
        the running-cart bar passes the game canvas explicitly. The icon scales with
        the canvas's system font scale (#39) so it grows on a larger panel -- the GAME
        canvas is always scale 1, so the cart bar is byte-identical. `scale` overrides
        that with the layout's CHROME scale (#203), which is what the responsive bar
        passes so the sprite fills the tap target the layout sized."""
        cv = cv if cv is not None else self.sys_canvas
        fs = int(scale) if scale else getattr(cv, "font_scale", 1)
        if fs < 1:
            fs = 1
        box = _BAR_ICON * fs
        if self.look.pixel_draw(cv, kind, (x, y, box, box)):
            return
        sp = self.look.icons
        if sp is not None and _iconsets.draw(
                cv, kind, (x, y, box, box),
                self.theme_colors.get("chrome_ink", 7) if ink is None else ink, sp):
            return
        img = self._bar_image(kind)
        if img is not None:
            cv.spr(img, x, y, fs)                        # 16px art upscaled by that scale
        else:
            self._glyph(kind, (x, y, _BAR_ICON * fs, _BAR_ICON * fs),
                        self.theme_colors.get("chrome_ink_dim", 6), cv, fs)


def wire_workstation_core(ws, store, carts_root, make_api, wifi,
                          audio_out=None, runtimes=None, can_manage=None,
                          before_slim=None, pointer=None, inp=None,
                          keyboard=None, first_variant=None):
    """The board-agnostic Workstation service wiring, in the ONE canonical order
    every backend used to hand-copy (host build_workstation + both boards'
    run_desktop -- the P4 literally carried a "same order as host" comment).
    The caller builds its board-specific backends (api/audio/wifi/lua/SD/OTA)
    and hands them in; board glue that must land between the store hookup and
    the #66 cart diet (the T-Deck's _with_sd + OTA updater) goes through
    `before_slim(ws)`. can_manage defaults to "the store root is known"; the
    host passes True. `first_variant` ("dark" or "light") is the theme variant
    a console with no saved `theme_variant` row boots in -- a page's colour
    scheme; None keeps `DEFAULT_VARIANT`, and a saved row always wins. Ends with the three boot loads (system.json settings /
    icon theme / achievements) -- install a WindowedWM AFTER this returns, so
    the persisted font scale is applied before the root layout context is
    captured (#73/#58)."""
    ws.make_api = make_api
    if audio_out is not None:
        ws.audio_out = audio_out
    if runtimes:
        ws.runtimes = dict(runtimes)
    ws.carts_store = store
    ws.carts_root = carts_root
    ws.can_manage = (carts_root is not None) if can_manage is None else can_manage
    ws.store.bind()
    ws.wifi = wifi
    if before_slim is not None:
        before_slim(ws)
    ws.carts.slim()   # #66 live-set diet: heavy payloads reload from the store
    if pointer is not None:
        ws.set_pointer(pointer)
        if inp is not None:
            inp.pointer = pointer   # touch-driven carts read it via the api touch()
    # Multiplayer (#65): attach the PlayerRouter to the InputState so btn(name,
    # player)/players() resolve extra controller slots. Slot 0 stays the console's
    # own InputState (zero regression); extra slots stay empty until a transport
    # registers one. Idempotent -- never re-wrap on a re-wire.
    if inp is not None and getattr(inp, "players", None) is None:
        inp.players = PlayerRouter(inp)
    if keyboard is not None:
        ws.keyboard = keyboard      # lets the code editor switch to text (ASCII) mode
        ws.sync_keyboard_mode()
    if first_variant in THEME_VARIANTS:
        ws.look.theme_variant = first_variant   # load_system's absent-row default
    ws.load_system()            # #28: system.json, the theme and its wallpaper
    try:                            # the console's level, the Settings row
        console_volume(int(ws.system.get("volume", 7)))
    except (TypeError, ValueError):
        pass
    ws.look.load_icon_sheet()       # Stage 1: the 16x16 bar IconSheet (theme or baked)
    ws.load_achievements()          # #21: unlocked badges survive reboots
