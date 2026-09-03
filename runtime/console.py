"""The shared Moybyte v0.4 console UI -- launcher + desktop + cards/code/paint
editors + the trackball/touch Pointer. Backend-agnostic: it draws through an
injected `canvas` (host Canvas or device DeviceCanvas -- identical TIC-80 API +
petme128 font) and persists through an injected cart store + make_api, so the
host sim and the T-Deck render the SAME pixels from this one file.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/
tree so the device freezes it (same pattern as editors.py). Keep it dependency-
free apart from the shared editor cores below.
"""

import time

from editors import CodeEditor, SpriteSheet, _SheetSprite
# (the #111 op-history core is history_router.py's import now, not this file's --
# #209 landing E took the code tab's History and its typing-burst codec with it)
# The block editor's UI layer (issue #29 Part 2, extracted from this file): the
# structured-outline screen + BlockLayout (its responsive geometry, #39 step 2) +
# the module constants/sentinels its rows/menu render. Re-exported under their
# pre-extraction names (BlockLayout, _BLK_*, _NEW_VAR_*, _NEW_LIST_*,
# _NUM_LITERAL_*) so anything doing `console.X` / `C.X` for one of them -- tests
# included -- still resolves. See block_editor_ui.py's module docstring for why
# it takes NAMES/_in/_err_text/_clamp_scroll as constructor args instead of
# importing them back from here (a real circular import: this module builds the
# one BlockEditorUI instance a Workstation holds). Same bare-or-package fallback
# as the _blocks_mod import just below (host tests that load console.py directly
# without the runtime/host_app.py aliasing, or one that hand-registers editors/
# audio/blocks/console like tests/test_micropython_spike.py's _load_moy_runtime).
try:
    from block_editor_ui import (
        BlockEditorUI, BlockLayout,
        _BLK_HINT_Y, _BLK_X0, _BLK_W, _BLK_Y0, _BLK_ROW_H, _BLK_INDENT, _BLK_ROWS,
        _BLK_AREA, _BLK_ADD, _BLK_DEL, _BLK_UP, _BLK_DN, _BLK_CODE,
        _BLK_MENU, _BLK_MENU_ROW_H, _BLK_MENU_ROWS, _BLK_KBD,
        _BLK_KBD_DEL, _BLK_KBD_OK, _BLK_KBD_X, _BLK_NUM, _BLK_NUM_GX, _BLK_NUM_GY,
        _BLK_NUM_BW, _BLK_NUM_BH, _BLK_NUM_BPR, _BLK_NUM_KEYS, _BLK_NUM_DEL,
        _BLK_NUM_BLOCK, _BLK_NUM_OK, _BLK_NUM_X, _CAT_LABEL, _NEW_VAR_ITEM,
        _NEW_VAR_LABEL, _NEW_LIST_ITEM, _NEW_LIST_LABEL, _NUM_LITERAL_ITEM,
        _NUM_LITERAL_LABEL, _blk_plain_label, _BLK_HINTS,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.block_editor_ui import (
        BlockEditorUI, BlockLayout,
        _BLK_HINT_Y, _BLK_X0, _BLK_W, _BLK_Y0, _BLK_ROW_H, _BLK_INDENT, _BLK_ROWS,
        _BLK_AREA, _BLK_ADD, _BLK_DEL, _BLK_UP, _BLK_DN, _BLK_CODE,
        _BLK_MENU, _BLK_MENU_ROW_H, _BLK_MENU_ROWS, _BLK_KBD,
        _BLK_KBD_DEL, _BLK_KBD_OK, _BLK_KBD_X, _BLK_NUM, _BLK_NUM_GX, _BLK_NUM_GY,
        _BLK_NUM_BW, _BLK_NUM_BH, _BLK_NUM_BPR, _BLK_NUM_KEYS, _BLK_NUM_DEL,
        _BLK_NUM_BLOCK, _BLK_NUM_OK, _BLK_NUM_X, _CAT_LABEL, _NEW_VAR_ITEM,
        _NEW_VAR_LABEL, _NEW_LIST_ITEM, _NEW_LIST_LABEL, _NUM_LITERAL_ITEM,
        _NUM_LITERAL_LABEL, _blk_plain_label, _BLK_HINTS,
    )

# The map (tilemap) editor's UI layer (issue #32, extracted from this file): the
# panned view + tile palette + pan/zoom + gesture handling. Re-exported under
# their pre-extraction names (_MV_*, _TP_*, _MAP_ZOOM/_MAP_ERASE/
# _MAP_CLOSE/_MAP_PAN_THRESH, _PAN_*) for the same `console.X`/`C.X` reasons as
# the block editor above, with the same bare-or-package fallback. (_MAP_SAVE was
# removed with the SAVE button, #111.)
try:
    from map_editor_ui import (
        MapEditorUI,
        _MV_X0, _MV_Y0, _MV_AVAIL_W, _MV_AVAIL_H,
        _MV_ZOOMS, _MAP_ZOOM, _MAP_SIZE, _TP_X0, _TP_Y0,
        _TP_CELL, _TP_COLS, _TP_ROWS, _TP_PAGE, _TP_AREA, _TP_PREV, _TP_NEXT,
        _TP_SKY, _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN, _MAP_ERASE,
        _MAP_CLOSE, _MAP_PAN_THRESH,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.map_editor_ui import (
        MapEditorUI,
        _MV_X0, _MV_Y0, _MV_AVAIL_W, _MV_AVAIL_H,
        _MV_ZOOMS, _MAP_ZOOM, _MAP_SIZE, _TP_X0, _TP_Y0,
        _TP_CELL, _TP_COLS, _TP_ROWS, _TP_PAGE, _TP_AREA, _TP_PREV, _TP_NEXT,
        _TP_SKY, _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN, _MAP_ERASE,
        _MAP_CLOSE, _MAP_PAN_THRESH,
    )

# The scene placement editor's UI layer (#85 Stage 2, its own module from birth
# -- the map editor's extraction shape): the WYSIWYG placed-actor editor.
try:
    from scene_editor_ui import SceneEditorUI
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.scene_editor_ui import SceneEditorUI

# The music/sound editor's UI layer (issue #50, extracted from this file): the
# tracker-style step editor + preview. Re-exported under its pre-extraction names
# (_MU_*, _mu_note_name, _mu_pad_rect) for the same `console.X`/`C.X` reasons as
# the block/map editors above, with the same bare-or-package fallback.
try:
    from music_editor_ui import (
        MusicEditorUI,
        _MU_TITLE_Y, _MU_VIEW, _MU_LIST_X, _MU_LIST_Y0, _MU_ROW_H, _MU_ROWS,
        _MU_LIST_W, _MU_LIST_AREA, _MU_OBJ_PREV, _MU_OBJ_NEXT, _MU_PAD_X,
        _MU_PAD_Y, _MU_PAD_W, _MU_PAD_H, _MU_PAD_GAP, _MU_SPEED_DN, _MU_SPEED_UP,
        _MU_PLAY, _MU_LOOP, _MU_NOTE_NAMES, _MU_WAVE_LABELS,
        _mu_note_name, _mu_pad_rect,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.music_editor_ui import (
        MusicEditorUI,
        _MU_TITLE_Y, _MU_VIEW, _MU_LIST_X, _MU_LIST_Y0, _MU_ROW_H, _MU_ROWS,
        _MU_LIST_W, _MU_LIST_AREA, _MU_OBJ_PREV, _MU_OBJ_NEXT, _MU_PAD_X,
        _MU_PAD_Y, _MU_PAD_W, _MU_PAD_H, _MU_PAD_GAP, _MU_SPEED_DN, _MU_SPEED_UP,
        _MU_PLAY, _MU_LOOP, _MU_NOTE_NAMES, _MU_WAVE_LABELS,
        _mu_note_name, _mu_pad_rect,
    )

# The perf HUD's rendering layer (#43/#44, extracted from this file): the
# bottom-right FPS chip + optional frame-time breakdown + its tap target. The
# perf *query* API (perf_sample/perf_breakdown/...) stays on Workstation (the
# device diag's measurement contract); only the drawing moves here. Same
# bare-or-package fallback as the editors above.
try:
    from perf_hud import PerfHud
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.perf_hud import PerfHud

# The firmware-update (OTA) SCREEN's UI layer (#53, extracted from this file): the
# confirm/download/install/done lifecycle + its pump + drawing. The update
# *queries* + channel config (_update_available/_online_update_available/
# _ota_channel/_cycle_channel) stay on Workstation (Settings + draw paths + tests
# reference them). Same bare-or-package fallback as the editors above.
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

# system.json's owner (#209 landing B, system_store.py): the settings dict
# `ws.system` aliases, the one persist funnel behind every Settings toggle, and
# the achievements list's store halves -- over a StoreHandle that reads the
# store/root/can_manage/_with_sd guard through `ws` per call. What APPLIES the
# settings (load_system's cascade) stays kernel policy, below.
try:
    from system_store import StoreHandle, SystemStore
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.system_store import StoreHandle, SystemStore

# The #111 undo ROUTER (#209 landing E, history_router.py): the bar UNDO/REDO
# pair, the code tab's typing burst, the tab-scoped journal walk they fall
# through to, and the idle-typing autosave the frame loop ticks. Takes the same
# StoreHandle -- the walk re-derived that guard by hand until this landing.
try:
    from history_router import HistoryRouter
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.history_router import HistoryRouter

# The ≡ dropdown / system menu's UI layer (#52, extracted from this file): the row
# builder + per-item actions + drawing. The sysmenu Popup, _about flag, reboot_hook
# and toggle_sysmenu() stay on Workstation (tested ws. surface + device). Same
# bare-or-package fallback as the editors above.
try:
    from system_menu_ui import SystemMenuUI
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.system_menu_ui import SystemMenuUI

# The Easter-egg subsystem + achievement/egg drawing (#21, extracted from this
# file): the 3 hidden eggs + their state + _draw_egg/_draw_confetti/
# _draw_achievements. The achievement CORE (ach, show_achievements,
# load_achievements/_achievement_unlocked) stays on Workstation (tested ws.ach.*
# + device ws.load_achievements()), and so do the three overlay DEADLINES those
# objects push into at event time (#209 landing B). Same bare-or-package fallback.
try:
    from achievements_ui import AchievementsUI
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.achievements_ui import AchievementsUI

# The Layer protocol + the self-contained surface adapters (extracted from this
# file -- see layers.py): the router builds the stack from these. layers.py is a
# dependency-free leaf (every class references only its self.ws back-ref), so there
# is no circular import back into console. Same bare-or-package fallback as above.
try:
    from layers import (
        _LegacyLayer, _PlayerLayer, _BlocksLayer, _UpdateLayer, _MapLayer, _MusicLayer,
        _SceneLayer, _PerfLayer, _AchOverlayLayer, _SysMenuLayer, _AboutLayer,
        _WebConsoleLayer)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layers import (
        _LegacyLayer, _PlayerLayer, _BlocksLayer, _UpdateLayer, _MapLayer, _MusicLayer,
        _SceneLayer, _PerfLayer, _AchOverlayLayer, _SysMenuLayer, _AboutLayer,
        _WebConsoleLayer)

# The unified top bar surface (#46, extracted from this file -- see
# bar_layer.py). bar_layer.py is the SINGLE SOURCE of the bar geometry constants
# (_STATUS_H / _BAR_* / the tool-switcher button rects); they're imported
# back here (re-exported under the same names) because console.py's own Layout + a few
# derived constants + the golden harness/tests reference them as console._X -- rather
# than duplicate them (drift), the same way block_editor_ui.py owns its _BLK_*. NAMES
# and _in are injected into the one BarLayer a Workstation builds (circular-import dodge).
try:
    from bar_layer import (
        BarLayer, _BAR_ICON, _BAR_GAP, _BAR_STRIDE, _BAR_Y, _SYSMENU_BTN, _HOME_BTN,
        _MENU_BTN, _PAINT_BTN, _MAP_BTN, _BLOCKS_BTN, _MUSIC_BTN, _BAR_BATT, _BAR_WIFI,
        _BAR_CLOCK, _STATUS_H)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import (
        BarLayer, _BAR_ICON, _BAR_GAP, _BAR_STRIDE, _BAR_Y, _SYSMENU_BTN, _HOME_BTN,
        _MENU_BTN, _PAINT_BTN, _MAP_BTN, _BLOCKS_BTN, _MUSIC_BTN, _BAR_BATT, _BAR_WIFI,
        _BAR_CLOCK, _STATUS_H)

# The "Make it mine" config-card editor surface (#3/#15, extracted -- see
# cards_layer.py). cards_layer.py is the single source of the card geometry constants
# (_CARD_*); imported back here so tests + a couple of console call sites resolve
# console._X. Its own GO/CODE/CLOSE buttons were dissolved into the unified bar
# (fix B); CART STATE stays on Workstation: ws.config / ws.apply / ws.adjust; CardsLayer
# mutates ws.config in place + dispatches through them.
try:
    from cards_layer import (
        CardsLayer, _CARD_X, _CARD_W, _CARD_Y0,
        _CARD_H, _CARD_VIEW_BOTTOM, _CARD_SCROLL_UP, _CARD_SCROLL_DN)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cards_layer import (
        CardsLayer, _CARD_X, _CARD_W, _CARD_Y0,
        _CARD_H, _CARD_VIEW_BOTTOM, _CARD_SCROLL_UP, _CARD_SCROLL_DN)

# The sprite/icon PAINT editor surface (#4/#30, extracted -- see paint_layer.py). ONE
# renderer serves both the cart sprite sheet (menu_view=="paint") and the system icon
# sheet (menu_view=="theme", EDIT ICONS), keyed on ws._editing_icons. paint_layer.py is
# the single source of the paint geometry constants (_PG_*/_SW*/_SPR_*/_PAINT_*),
# imported back here for tests + tools. The SHEETS + ws.paint handle + save persistence
# stay on Workstation; PaintLayer reads them + dispatches GET/PUT/CLOSE to ws (SAVE
# removed with the button, #111 -- CLOSE + every other exit path hard-commit instead).
try:
    from paint_layer import (
        PaintLayer, ThemeLayer, _PG_X0, _PG_Y0, _PG_CELL, _PG_SPAN, _PG_AREA, _SW_X0,
        _SW_Y0, _SW, _SW_COLS, _SW_AREA, _SPR_PREV, _SPR_NEXT, _PAINT_SIZE,
        _PAINT_CLOSE, _PAINT_GET, _PAINT_PUT)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.paint_layer import (
        PaintLayer, ThemeLayer, _PG_X0, _PG_Y0, _PG_CELL, _PG_SPAN, _PG_AREA, _SW_X0,
        _SW_Y0, _SW, _SW_COLS, _SW_AREA, _SPR_PREV, _SPR_NEXT, _PAINT_SIZE,
        _PAINT_CLOSE, _PAINT_GET, _PAINT_PUT)

# The Settings app surface (#28/#39/#53, extracted -- see settings_layer.py). The
# aggregator: rows + scroll + drawing move to SettingsLayer, which owns NO config -- it
# reads ws state (system/ws.look/diag_live) and dispatches every mutation to the ws
# setters; the wallpaper cluster is single-sourced on ws.look (the launcher shares
# that backdrop). settings_layer.py is the single source of the _SET_* geometry
# constants (also used by console's Layout), imported back here for Layout + tests.
# SETTINGS_TOGGLES rides the same import: it is the ONE declaration of the
# persisted ON/OFF settings (#209 section 7), and this file is what reads it for
# the flat defaults, the boot apply and the shared persistence tail.
try:
    from settings_layer import (
        SettingsLayer, SETTINGS_TOGGLES, _SET_X, _SET_W, _SET_ROW_Y0, _SET_ROW_H,
        _SET_BACK, _SET_ACH, _SET_TITLE_HIT)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.settings_layer import (
        SettingsLayer, SETTINGS_TOGGLES, _SET_X, _SET_W, _SET_ROW_Y0, _SET_ROW_H,
        _SET_BACK, _SET_ACH, _SET_TITLE_HIT)

# The Python code editor surface (#24/#39, extracted -- see code_layer.py). CodeLayer
# owns the full-screen text view + drawing + code-UI state; the shared ws.editor handle
# (like ws.paint) + save_code/run_code + the code-error state + code_layout stay on ws.
# code_layer.py is the single source of the code geometry constants (_CODE_*/_ED_*/
# _SYM_*/_CODE_SYMBOLS) + the MicroPython-safe syntax highlighter, imported back here for
# console's CodeLayout + the crash panel (_CODE_LH) + tests.
try:
    from code_layer import (
        CodeLayer, _CODE_X0, _CODE_Y0, _CODE_LH, _CODE_AREA,
        _CODE_SYMBOLS, _SYM_Y, _SYM_H, _SYM_CELL, _SYM_AREA)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import (
        CodeLayer, _CODE_X0, _CODE_Y0, _CODE_LH, _CODE_AREA,
        _CODE_SYMBOLS, _SYM_Y, _SYM_H, _SYM_CELL, _SYM_AREA)

# Self-contained support widgets (extracted -- see widgets.py): the cursor blittable
# _Blit, the Pointer cursor, the Achievements milestone tracker (+ its ACHIEVEMENTS
# catalog), Pmem (cart persistent RAM), the _SilentAudio no-op backend, and the reusable
# Popup dropdown. A dependency-free leaf; imported back here so console.Pointer /
# console.Popup / console.ACHIEVEMENTS / ... resolve for Workstation + host_app + tests.
try:
    from widgets import (
        _Blit, Pointer, Achievements, Pmem, Clipboard, _SilentAudio, Popup, ACHIEVEMENTS,
        TOAST_MS, _PLAY_GOAL, _POPUP_X, _POPUP_Y, _POPUP_W, _POPUP_ROW_H, _POPUP_PAD_X,
        _POPUP_SEP_H)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import (
        _Blit, Pointer, Achievements, Pmem, Clipboard, _SilentAudio, Popup, ACHIEVEMENTS,
        TOAST_MS, _PLAY_GOAL, _POPUP_X, _POPUP_Y, _POPUP_W, _POPUP_ROW_H, _POPUP_PAD_X,
        _POPUP_SEP_H)

# The desktop wallpaper backdrop component (#28, extracted -- see wallpaper.py). The
# SHARED backdrop the launcher home + Settings both draw (ws.wallpaper.draw). It owns
# the rendering + the compiled-cart cache; the CHOICE + the picker/query API are
# ws.look's (appearance.py -- select_wallpaper drives the component).
try:
    from wallpaper import Wallpaper
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wallpaper import Wallpaper

try:
    from artwork import ArtworkService, PaintAppLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.artwork import ArtworkService, PaintAppLayer

try:
    from appearance_app import AppearanceAppLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.appearance_app import AppearanceAppLayer

try:
    from writer_app import WriterAppLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.writer_app import WriterAppLayer

try:
    from calc_app import CalcAppLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.calc_app import CalcAppLayer

try:
    from app_decls import APPS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_decls import APPS

# The narrowed shell interface a SYSTEM APP is handed (ui_refactor_2026-08
# Phase 6). An app no longer holds `ws`; it holds an AppContext carrying only
# the roles its NEEDS tuple declares -- see runtime/app_context.py.
try:
    from app_context import AppContext
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_context import AppContext

# Crash isolation for content the shell runs on the kid's behalf (#160,
# ui_refactor_2026-08 Phase 8): three failed opens and an app cart stops being
# offered -- see runtime/crash_guard.py for why an in-process except cannot do
# this job.
try:
    from crash_guard import CrashGuard
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.crash_guard import CrashGuard

# Appearance (appearance.py): `ws.look`, the LOOK collaborator (#209 landing D)
# -- theme + variant, the widget skin, the system font scale, the wallpaper
# choice and the top-bar icon sheet. It is also the module that installs a skin
# (`skin.use`), for the same reason it installs a theme: the choice is a
# persisted setting and the installed skin is process-wide state inside `ui`.
# Every surface just draws through `ui` and never learns a skin exists.
try:
    from appearance import Appearance
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.appearance import Appearance

# USER APPS (#181, ui_refactor_2026-08 Phase 7): the permission-keyed filter
# over AppContext that a `type: "app"` CART is handed. The shell needs only its
# identity helper here; the Player is what builds the namespace.
try:
    import system_api
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import system_api


def _resolve_app_entry(entry):
    """Resolve an app declaration's "module:Class" to the class itself.

    Two namespaces, as everywhere in this tree: the boards and the wasm head
    freeze `runtime/` FLAT (`import writer_app`), the host imports the package
    (`runtime.writer_app`). Same ladder every module header here writes by
    hand -- resolved from data instead of once per app.
    """
    mod_name, _, cls_name = entry.partition(":")
    try:
        mod = __import__(mod_name, None, None, (cls_name,))
    except ImportError:
        mod = __import__("runtime." + mod_name, None, None, (cls_name,))
    return getattr(mod, cls_name)

try:
    from artwork import PaintAppLayout
    from appearance_app import AppearanceLayout
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.artwork import PaintAppLayout
    from runtime.appearance_app import AppearanceLayout

try:
    from storybook_app import StorybookAppLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.storybook_app import StorybookAppLayer

try:
    from sheets_app import SheetsAppLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.sheets_app import SheetsAppLayer

try:
    from files_app import FilesAppLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.files_app import FilesAppLayer

# The desktop home / launcher surface (#28, extracted -- see launcher_layer.py): the
# Launcher grid CLASS (its instance stays ws.launcher, the single source everything
# reads) + LauncherHomeLayer (the "launcher" content Layer -- home composition + grid
# nav). Launcher takes NAMES + _blit_glyph injected for its tile art; ws.open() stays.
try:
    from launcher_layer import (Launcher, LauncherHomeLayer, EditorPickerLayer,
                                make_tile, new_tile, MAKE_TILE_TYPE, NEW_TILE_TYPE,
                                PSEUDO_TILE_TYPES)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.launcher_layer import (Launcher, LauncherHomeLayer, EditorPickerLayer,
                                        make_tile, new_tile, MAKE_TILE_TYPE,
                                        NEW_TILE_TYPE, PSEUDO_TILE_TYPES)

try:
    import ui as _uimod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _uimod


# Project (project.py): the open cart's DATA + commit_* verbs; its six data
# fields are forwarding properties here so the surface files + tests keep the
# ws.<X> spelling.
try:
    from project import Project
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.project import Project

# Player (player.py): the run-loop black box; its cart-run fields
# (cart_error/crash_line/ns/...) are forwarding properties here for the same
# reason. Exit is hold-BACKSPACE (games) / the bar X (tools) -- the #71 pause
# machinery is retired, do not reintroduce it.
try:
    from player import Player
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.player import Player

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

# The block vocabulary/compiler (#29). Imported under whichever name it's known by:
# bare `blocks` on the device (frozen top-level) and on the host once host_app has
# aliased it, or `runtime.blocks` when a test loads console/moy_runtime directly
# without that alias (the device path is plain `import blocks`). Mirrors
# moy_carts._import_blocks so neither module hard-depends on import order.
try:
    import blocks as _blocks_mod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import blocks as _blocks_mod


# The console's stateless base layer -- the MOY64 palette (NAMES/color), the responsive
# Layout/CodeLayout geometry (#39), the icon-glyph vocabulary (_GLYPHS/_blit_glyph), the
# themeable top-bar IconSheet slot map + art (_ICON/_ICON_ART),
# the cursor sprite (CURSOR), and the small pure helpers (_in/_clamp_scroll/_cursor_delta/
# _ticks_*/_err_text/_from_ascii) -- now live in chrome.py (extracted so the Workstation
# kernel is alone in this file). Imported back + re-exported under the pre-extraction names
# so every `console.X` and bare reference below -- tests included -- still resolves. chrome.py
# is a leaf (it imports only editors/widgets + surface geometry constants, none of which
# import back), so there is no cycle. Same bare-or-package fallback as the surfaces above.
try:
    from chrome import (
        _ticks_ms, _ticks_us, _ticks_diff, _err_text, _from_ascii, CURSOR, NAMES, color,
        _ICON_COLS, _ICON_ROWS, _ICON_W, _ICON_H, _ICON_GAP_X,
        _ICON_GAP_Y, _ICON_X0, _ICON_Y0, _ICON_BOX, _PAGE_PREV, _PAGE_NEXT,
        _CURSOR_BASE, _CURSOR_ACCEL,
        _BASE_W, _BASE_H, _FONT_W, Layout, CodeLayout, _GLYPH_SIZE, _GLYPHS,
        _blit_glyph, _ICON, _ICON_ART, _nibble,
        _cursor_delta, _clamp_scroll, _in, _SPLASH_MS,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (
        _ticks_ms, _ticks_us, _ticks_diff, _err_text, _from_ascii, CURSOR, NAMES, color,
        _ICON_COLS, _ICON_ROWS, _ICON_W, _ICON_H, _ICON_GAP_X,
        _ICON_GAP_Y, _ICON_X0, _ICON_Y0, _ICON_BOX, _PAGE_PREV, _PAGE_NEXT,
        _CURSOR_BASE, _CURSOR_ACCEL,
        _BASE_W, _BASE_H, _FONT_W, Layout, CodeLayout, _GLYPH_SIZE, _GLYPHS,
        _blit_glyph, _ICON, _ICON_ART, _nibble,
        _cursor_delta, _clamp_scroll, _in, _SPLASH_MS,
    )


_SPLASH_IMG = None


def splash_image():
    """The Moy mascot as a 16x16 blittable built straight from _ICON_ART with REAL
    transparency ("." -> -1), cached. Not the icon-sheet tile: an IconSheet is a
    solid indexed grid where a blank pixel is index 0 (black) -- which is also Moy's
    outline colour, so a sheet blit can't tell the outside from the outline and
    boxes the mascot. Building the image here keeps the outline AND lets the dark
    field (and the corner bite) show through."""
    global _SPLASH_IMG
    if _SPLASH_IMG is None:
        art = _ICON_ART.get("moy", ())
        pix = []
        for ly in range(16):
            row = art[ly] if ly < len(art) else ""
            for lx in range(16):
                pix.append(_nibble(row[lx]) if lx < len(row) else -1)
        _SPLASH_IMG = _SheetSprite(16, 16, pix, -1)
    return _SPLASH_IMG


def draw_splash(cv, frac=None, status=None):
    """Paint the boot logo: 'Moy' (the moybyte mascot) scaled up over a dark field,
    with the two-tone `moybyte` wordmark below. Drawn on the SYSTEM canvas (like the
    launcher) so it fills the real panel; host and device share this one path.

    `frac` (0..1) and `status` add a progress bar and a line of text under the
    wordmark, which is how a board that boots slowly says so -- the P4's
    full-erase boot spends 17 of its 25 seconds seeding cartridges, long before
    a Workstation exists to own the screen. Hence a free function taking a
    canvas rather than a Workstation method: the loading screen and the boot
    logo have to BE the same picture, or the machine appears to start twice.
    """
    W, H = cv.w, cv.h
    cv.cls(NAMES["dark_blue"])
    scale = min(W, H) // 56                    # Moy ~1/4 of the panel, reflows with size
    if scale < 3:
        scale = 3
    side = 16 * scale
    # Wordmark is drawn at text scale 1: the device's framebuf text is a fixed 8px
    # and ignores a scale arg, so scale-1 is the ONLY size that renders identically
    # on host and device (host==device parity). Kept small + centred under Moy.
    word = 8                                   # one 8px char cell wide
    gap = 10
    block_h = side + gap + word
    # The bar and its label extend the block DOWNWARD, so the logo keeps its
    # own vertical centring when there is no progress to report.
    bar_h, bar_gap = 10, 18
    if frac is not None:
        block_h += bar_gap + bar_h
    if status:
        block_h += bar_gap + word
    top = (H - block_h) // 2
    cv.spr(splash_image(), (W - side) // 2, top, scale)
    wy = top + side + gap
    # Two-tone wordmark: "moy" in cream, "byte" in the mascot's indigo body colour.
    tw = 7 * word                             # "moybyte" is 7 chars
    wx = (W - tw) // 2
    cv.print("moy", wx, wy, NAMES["white"], 1)
    cv.print("byte", wx + 3 * word, wy, NAMES["indigo"], 1)
    y = wy + word
    if frac is not None:
        bar_w = W // 3
        bx, by = (W - bar_w) // 2, y + bar_gap
        # TIC-80 naming, and the trap in it: rect is FILLED and rectb is the
        # outline -- there is no rectfill on the device canvas.
        cv.rectb(bx - 2, by - 2, bar_w + 4, bar_h + 4, NAMES["dark_grey"])
        fill = int(bar_w * (frac if frac < 1 else 1))
        if fill > 0:
            cv.rect(bx, by, fill, bar_h, NAMES["yellow"])
        y = by + bar_h
    if status:
        cv.print(status, (W - len(status) * word) // 2, y + bar_gap,
                 NAMES["light_grey"], 1)


def _ema(cur, sample):
    """One-pole EMA (alpha 0.15) with a <=0 "unseeded" bootstrap -- the perf
    readouts' smoothing, written once (frame() applies it to ~10 fields)."""
    return float(sample) if cur <= 0 else cur + (sample - cur) * 0.15

# Frame pacing knob (#63): True locks GAME carts to a steady cadence (30 default,
# manifest "fps": 60 opt-out); False runs everything uncapped at the loop's own
# fps cap -- the measurement mode (owner call 2026-07-08: we want the REAL
# per-cart numbers on the glass, and #66 is fed from them).
#
# RE-AFFIRMED ON PRODUCT GROUNDS 2026-08-19 (owner): uncapped-by-default is
# not just the measurement mode, it is the intended shipping behavior --
# "if no fps set, it should be 60; 30 is available via FRAMESKIP always."
# The 30-lock is the governor/frameskip's business, chosen per device in
# Settings, never the silent default. (This footnote exists because the
# docstring below once read as "games default to 30" and a same-day A/B
# verdict was misworded off the back of it.)
FPS_GOVERNOR = False

class Workstation:
    def __init__(self, comp, canvas, input, carts=None, sys_canvas=None,
                 font_scale=1):
        # Built in five ordered stages (each a method so the constructor reads
        # as a table of contents). The ORDER is load-bearing: the WM must exist
        # before anything reads/writes `screen`, Project/Player/EditorApp before
        # anything sets their forwarded fields, and the layer stack last.
        self._init_canvases(comp, canvas, sys_canvas, font_scale)
        self._init_components(input, carts)
        self._init_state()
        self._init_perf()
        self._init_overlays()
        # The compositor/router layer stack (docs/history/shell_layers_refactor_v1.md). Built
        # once here; _visible_stack()/_draw_stack() z-order + gate them per frame.
        self._build_layers()

    def _init_canvases(self, comp, canvas, sys_canvas, font_scale):
        """The two-domain canvas seam (#39), the WM, the theme and the responsive layouts."""
        self.comp = comp
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
        self._run_canvas = None            # the bound small canvas, while a run holds it
        self._run_canvas_stock = None      # what self.canvas was before the bind
        self._run_canvas_shared = False    # True when the bind promoted stock to system
        self._run_canvas_cache = {}        # {(w, h): canvas} -- 3 sizes max, reused
        # A RESPONSIVE app cart (#181) draws on the SYSTEM canvas instead of the
        # fixed game one; a plain attribute (never a property -- the app-context
        # perf convention) so the chrome can read it once per frame. False for
        # every game, every fixed app cart, and every shipped system app, which
        # is what keeps the pixel goldens where they are.
        self.app_full_canvas = False
        # The LOOK (#209 landing D, appearance.py): theme + variant, the widget
        # skin, the requested system font scale, the wallpaper choice and the
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
        self.wm = FullscreenStackWM(self)
        # windowed_chrome is a PROPERTY (world-aware, two-worlds #105): it is
        # True only while the windowed WM's DESK (the make world) is open --
        # see the property below the screen projection.
        self.layout = Layout(self.sys_canvas.w, self.sys_canvas.h,
                             self.look.effective_font_scale())
        # Responsive editor geometry (#39 step 2): the code + block editors now draw
        # on the SYSTEM canvas at native size, so their layout (visible cols/rows,
        # button rects, palette/menu) derives from (w, h, font_scale) -- exactly the
        # _base-verbatim pattern Layout uses for the desktop. Rebuilt by _relayout on
        # a size/font change. (Sprite/paint + map editors stay a 320x240 viewport --
        # step 3.)
        self.code_layout = CodeLayout(self.sys_canvas.w, self.sys_canvas.h,
                                      self.look.effective_font_scale())
        # The block editor's UI (issue #29 Part 2, extracted from this class -- see
        # block_editor_ui.py): one instance, built once here and delegated to from
        # handle_input/handle_pointer/frame's menu_view == "blocks" branches plus
        # set_menu_view/_relayout/_leave_menu/go_home/open. NAMES/_in/_err_text/
        # _clamp_scroll are injected (see that module's docstring for why).
        self.block_ui = BlockEditorUI(self, NAMES, _in, _err_text, _clamp_scroll)
        self.block_ui.relayout(self.sys_canvas.w, self.sys_canvas.h,
                               self.look.effective_font_scale())

    def _init_components(self, input, carts):
        """Injected-service attach points + the shell processes (Project/Player/
        EditorApp) + the extracted editor/HUD UIs."""
        self.input = input
        # The (store, root, can_manage, with_sd) guard 4-tuple as ONE object
        # (#209 landing B, system_store.py). FIRST, because everything that
        # touches storage takes it: both store-owning collaborators below AND
        # app_context's storage roles -- and the ArtworkService a few lines down
        # already builds an AppContext. It captures nothing; every field is read
        # through `self` at the moment of use, because none of them is wired yet.
        self.store = StoreHandle(self)
        self.make_api = None       # injected: make_api(canvas, input, cfg, sheet, audio, tilemap, pmem, wifi)->ns
        # A narrow capability for the shipped Paint app. It is not a Layer, so
        # it is not in app_decls -- but it is on the APP side of the seam, so it
        # takes an AppContext like one. Its prefs namespace is "paint" and not
        # its id, because `paint_doc` is on real cards since #108 (see Prefs).
        self.artwork = ArtworkService(
            self.app_context("paint", ArtworkService.NEEDS, prefs_ns="paint"))
        self.make_audio = None      # injected: make_audio(engine)->audio backend (host/device)
        self.audio = None           # the per-cart audio backend (built on open, #16)
        # WiFi (#38): a SYSTEM service shared across carts (the connection persists
        # when a cart exits), not per-cart. run_desktop/build_workstation injects
        # the backend here; it's exposed to a cart's namespace ONLY when the cart's
        # manifest permissions include "network" (capability-gated -- see _start).
        self.wifi = None            # injected wifi backend (host FakeWifi / device WLAN)
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
        # network round trip; a board that grows pins of its own would inject a
        # direct driver instead. The cart verbs are the same two names either way.
        # The CONTRACT is written at the remote backend's level (a write is
        # queued, a read may be one round trip stale), so a native backend is a
        # strict improvement and no cart has to know which it got.
        self.gpio = None
        # #65 Phase 2: the live two-console LOCKSTEP session (netplay.LockstepSession),
        # set by a transport once two consoles have agreed on a cart and a seed, and
        # torn down with the run. None = solo, and every cart behaves as it always has.
        self.netplay = None
        # The RADIO that forms those sessions (device/moy_espnow.EspNowLink), injected
        # by run_desktop on the boards that have one. None on the host, the P4 and the
        # browser -- and absence is the whole interface: nothing here probes for a
        # radio, it is simply never armed.
        self.link = None
        self.carts_store = None     # injected: cart store module (moy_carts API)
        # #67 dual-runtime seam: factory(ns, src) -> a running Lua cart handle
        # (.init/.update/.draw callables + .close()). build_workstation injects
        # the lupa-backed runtime/lua_host.py; the device injects moy_lua once
        # Phase 1 lands. None = "runtime": "lua" carts open the error panel.
        self.lua_runtime = None
        # OTA firmware updater (#53): injected by the device (moy_ota.OtaUpdater); None
        # on the host. When present AND the build is OTA-capable, Settings grows an
        # "UPDATE FW" row that flashes a new image from /sd/update to the inactive slot.
        self._updater = None
        self.c6_updater = None   # P4 only: the radio co-processor's updater (#7/#58)
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
        self.update_ui = UpdateUI(self, NAMES, _in, _err_text)
        # WASM MODE (#197): the pin, the paired url, the connection screen and
        # the parked flag, all on one object (web_console.py). The webhost above
        # stays a flat Workstation attribute -- `poll_webhost` reads it at every
        # frame tail on all three boards -- and this reads it through `self`.
        self.web = WebConsole(self, NAMES, _in)
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
        self.launcher = Launcher(self._launcher_items(self.carts.all),
                                 self.layout, NAMES, _blit_glyph)
        # The HOME grid exposes the selected card's PLAY/CHANGE row on the desktop-
        # density tiers (visual identity v1 Section 1.2); the picker below keeps the
        # single-verb pick, so the flag stays off there.
        self.launcher.actions = True
        # The shelf's pseudo cards (MAKE STUDIO / + New) draw the real themeable
        # IconSheet pencil/plus sprite big -- keyed so the sheet's 0-filled
        # backdrop doesn't plate the card.
        self.launcher.icon_for = self._icon_image_keyed
        self.launcher.cover_for = self.covers.cover_for
        # The favorite-star corner badge (#105): only the RUN grid plays, so only
        # ws.launcher (not the Editor's project-picker) gets the toggle.
        self.launcher.favorite_for = self.carts.is_favorite
        # Default the highlight to the first RUNNABLE cart (skip the pinned Make tile at
        # slot 0), so a bare RUN/A plays a game rather than opening the picker -- the
        # launcher is RUN-first (spec shell_ux_v1.md); Make is a tap/nav target.
        self.launcher.sel = next((i for i, it in enumerate(self.launcher.items)
                                  if it.get("path")), 0)
        # The Editor's project-picker grid (spec shell_ux_v1.md): its own Launcher
        # instance so it keeps an independent selection/page, reusing the SAME tile
        # rendering. `ws.editor_picker` (the content Layer) draws it; ws.pick_selected
        # opens the chosen cart in the Editor.
        self.picker = Launcher(self._picker_items(self.carts.all),
                               self.layout, NAMES, _blit_glyph)
        self.picker.icon_for = self._icon_image_keyed
        self.picker.cover_for = self.covers.cover_for
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
        self.player = Player(self, NAMES, _in)
        # The EDITOR app (Stage 3, editor_app.py): owns the tab ladder + the active-tab
        # state (EditorApp.tab). Built idle here (BEFORE anything can set menu_view,
        # which is now a forwarding projection of editor_app.tab -- see below). The tab
        # machine (set_menu_view/_open_*/_leave_menu) moved onto it; ws keeps one-line
        # forwards so every surface file + test is unchanged. NAMES/_in are injected
        # (Stage 4, docs/history/shell_ux_technical_plan_v1.md): the Editor now lends the top
        # bar's left zone (draw_zone/zone_tap, bar_layer.py) so it needs the shared
        # draw toolkit + rect hit-test, like the other zone-owning surfaces.
        self.editor_app = EditorApp(self, NAMES, _in)
        self._run_caller = None       # who to return to on EXIT (run() records it; the
                                      # launcher root OR -- Stage 3 -- the Editor. The
                                      # Stage-5 hold-BACKSPACE / context-X
                                      # all pop to it via _exit_to_caller / exit())
        # (The cards menu's selection/scroll state -- msel/mtop -- lives on
        # self.cards_layer now, built in _build_layers with the rest of the stack.)
        # (The active menu sub-view -- "cards"|"code"|"paint"|"map"|"blocks"|"music"|
        # "theme" -- lives on self.editor_app.tab now (Stage 3); ws.menu_view is a
        # forwarding projection of it, so every reader/writer is unchanged.)
        self.editor = None            # CodeEditor while menu_view == "code"
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
        self.map_ui = MapEditorUI(self, NAMES, _in)
        # The scene placement editor's UI (#85 Stage 2 -- see scene_editor_ui.py):
        # one instance, delegated to from the "scene" content layer plus
        # set_menu_view/_open_scene/open/go_home, the exact map_ui lifecycle.
        self.scene_ui = SceneEditorUI(self, NAMES, _in)
        # Block editor (#29 Part 2) state now lives on self.block_ui (built above).
        # The music/sound editor's UI (#50, extracted from this class -- see
        # music_editor_ui.py): one instance, delegated to from handle_input/
        # handle_pointer/frame's menu_view == "music" branches plus set_menu_view/
        # _open_music/open (NOT go_home -- see music_editor_ui.py's docstring).
        self.music_ui = MusicEditorUI(self, NAMES, _in)
        # The perf HUD's rendering (#43/#44, extracted from this class -- see
        # perf_hud.py): the FPS chip + frame-time breakdown drawn in frame() and
        # the tap target hit-tested in handle_pointer. Named perf_ui (NOT
        # perf_hud -- that stays the tested boolean flag below for "breakdown
        # shown?"). The perf query API (perf_sample/perf_breakdown/...) stays on
        # this class (device diag contract). Pure read-only consumer of the
        # timing fields.
        self.perf_ui = PerfHud(self, NAMES)

    def _init_state(self):
        """Store/settings/wallpaper/cache/status fields (the mutable shell state)."""
        self.keyboard = None          # set by run_desktop (for raw/text mode toggle)
        # (The code editor's keyboard-edge tracker (_ekey_prev) + drag-scroll origin
        # (_drag) + highlight memo (_hl_cache) live on self.code_layer now.)
        # (The paint drag-stroke origin -- _paint_drag -- lives on self.paint_layer.)
        # (The launcher's trackball-hover state (_lhover) lives on self.launcher_layer.)
        self.pointer = None           # set by run_desktop
        # The system clipboard (#132): the one typed holder every editor writes
        # through (code tab / Writer / Sheets v1), so copy in one app pastes in
        # another. Console-side end-to-end -- works identically over the web
        # transport, never touches a host OS clipboard (parity trap).
        self.clipboard = Clipboard()
        # system.json (#209 landing B): the store owns the dict and every
        # persist funnel; `self.system` is a plain ALIAS of it and neither name
        # is ever rebound -- prefs.load() clears and updates in place, which is
        # what keeps settings_layer's raw writes, the launcher's per-paint
        # favorites read and app_context's Prefs role honest with no migration.
        self.prefs = SystemStore(self, self.store)
        self.system = self.prefs.settings
        # Crash isolation (#160 / Phase 8): the dict itself, because it stays
        # the same object across a load.
        self.app_guard = CrashGuard(self.system, self.prefs.persist)
        # Desktop wallpaper (#28): a chosen wallpaper-type cart compiled into its
        # own namespace and run (its _draw, optionally _update) as the BACKDROP each
        # home/settings frame -- the Picotron "wallpaper is a cart" model. The
        # component owns the RENDERING + the compiled-cart cache; the CHOICE
        # (`ws.look.wallpaper_id`) and the picker verbs are the look's.
        self.wallpaper = Wallpaper(self, NAMES)
        # Expensive-event counters (2026-07-26). See note_cost.
        self.costs = {}
        self._quiet_frames = 0        # consecutive frames the redraw gate skipped
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
        # on self.bar_layer; look.set_icon_sheet bumps its cache gen via bar_layer.invalidate().
        self.bar_layer = BarLayer(self, NAMES, _in)
        # Themeable top bar (Stage 2): True while the PAINT editor is repainting the
        # SYSTEM icon sheet (Settings -> EDIT ICONS) rather than a cart's sprites.
        # It changes where SAVE writes (system_icons.moygfx, not the cart) and where
        # CLOSE/back returns (Settings, not the running cart). menu_view == "theme"
        # reuses the cart PAINT renderer/input over ws.look.icon_sheet (PaintEditor is
        # tile-size-agnostic, so the 16x16 IconSheet edits natively).
        self._editing_icons = False
        # (The Settings selection/scroll window -- set_msel/set_top -- lives on
        # self.settings_layer now, built in _build_layers with the rest of the stack.)
        self.carts_root = None        # SD carts dir (reads); set by run_desktop
        # (cart_error + crash_line live on self.player now -- Stage 2; forwarding
        # properties below, so ws.cart_error / ws.crash_line are unchanged.)
        self.save_status = None       # last save_code result text (e.g. a syntax error)
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

    def _init_perf(self):
        """The perf/diag/frameskip measurement fields (#43/#44/#66/#68/#77)."""
        # The persisted ON/OFF settings, at the registry's declared defaults
        # (#209 section 7 -- SETTINGS_TOGGLES in settings_layer.py carries each
        # one's prose). FLAT ATTRIBUTES on purpose and forever: `frame_cap_fps`
        # reads self.frameskip and device_boot's `pace` calls it every loop
        # iteration on all three boards; both WMs read show_fps per painted game
        # frame. load_system replaces these with the store's values at boot.
        for _key, _label, _default, _setter, _gate, _dev in SETTINGS_TOGGLES:
            setattr(self, _key, _default)
        self._fps = 0.0               # smoothed frames/sec (EMA of 1/dt)
        # Frame-time breakdown HUD (#43/#44 perf): off by default; tap the FPS
        # readout (bottom-right, while a cart runs) to toggle it. When on, frame()
        # records the per-frame split in ms -- _flush_ms is the compositor's panel
        # DMA flush (comp.flush(); ~0 on the host's _NullComp), _draw_ms is the
        # rest (cart _update/_draw + the console's own draw = total minus flush).
        # All EMA-smoothed like _fps so the numbers read steady, not single-frame
        # jitter. This tells us whether the wall is the SPI flush or the per-frame
        # MicroPython draw cost on device. Measurement only -- no render-path change.
        self.perf_hud = False         # frame-time breakdown HUD shown? (tap FPS to toggle)
        # perf_capture decouples the per-frame timing MEASUREMENT from drawing the
        # HUD: when either perf_hud OR perf_capture is set, frame() records the
        # flush/draw split (the two cheap ticks calls below). The device backend
        # (moy_runtime.run_desktop) sets perf_capture=True so it can SAMPLE these
        # numbers into the offline diag log without painting the HUD on screen.
        # Default False -> host behaviour is byte-identical (no extra ticks calls).
        self.perf_capture = False     # measure flush/draw without drawing the HUD
        self._flush_ms = 0.0          # smoothed comp.flush() ms (panel DMA)
        self._draw_ms = 0.0           # smoothed draw ms (total frame - flush)
        # DRAWBRK phase split of _draw_ms (#43 follow-up): where the per-frame draw
        # cost actually goes -- cart _update, cart _draw, and the console chrome
        # (bar + cursor + overlays, the remainder). Surfaced via perf_breakdown().
        self._upd_ms = 0.0            # smoothed cart _update(dt) ms (game LOGIC)
        self._cart_ms = 0.0           # smoothed cart _draw() ms (RENDERING)
        self._audio_ms = 0.0          # smoothed audio.tick(dt) ms (mixer feed)
        self._chrome_ms = 0.0         # smoothed chrome ms (= draw - upd - cart - audio)
        # LAYERBRK (#172) / the hitch logger's hp() detail (#184): the two stack
        # walks, split per layer. _pf_layers is {layer.id: us} for the last
        # PAINTED frame (rebuilt each paint under _perf); _pf_ptr is a fixed
        # 6-slot scratch overwritten in place by handle_pointer --
        # [total_us, pre_us, worst_us, worst_id, claim_id, n_visited].
        self._pf_layers = None
        self._pf_ptr = [0, 0, 0, None, None, 0]
        self._ptr_last_x = -1     # handle_pointer's idle fast-path: last routed
        self._ptr_last_y = -1     # pointer position (ints -- no per-frame tuple)
        self._ptr_was_down = False  # ...and whether it was held (release edge)
        # #184 deferred transitions: [armed, fn] entries queued by defer().
        # A tap handler schedules its heavy transition here instead of running
        # it inside the pointer walk; frame() paints the acknowledgment first
        # (arming the entry after the flush), then runs it at the next frame's
        # top -- so the pressed state is ON GLASS during the load stall.
        self._deferred = []
        # RAW (un-smoothed) copy of THIS frame's phase split (#66 HITCH v3): the
        # EMAs above hide which phase a single 150ms hitch frame spent its time
        # in (a one-frame spike moves an alpha=0.15 EMA by only 15% of itself).
        # The hitch logger prints these instead.
        self._raw_upd = 0.0
        self._raw_cart = 0.0
        self._raw_audio = 0.0
        self._raw_chrome = 0.0
        self._raw_flush = 0.0
        self._raw_draw = 0.0
        # CHROMEBRK sub-split of _chrome_ms (#66 lever 5, instrument-before-cutting):
        # what the ~4-6ms of cart-path chrome actually buys -- the top status bar
        # (_draw_status_strip), the game->system viewport composite (a no-op when the
        # canvases are one object, i.e. today's 320x240 device), the cursor, the rest
        # of the WM stack walk (_stk_ms, 2026-08-14), and the router remainder.
        # Only measured on the running-cart path with perf capture on; surfaced via
        # perf_chrome() -> the device CHROMEBRK diag line.
        self._bar_ms = 0.0
        self._cmp_ms = 0.0
        self._cur_ms = 0.0
        self._stk_ms = 0.0  # the WM stack walk's CHROME share (2026-08-14)
        self._bg_ms = 0.0   # #172: backdrop restore, a SUB-slice of _cart_ms
        # (The clock-text cache moved to self.bar_layer with the rest of the bar #66.)
        self._fs_phase = False        # frameskip's alternation bit (#77)

    def _init_overlays(self):
        """Achievements/eggs (#21), the system menu (#52), device hooks, and the
        #44 redraw gate."""
        # THE OVERLAY DEADLINES (#209 landing B, rev 3). The achievement objects
        # PUSH here at event time -- an unlock arms the toast, an egg arms the
        # popup, the Konami code arms the confetti -- and the per-frame gates
        # (`_animating`, the WM's `_overlay_sig`) read these plain ints and
        # nothing else. Before this they POLLED `ach.toast_active()` /
        # `ach_ui._egg_active()` / `ach_ui._confetti_until` every loop on every
        # tier, to be told "no" on essentially every frame of the console's life.
        # Same shape as `_notice_until` below; 0 means down, and the objects keep
        # only the PAYLOAD they draw, which is read only while its deadline is up.
        self._toast_until = 0          # _ticks_ms the achievement banner hides at
        self._egg_until = 0            # _ticks_ms the Easter-egg popup hides at
        self._confetti_until = 0       # _ticks_ms the Konami confetti ends at
        # Achievements (#21): a small set of fun milestones + the hidden Easter-egg
        # rewards. Starts empty/volatile; load_achievements() adds the SD store.
        # The unlock hook is wired from the START, not at that load: persistence
        # waits for a store, but arming the toast is kernel behavior a build with
        # no card still owes the kid.
        self.ach = Achievements(on_unlock=self._achievement_unlocked)
        # The Easter-egg subsystem + achievement/egg drawing (#21, extracted from
        # this class -- see achievements_ui.py): the 3 hidden eggs + their trigger/
        # popup state (_konami_pos/_clock_taps/_secret_taps/egg_msg) +
        # _draw_egg/_draw_confetti/_draw_achievements. The achievement core
        # (ach/show_achievements/load_achievements/...) and the deadlines above
        # stay here. Egg trigger state is reset on screen changes
        # (go_home/settings/desktop tap) via self.ach_ui.* so a stray sequence
        # never carries between contexts.
        self.ach_ui = AchievementsUI(self, NAMES, ACHIEVEMENTS)
        self.show_achievements = False  # the locked/unlocked list overlay (Settings entry)
        # A transient SYSTEM banner (#53): something the machine did on its own and
        # the kid should hear about without going looking. Distinct from the
        # achievement toast, which celebrates something the KID did. First user is
        # the firmware-update verdict -- an update lands during a reboot, so there
        # is no screen it could otherwise report to.
        self._notice = None            # (title, sub, kind) while up
        self._notice_until = 0
        # Top-bar system menu (#52): the ≡ dropdown. A reusable Popup owns its own
        # open/selected state; the SYSTEM group (Settings/About/Reboot) is always
        # present, a CART group (Restart/Delete) is prepended only while a cart is
        # open. `_about` is a tiny dismissible info modal the About row pops.
        self.sysmenu = Popup()
        self._about = False
        # The ≡ dropdown / system menu's UI (#52, extracted from this class -- see
        # system_menu_ui.py): the row builder + per-item actions + drawing.
        # toggle_sysmenu() + the sysmenu Popup + _about flag stay here (tested ws.
        # surface); the menu just delegates item-building/drawing to this.
        self.menu_ui = SystemMenuUI(self, NAMES)
        # Reboot hook: the device injects a callable (machine.reset via the OTA
        # updater); None on the host -> the Reboot row is a safe no-op (go_home).
        self.reboot_hook = None
        # Redraw-on-change (#44 step 1): a static UI screen costs ~0 -- frame() only
        # redraws + flushes when something visible changed. `_dirty` is the "redraw
        # this frame" flag; it starts True so the very first frame always paints, and
        # is set whenever input/state could have changed the picture (mark_dirty()).
        # `_last_ptr` snapshots the pointer state actually drawn so a cursor move/hide/
        # click triggers exactly one redraw. A running cart and a live wallpaper /
        # overlay effect animate every frame -> always dirty -> unchanged full-redraw
        # behaviour for them. `_frames_drawn` counts the frames that actually painted
        # (idle frames are skipped) -- a host-testable witness of the win.
        self._dirty = True
        self._last_ptr = None         # (x, y, visible, down, click) last drawn, or None
        self._frames_drawn = 0        # frames that actually drew+flushed (test witness)
        self._frame_dt_ms = 33.0      # last loop tick in ms (kinetic velocity, #113)
        self._pointer_dt_ms = 33.0    # ...as charged to the pointer SAMPLE (see
        self._stale_ptr_ms = 0.0      # _tick_pointer_dt), None while it's a repeat
        self._frame_requested = False # a draw asked for another frame (request_frame:
                                      # a coasting fling re-arms the gate the
                                      # covers.take_deferred way -- set DURING a draw,
                                      # consumed after the gate cleared _dirty)
        # Per-frame perf scratch (#43/#66): the running-cart content Layer fills these
        # during its draw so the router's frame-end DRAWBRK/CHROMEBRK accounting can read
        # the split without threading it back through the loop. Zeroed each frame().
        # MICROSECONDS since 2026-08-14 -- ms truncation was piling into CHROMEBRK's
        # `other`, which is a residual and so inherited every term's rounding.
        self._pf_upd = 0
        self._pf_cart = 0
        self._pf_audio = 0
        self._pf_bar = 0
        self._pf_bg = 0     # #172: the declared-backdrop share of _pf_cart
        self._pf_stack = 0  # total us of the last painted frame's layer walk
        # #172: the frame's unmeasured EDGES, us -- pre = entry..draw span open
        # (journal tick, splash, frameskip branch, redraw gate), post = the tail
        # after the flush (dirty clear, covers/fling re-arm, pointer snapshot).
        self._pf_pre = 0
        self._pf_post = 0


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
        self.cards_layer = CardsLayer(self, NAMES, _in, _err_text)
        # The PAINT editor surface (#4/#30): one renderer for the cart sheet ("paint")
        # and the icon sheet ("theme"), keyed on ws._editing_icons. It reads ws.paint /
        # ws.sheet + dispatches SAVE/GET/PUT/CLOSE to ws. The theme content is ThemeLayer
        # -- it owns the EDIT-ICONS lifecycle + delegates all editing to this PaintLayer.
        self.paint_layer = PaintLayer(self, NAMES, _in)
        self.theme_layer = ThemeLayer(self, self.paint_layer, NAMES)
        # The Settings app (#28/#39/#53): the aggregator screen. Owns the row list +
        # scroll window (set_msel/set_top) + drawing; reads ws config/system state +
        # dispatches every mutation to ws setters (it owns NO config).
        self.settings_layer = SettingsLayer(self, NAMES, _in, _clamp_scroll)
        # The system APPS are constructed AND registered below, from the
        # declarations (`_init_apps`) -- Paint's indexed document + reflowing
        # chrome, Writer's notebook, Storybook's compiling decks, Sheets'
        # formula grids, Files' user-files gallery and Calc. Each is a
        # `system_carts/<folder>/manifest.json` "app" block, not a line here.
        # The Python code editor (#24/#39): the full-screen text view. Owns the drawing
        # + code-UI state (keyboard edge / drag / highlight memo); the shared ws.editor
        # handle + save_code/run_code + code-error state + code_layout stay on ws.
        self.code_layer = CodeLayer(self, NAMES, _in)
        # The desktop home / launcher (#28): the home composition + grid nav. The Launcher
        # GRID instance stays ws.launcher (the single source); this Layer draws it.
        self.launcher_layer = LauncherHomeLayer(self, NAMES, _in)
        # The Editor's project-picker content Layer (spec shell_ux_v1.md): reuses the
        # launcher grid look over ws.picker; the Make tile opens it, picking a cart opens
        # the Editor above it.
        self.editor_picker = EditorPickerLayer(self, NAMES, _in)
        # Content layers (exactly one active per frame, chosen by screen/menu_view). Every
        # surface is now its own Layer/component; only the running-cart "desktop" + the
        # theme wrapper remain thin _LegacyLayer shims over Workstation methods.
        # SYSTEM APPS (docs/app_api_v1.md) are NOT listed here -- register_app below
        # adds each one's kind to this table.
        self._content_layers = {
            "launcher": self.launcher_layer,
            "picker": self.editor_picker,
            "settings": self.settings_layer,
            "update": _UpdateLayer(self),
            "webconsole": _WebConsoleLayer(self),   # #197: the parked wasm mode
            "desktop": _PlayerLayer(self),   # Stage 2: the run loop is ws.player

            "code": self.code_layer,
            "blocks": _BlocksLayer(self),
            "music": _MusicLayer(self),
            "theme": self.theme_layer,
            "paint": self.paint_layer,
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
        self._app_min_sizes = {}
        self._app_titles = {}
        self._init_apps()
        # The boot logo is a draw-time takeover of the screen content (input still
        # routes to the underlying screen), so it's not in _content_layers.
        self._splash_layer = L("splash", "system", draw=lambda dt: self._draw_splash())
        # Transient overlays (gated in _overlay_stack) + the cursor. The perf HUD is
        # a game-domain overlay (drawn before the composite, riding the viewport); the
        # rest are system-domain, on top of the composited screen.
        self._perf_layer = _PerfLayer(self)
        self._confetti_layer = _AchOverlayLayer(self, "confetti", self.ach_ui._draw_confetti)
        self._ach_layer = _AchOverlayLayer(self, "achievements", self.ach_ui._draw_achievements)
        self._egg_layer = _AchOverlayLayer(self, "egg", self.ach_ui._draw_egg)
        self._toast_layer = L("toast", "system", draw=lambda dt: self._draw_toast())
        self._notice_layer = L("notice", "system", draw=lambda dt: self._draw_notice())
        self._sysmenu_layer = _SysMenuLayer(self)
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
            return self._content_layers.get(self.menu_view) or self._content_layers["cards"]
        return self._content_layers.get(kind) or self._content_layers["launcher"]

    @property
    def _active_content(self):
        """The active content Layer (spec alias for _content_layer())."""
        return self._content_layer()

    @property
    def windowed_chrome(self):
        """True while the MAKE world (the windowed WM's desk, #105 two-worlds)
        is open: in-window app bars suppress the OS right zone, and
        the bar's wifi icon deep-links to the Settings window. Always False in
        the PLAY world (fullscreen Library/games -- even on the windowed tier)
        and on the fullscreen-stack tiers, where the fullscreen chrome rules
        apply verbatim."""
        wm = getattr(self, "wm", None)
        return wm is not None and wm.desk_open()

    # The overlay/visible/draw stacks are now built + MEMOIZED by the WM (Stage 6c,
    # wm.py) -- rebuilt only on a back-stack push/pop, a menu_view tab switch, or an
    # overlay-gate/splash flip, so a static top-of-stack allocates no per-frame list
    # (the #66 perf-recovery win). These stay as the tested ws. entry points; frame()
    # walks self.wm.draw_stack() and handle_input/pointer walk the cached reversed
    # visible stack (wm.visible_stack_rev()) directly, so the hot path never allocates.
    def _overlay_stack(self):
        return self.wm.overlay_stack()

    def _visible_stack(self):
        return self.wm.visible_stack()

    def _draw_stack(self):
        return self.wm.draw_stack()

    @property
    def sys_canvas(self):
        """The SYSTEM canvas the desktop chrome + overlays render on (#39). A distinct
        SystemCanvas when one was supplied, else the GAME canvas itself (degradation:
        one surface, pixel-identical to today). Reading through `self.canvas` keeps it
        correct even if a backend swaps the game canvas (e.g. the web CommandCanvas)."""
        return self._sys_canvas if self._sys_canvas is not None else self.canvas

    def _cart_has_perm(self, name):
        """True iff the open cart's manifest permissions include `name` (#38).
        moy_carts.load() carries the manifest "permissions" list onto the cart;
        an embedded/legacy cart with none simply never matches, so it gets no
        gated APIs."""
        perms = self.cart.get("permissions") if self.cart else None
        return bool(perms) and name in perms

    # -- user apps: identity + the crash guard (#181 / #160) -----------------

    def app_cart_id(self, cart):
        """The stable identity a USER APP cart's prefs namespace and crash
        strikes are keyed by (`system_api.app_id_for`: the title slug, so it
        survives the host-folder / device-folder mismatch)."""
        return system_api.app_id_for(cart)

    def is_user_app(self, cart):
        """True when `cart` is an app cart the Player runs as a USER APP -- a
        `type: "app"` cart that no registered shell app claims as its identity.

        The claim check matters: `calc.moy` is also `type: "app"`, but the
        launcher dispatches it to `CalcAppLayer` and its `main.py` is only the
        older-shell fallback body."""
        return (cart is not None and cart.get("type") == "app"
                and not self.is_system_app(cart))

    def app_bar_h(self):
        """Rows the exitable "tool" strip owns on top of a running app cart's
        surface -- what `bar_h()` reports to a USER APP so it can draw below the
        chrome instead of hardcoding 18 and breaking at font scale 2."""
        return self.bar_layer._bar_h("tool")

    def cart_broken(self, cart):
        """True when the crash guard has turned this app cart OFF (#160).

        NOT what the Player reads -- it refuses through `app_guard.arm()`
        returning False, which is the same answer taken on the path that also
        records the strike. This is the shell-vocabulary query (is_user_app +
        the title-slug id in one call): `tests/test_user_apps.py` asserts
        against it, and it is half of what the deferred picker BADGE needs
        (docs/history/ui_refactor_2026-08.md, Phase 8's open tails). The cart stays in
        the Editor picker either way, because editing it is how it gets
        fixed."""
        if not self.is_user_app(cart):
            return False
        return self.app_guard.disabled(system_api.app_id_for(cart))

    def forgive_app(self, cart):
        """Clear `cart`'s crash strikes -- the kid changed its CODE (#160).

        The other half of three-strikes, and without it the refusal panel's
        "EDIT it" was a dead end: nothing called `CrashGuard.forgive`, so the
        only ways back were renaming the cart or hand-editing `system.json`.

        Called from `Project.commit_code`, and deliberately from there ALONE.
        A code commit is the one edit that can change whether the cart hangs,
        faults or exhausts the heap -- the failures the guard exists for, none
        of which a sprite, a map or a config tweak can fix or cause. Forgiving
        on every asset save would hand a boot-looping app a fresh set of
        strikes for repainting a tile; forgiving only on a hand-edited
        `system.json` is what we had.

        Strikes are cleared, not decremented: the kid's next open starts from
        zero and gets the full three, exactly like a cart the guard has never
        seen. False when there was nothing to forgive."""
        if not self.is_user_app(cart):
            return False
        return self.app_guard.forgive(system_api.app_id_for(cart))


    def load_system(self):
        """Read the system settings (`self.prefs`) and APPLY them -- the saved
        wallpaper, font scale, theme, skin and every persisted toggle (#39).

        The read is the store's; this cascade is kernel policy and stays here.
        What a setting MEANS is the shell's business -- each one relays through
        the same `set_*` verb the Settings row calls, with persist=False so
        loading never re-writes what it just read."""
        self.prefs.load()
        # System font scale (#39): apply the persisted choice (1/2/3) so the desktop
        # boots at the saved text size. look.set_font_scale relays it into the system
        # canvas + relayouts; persist=False so loading doesn't re-write the store.
        self.look.set_font_scale(self.system.get("font_scale", self.look.font_scale),
                                 persist=False)
        # Paint's shared document lives outside the re-seeded built-in cart. Restore
        # My Art's bg asset before compiling a persisted My Art wallpaper.
        self.artwork.sync_wallpaper()
        self.look.select_wallpaper(self.system.get("wallpaper"), persist=False)
        self.look.set_theme(self.system.get("theme", self.look.theme_name),
                            persist=False,
                            variant=self.system.get("theme_variant",
                                                    self.look.theme_variant))
        # The widget skin, beside the colorway it belongs with. Applied ONLY
        # when the store names one: `ui`'s tables are already the default, so
        # "no key" means "nothing to install", not "install the default over
        # whatever this process has" -- see the note at Appearance.skin_name.
        _sk = self.system.get("skin")
        if _sk is not None:
            self.look.set_skin(_sk, persist=False)
        # Every persisted ON/OFF setting, through the verb its registry entry
        # names (#209 section 7): the key is the system.json key, the default is
        # declared beside it, and a board that cannot serve one gets the honest
        # answer from the setter rather than a silent flag. Six hand-kept lines
        # used to sit here, and a seventh toggle is now none.
        for key, _label, default, setter, _gate, _dev in SETTINGS_TOGGLES:
            getattr(self, setter)(self.system.get(key, default), persist=False)




    def _relayout(self):
        """Rebuild the responsive layout from the live system-canvas size + the
        EFFECTIVE font scale and re-push it into the launcher (so its grid reflows).
        Called on a font-scale change (and could be called on a resize)."""
        w, h, fs = self.sys_canvas.w, self.sys_canvas.h, self.look.effective_font_scale()
        self.layout = Layout(w, h, fs)
        self.launcher.set_layout(self.layout)
        # Editor layouts reflow too (#39 step 2); an open code editor adopts the new
        # visible window live so a font/size change reflows it without losing the buffer.
        self.code_layout = CodeLayout(w, h, fs)
        self.block_ui.relayout(w, h, fs)
        if self.editor is not None:
            self.editor.set_view_size(self.code_layout.cols, self.code_layout.rows)
        # The step-3 responsive editors (#39): each converted layer owns its layout;
        # guarded, since _relayout is first called before _build_layers registers them.
        for _lyr in ("paint_layer", "map_ui", "scene_ui", "music_ui", "cards_layer"):
            _obj = getattr(self, _lyr, None)
            if _obj is not None:
                _obj.relayout(w, h, fs)
        for _app, _t in getattr(self, "_apps", ()):   # registered system apps
            _relay = getattr(_app, "relayout", None)
            if _relay is not None:
                _relay(w, h, fs)
        # The windowed WM (wm_windowed.py, big-screen tier) re-anchors its layout
        # contexts after any relayout; a no-op hook on the fullscreen-stack WM.
        _hook = getattr(self.wm, "on_relayout", None) if hasattr(self, "wm") else None
        if _hook is not None:
            _hook()


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

    def park_web_console(self):
        """`web.park()` -- the layers.py contract comment and the tests."""
        return self.web.park()

    def stop_web_console(self):
        """`web.stop()` -- the Guition on-glass suite's serial vocabulary."""
        return self.web.stop()

    def unpark_web_console(self):
        """`web.unpark()` -- the tests."""
        return self.web.unpark()

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

    def _set_toggle(self, key, on, persist):
        """The tail every SETTINGS_TOGGLES verb shares: the flat mirror, the
        repaint mark, and the persisted copy under the SAME name (#209 section
        7). What differs per toggle -- the phase reset, the canvas hook, the
        keyboard hand-over -- stays written out in the verb that calls this.

        The mirror is set with setattr, which is fine because this runs on a
        FLIP and at boot, never per frame; the READ side stays a plain
        attribute everywhere, and must -- `frame_cap_fps` reads self.frameskip
        and device_boot's `pace` calls it every loop iteration on all three
        boards."""
        setattr(self, key, on)
        self._dirty = True
        if persist:
            self.system[key] = on
            self.prefs.persist()

    def set_diag_live(self, on, persist=True):
        """Flip the #68 diagnostics gate (Settings -> PERF DIAG) and persist it.
        The device loop (moy_runtime.run_desktop) reads self.diag_live each cycle,
        so the change takes effect within a frame -- no reboot."""
        self._set_toggle("diag_live", bool(on), persist)

    def set_diag_sd(self, on, persist=True):
        """Flip the periodic diag->SD write gate (Settings -> DIAG SD LOG) and
        persist it. Separate from PERF DIAG so a measurement session can stream
        serial samples WITHOUT the ~115ms 20s sdflush stutter; the offline
        play-then-read-diag.log workflow flips this ON too. Crash/cart-exit
        flushes are unconditional either way (the safety net)."""
        self._set_toggle("diag_sd", bool(on), persist)

    def set_frameskip(self, on, persist=True):
        """Flip the #77 frameskip gate (Settings -> FRAMESKIP) and persist it.
        Takes effect on the next frame; the phase bit resets so the first frame
        after a toggle always renders (no one-frame blank on enable)."""
        self._fs_phase = False
        self._set_toggle("frameskip", bool(on), persist)

    def second_keyboard(self):
        """The keyboard that can become player two, or None.

        A board qualifies only when it has a SECOND keyboard: the T-Deck's
        paired Bluetooth one, alongside the physical C3 keyboard it already
        has. On the touch-only boards a BLE keyboard IS `ws.keyboard` -- the
        only one there is -- so handing it to player two would leave player one
        with nothing to press."""
        ble = getattr(self, "ble_keyboard", None)
        if ble is None or ble is self.keyboard:
            return None
        return ble if getattr(ble, "set_player", None) is not None else None

    def set_two_player(self, on, persist=True):
        """Flip LOCAL 2P (Settings -> 2 PLAYERS) and persist it.

        Two kids, two real keyboards, one screen, and no radio between consoles.
        The whole mechanism is that the second keyboard's input SOURCE carries a
        player: a source with a player IS a player (#26), so `players()` reports
        2 and every cart that offers a 2P mode finds it -- no transport, no
        session, no netcode.

        A board with no second keyboard reports OFF whatever it is told. Saying
        otherwise would be the frozen-meter bug in another costume: the console
        would claim two players while nothing on it could produce the second
        one's buttons. (The keyboard slot stays honest at the other end too --
        an UNCONNECTED Bluetooth keyboard does not hold the slot, or the cart
        would field a character nobody could move.)"""
        on = bool(on)
        kb = self.second_keyboard()
        if kb is None:
            on = False
        else:
            try:
                kb.set_player(1 if on else 0)
            except Exception as exc:  # noqa: BLE001 -- a keyboard hiccup is not a crash
                print("Moybyte 2 players failed:", exc)
                on = False
        self._set_toggle("two_player", on, persist)

    def set_crisp_pixels(self, on, persist=True):
        """Flip the CRISP PIXELS composite (Settings row, capability-gated) and
        persist it. The mode lives on the SYSTEM canvas (set_crisp_scale --
        the P4's P4SystemCanvas routes the game composite nearest-neighbour
        instead of the PPA's fixed-bilinear scaler); a canvas without the hook
        never shows the row, so this setter is then only ever the boot apply
        of a stale system.json key."""
        on = bool(on)
        self._set_toggle("crisp_pixels", on, persist)
        hook = getattr(self.sys_canvas, "set_crisp_scale", None)
        if hook is not None:
            hook(on)

    def set_show_fps(self, on, persist=True):
        """Flip the in-game FPS chip (Settings -> SHOW FPS) and persist it.
        The chip is GAME-domain (it rides the cart's canvas and its composite
        scale -- 2x-big on a 128px cart, and fold-compatible for free, #190),
        so hiding it is purely cosmetic: the perf fields keep updating and
        PERF DIAG is untouched. Hiding also disables the chip's tap-to-toggle
        breakdown HUD, so clear that too rather than strand it on-screen."""
        on = bool(on)
        if not on:
            self.perf_hud = False
        self._set_toggle("show_fps", on, persist)

    def _persist_system(self):
        """`prefs.persist()` -- app_context's Prefs role and the dev channel's
        `vol`, which keep speaking this name."""
        return self.prefs.persist()

    def _ota_channel(self):
        """The selected OTA update channel ("stable" / "unstable" beta). Drives which
        manifest UPDATE ONLINE checks; persisted in system.json once chosen.

        The default is the channel this FIRMWARE was built on, not a constant. A
        board that took a beta is running `unstable`, and defaulting it to
        `stable` meant every check compared the two, found them different, and
        offered the "update" -- a downgrade, on every check, forever, because
        installing it is the only thing that would make the two agree. Which
        channel you are on is a fact about the running image; the setting is a
        deliberate departure from it, so absence of a setting should mean "the
        one I am on"."""
        saved = self.system.get("ota_channel")
        if saved in ("stable", "unstable"):
            return saved
        u = self.updater
        if u is not None:
            try:
                running = u.channel()
                if running in ("stable", "unstable"):
                    return running
            except Exception:            # a backend without channel(): fall through
                pass
        return "stable"

    def _cycle_channel(self, d):
        """Toggle the OTA channel STABLE<->UNSTABLE and persist. Two channels, so any
        step flips. This only changes what UPDATE ONLINE checks -- the running firmware
        is unchanged until a manifest is actually installed (and the bootloader's
        rollback still guards a bad beta image)."""
        self.system["ota_channel"] = (
            "stable" if self._ota_channel() == "unstable" else "unstable")
        self.prefs.persist()

    def load_achievements(self):
        """Wire a fresh Achievements over the badges the store remembers (#21).

        The read is `prefs`'; the WIRING is kernel -- persistence goes back to
        the store, the unlock effects (the toast deadline + the beep) are the
        kernel's own. Call after the store + carts_root are injected (host
        build_workstation / device run_desktop)."""
        self.ach = Achievements(self.prefs.load_achievements(),
                                on_save=self.prefs.save_achievements,
                                on_unlock=self._achievement_unlocked)

    def _achievement_unlocked(self, ach_id):
        """A fresh unlock's EFFECTS: arm the toast overlay, then celebrate with a
        short rising beep when audio is wired (#21, rev-3 event push).

        `Achievements` generates the effect and the kernel executes it. The
        deadline is written HERE, at the unlock, rather than polled per frame off
        the object -- `_animating` and the WM's overlay signature read the flat
        field and never call into `ach` on the frame path. There is no toast
        QUEUE to preserve: `award()` overwrites its payload, so a second unlock
        inside the window replaces the banner and extends the deadline, which is
        exactly what a later write to this field does.

        The deadline is armed BEFORE the beep so a silent (or broken) backend
        cannot cost the kid the banner; the beep itself is best-effort."""
        self._toast_until = _ticks_ms() + TOAST_MS
        au = self.audio
        if au is not None:
            try:
                au.beep(880, 0.08)
                au.beep(1320, 0.12)
            except Exception:  # noqa: BLE001
                pass

    # -- hidden Easter eggs (#21) now live on self.ach_ui (achievements_ui.py,
    # AchievementsUI): the 3 eggs + their trigger state + the popup payload +
    # _show_egg + _draw_egg/_draw_confetti/_draw_achievements. The achievement
    # core above (load_achievements/_achievement_unlocked + self.ach) and the
    # overlay deadlines those objects arm (_init_overlays) stay here.



    def note_cost(self, what):
        """Count one EXPENSIVE event: a cache build, or a call into storage.

        Every performance bug found in the 2026-07-26 session was a violated
        assumption that produced NO SIGNAL -- a cache silently missing 100% of the
        time (the bar strip keyed on canvas identity while the WM alternated
        destinations: 72ms of an 86ms frame, twice per gesture), an accessor
        silently rebuilding per row, storage reads silently landing on drag
        frames. None of them broke anything; they just made two frames in
        thirty-one five times slower, which only shows up if you happen to measure
        the exact frame. Each took hours to find, and three wrong models died on
        the way.

        So the expensive paths say so. Deliberately counted on the BUILD side
        only, never on the hit side: a cache hit is the hot path and stays
        untouched, while a build already costs 15-100ms, so one dict increment
        there is free. Hit RATE is not the interesting number anyway -- "rebuilt
        44 times in 44 frames" is the thing that screams, and a bare build count
        says it.

        Read it two ways: the P4's `state` serial command reports it, so a glass
        session sees a thrashing cache immediately; and tests assert BUDGETS over a
        run of frames (tests/test_top_bar.py, tests/test_cover_pipeline.py), which
        turns this whole bug class from a perf mystery into a test failure."""
        d = self.costs
        d[what] = d.get(what, 0) + 1

    # -- Settings screen (#28) -----------------------------------------------
    #
    # Most rows are live (wallpaper/theme/font size/icons/frameskip/fps/diag --
    # settings_layer.py is the authority); only the remaining "mock" rows step a
    # cosmetic placeholder value. Each row is (key, label, kind).


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
        if not self.wm.top_is("settings"):        # Stage 6d: ask the stack top
            self._settings_return = self.wm.top_kind()  # resume here on exit (cart vs home)
        self._dirty = True             # screen change repaints (#44)
        self.settings_layer.reset()    # reset the selection + scroll window (#53)
        self.wm.goto("settings")       # Stage 6e: push Settings onto the back-stack
        self.show_achievements = False
        self.ach_ui._secret_taps = 0              # fresh secret-door run each visit (#21)
        self._set_text_mode(False)

    def _exit_settings(self):
        # Windowed WM (#73): close JUST the Settings window -- whatever else is
        # open (a running game, the Make window) stays. Desk world only (#105):
        # play-world Settings is fullscreen and takes the resume-or-home rules.
        _ck = getattr(self.wm, "close_window_kind", None)
        if _ck is not None and self.wm.desk_open():
            self._dirty = True
            _ck("settings")
            return
        # Close Settings back to wherever it was opened from: resume the running cart
        # if we came from one (the gear on the in-cart/crash bar), else the launcher home.
        if getattr(self, "_settings_return", "launcher") == "desktop" and self.cart is not None:
            # Resume the running cart WITHOUT clobbering _run_caller (the Stage-3 review
            # fix): Settings was opened OVER a cart the Editor may have launched via PLAY,
            # so the cart's exit must still return to that caller (the Editor tab), not be
            # reset to the launcher here. run() would overwrite _run_caller; popping
            # Settings off the stack reveals the cart below + preserves who it pops to.
            self.wm.goto("desktop")    # Stage 6e: pop Settings, resume the cart below
            self._dirty = True
        else:
            self.go_home()

    # -- top-bar system menu (#52) -------------------------------------------
    #
    # The ≡ dropdown built on the reusable Popup primitive. Contents are rebuilt each
    # open from the live state: a SYSTEM group always (Settings / About / Reboot), and
    # -- only when a cart is open -- a CART group PREPENDED (Restart / Delete). The
    # actions wire to the existing console flows (open_settings, apply = re-run, the SD
    # delete path). Selecting any row closes the menu (Popup.activate closes first).

    def toggle_sysmenu(self):
        """≡ tapped (or its keyboard shortcut): open the dropdown if closed, close it
        if open. Rebuilds the item list so the cart group reflects the current state.
        The rows + their action callbacks + the drawing live on self.menu_ui
        (system_menu_ui.py); this stays here as the tested ws. entry point."""
        self._dirty = True             # overlay open/close repaints (#44)
        # Anchor the dropdown under the ≡ button's NEW right-zone position (Stage 4
        # moved ≡ off the left edge): right-align the panel to the button so it hangs
        # down-LEFT and stays on screen, clamped to [0, canvas_w - _POPUP_W]. The
        # sysmenu draws on the SYSTEM canvas, so anchor to the responsive Layout's
        # ≡ rect (the launcher/Settings/Editor bar), not the fixed crash-bar slot.
        bx, _by, bw, _bh = self.layout.sysmenu_btn
        fs = self.look.effective_font_scale()
        self.sysmenu.fs = fs          # rows hold fs-scaled text -> fs-scaled geometry
        pw = _POPUP_W * fs
        ax = bx + bw - pw
        self.sysmenu.anchor_x = max(0, min(ax, self.sys_canvas.w - pw))
        self.sysmenu.toggle(self.menu_ui._sysmenu_items())

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
    def tables(self):
        return self.project.tables

    @tables.setter
    def tables(self, value):
        self.project.tables = value

    @property
    def texts(self):
        return self.project.texts

    @texts.setter
    def texts(self, value):
        self.project.texts = value

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
    # every writer (ThemeLayer sets "theme"; open()/set_menu_view set the cart tabs)
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

    def defer(self, fn):
        """#184: schedule a heavy transition (cart start, editor open, PLAY)
        instead of running it inside the pointer walk. The tap frame paints its
        acknowledgment (selection highlight + the LOADING toast) and PRESENTS
        it (comp.flush() runs inside frame()); the queued `fn` then runs at
        that same frame's tail -- so the 1-2s a cart start costs happens
        behind a frame that already shows the tap landed, not behind a frozen
        stale shelf. defer() marks dirty so the acknowledgment frame always
        paints (the redraw gate can't skip it)."""
        self._deferred.append(fn)
        self._dirty = True             # the acknowledgment frame must paint

    def _run_deferred(self):
        """Run the deferred transitions queued BEFORE this drain started
        (frame()'s tail, after the flush presented the acknowledgment). A
        transition that defers another action leaves it for the next frame --
        its own result must paint first."""
        q = self._deferred
        for _ in range(len(q)):
            fn = q.pop(0)
            _t0 = _ticks_ms() if self.perf_capture else 0
            fn()
            if self.perf_capture:
                print("DEFER ms=%d fn=%s"
                      % (_ticks_diff(_ticks_ms(), _t0),
                         getattr(fn, "__name__", "?")))
        self._dirty = True             # the transition's result must paint

    def run(self, project, caller):
        """Show `project`'s running cart on the desktop, recording `caller` so the exit
        gesture knows where to return (spec Section 2's run/return -- a stack discipline,
        not a blocking call, since the frame loop can't block). The cart itself is started
        by the explicit _start() at each call site (open/apply/run_code/_leave_menu);
        run() makes the desktop layer active + records the caller. The launcher home root
        is one caller (pop == go_home); the Editor is the second (Stage 3), so PLAY
        returns to the same tab -- proving the Player is caller-agnostic."""
        if self.cart_error is not None and self._crash_to_code():
            return                     # the start already failed (syntax/init error):
                                       # no parked OOPS screen -- straight to the line
        self.crash_popup = None        # a clean launch retires any stale popup
        self._run_caller = caller
        # #178: the caller already says WHY this run is starting, so the windowed
        # tier can size the playtest window for the job -- an Editor PLAY is a dev
        # action (small, beside the code), a desk/Library run is play (as big as
        # fits). Stamped BEFORE the push that creates the window.
        self.wm.set_play_intent("dev" if caller is self.editor_app else "play")
        self.wm.goto("desktop")        # Stage 6e: push the Player process onto the back-stack

    def _exit_to_caller(self):
        """Pop the running cart back to whoever launched it (run()'s recorded caller,
        spec Section 2's launch-and-return). The Player's Stage-5 exit gestures
        (hold-BACKSPACE) calls this. The Editor is the second caller
        (Stage 3b): a cart run from PLAY returns to the Editor on the tab it left
        (screen -> "menu"; editor_app.tab is preserved -> the SAME tab), proving the
        Player has zero knowledge of who launched it. Any other caller (the launcher
        home root, or None) pops all the way home."""
        # Drop the dead run's world NOW (#66 repeat-run fragmentation fix): the
        # next cart must build into a compact heap, not around this one's corpse
        # (see Player.release_world's docstring for the measured mechanism).
        self.player.release_world()
        # Windowed WM (#73): closing the playtest must never truncate unrelated
        # windows stacked above it (e.g. Settings) -- the WM removes ONLY the
        # player and refocuses the caller's window. Desk world only (#105): a
        # play-world game (launched from the fullscreen Library) pops by the
        # fullscreen rules below, landing back in the Library via go_home.
        _cp = getattr(self.wm, "close_player", None)
        if _cp is not None and self.wm.desk_open():
            self._dirty = True
            _cp()
            return
        if self._run_caller is self.editor_app:
            self._dirty = True             # screen change repaints (#44)
            self.wm.goto("menu")           # Stage 6e: pop the Player, back to the Editor tab
            # #80: returning DIRECTLY to the code tab is not a tab CHANGE, so
            # set_menu_view's text-mode flip never fires here -- the keyboard
            # stayed in the cart's raw-matrix mode, where plain letters still
            # map but the sym layer doesn't exist (sym+digit typed NOTHING in
            # the code editor after a PLAY). Restore the returned-to tab's mode.
            self._set_text_mode(getattr(self.editor_app, "tab", None) == "code")
        else:
            self.go_home()

    def _crash_to_code(self):
        """A crashed cart run throws the kid STRAIGHT into the code editor on
        the crashing line, with a dismissible error popup (owner ask
        2026-07-23) -- replacing the parked OOPS panel + its manual TAP-CODE
        step at both crash sites (a failed start via run(), a mid-frame crash
        via Player._frame_running). Returns True when it navigated; False
        (no open project/cart -- nothing to edit) keeps the caller on the old
        panel fallback. The dead run's world is released exactly like a
        normal exit; cart_error/crash_line survive it (the editor's inline
        marker + the popup read them)."""
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
        err = self.cart_error or "crashed"
        line = self.crash_line
        self.player.release_world()
        # Windowed desk world (#73/#105): the crashed playtest window closes
        # like a normal exit (never truncating windows stacked above it);
        # the fullscreen tiers just re-route the back-stack below.
        _cp = getattr(self.wm, "close_player", None)
        if _cp is not None and self.wm.desk_open():
            _cp()
        self.editor_app.open(self.project)
        self.set_menu_view("code")
        if line is not None:
            # Land the caret on the line that raised (set_menu_view only marks
            # it when it BUILDS the editor -- an editor kept open across the
            # PLAY needs the explicit jump).
            self._mark_code_error(line - 1, err[:32])
        self.crash_popup = err
        self._dirty = True
        return True

    def exit(self):
        """Exit the active TASKBAR app back toward the launcher root (spec Section 9's
        context X, Stage 5): BarLayer.handle_bar_tap routes a tap on the right-zone X
        here. The launcher IS the back-stack root and never exits (its bar draws no X,
        so this is never reached with screen == "launcher"). A pre-Stage-6 shim over the
        screen strings: Settings closes via its own resume-or-home rule; the Editor (and
        any other taskbar app) goes home."""
        if self.wm.top_is("settings"):            # Stage 6d: ask the stack top
            self._exit_settings()
        elif not self.wm.top_is("launcher"):
            self.go_home()

    def _draw_cart_bar(self):
        """Draw the unified top bar over the CRASH frame (the only cart-path chrome left
        after Stage 5 retired the pause frame). The bar is the shell's, not the Player's,
        so its draw + the _pf_bar (CHROMEBRK) accounting stay here; the Player asks for it
        via this thin helper so player.py never reaches the bar surface directly (the
        Stage-2 isolation guarantee)."""
        _perf = self.perf_hud or self.perf_capture
        _tb = _ticks_us() if _perf else 0
        self.bar_layer._draw_status_strip("desktop")   # unified top bar (tool switcher)
        if _perf:
            self._pf_bar = _ticks_diff(_ticks_us(), _tb)   # CHROMEBRK: the bar's share (us)

    def _cart_bar_tap(self, px, py):
        """Route a CRASH-frame tap to the top-bar tool switcher (bar-owned), returning
        True iff a tool icon consumed it. Same isolation reason as _draw_cart_bar: the
        Player calls this instead of reaching the bar surface."""
        return self.bar_layer.handle_cart_tap(px, py)

    def frame_cap_fps(self):
        """The frame-loop cap for THIS moment (#63 frame pacing). DEFAULT:
        everything runs uncapped at the loop's 60 -- FPS_GOVERNOR ships False
        (see its block above; owner-affirmed twice). WHEN the governor or
        FRAMESKIP is on, a running GAME locks to a steady 30 (the SNES rule: a
        LOCKED cadence feels smoother than a swing -- most carts land in the
        29-45 band, and holding the fast frames to the slow ones' pace turns
        "38-55 and jittery" into "30 and rock solid") unless its manifest
        declares `"fps": 60` (Hop Quest, Sky Run). Tools/apps and every console
        screen keep 60 in every mode -- the pointer must stay responsive. The
        device loop re-reads this every iteration; the host simulator paces via
        its own --fps flag."""
        # #77 pairing (2026-08-10, learned on zoomed celeste): FRAMESKIP implies
        # the cap. The p8 ports pace THEMSELVES by frame-quantized dt with a
        # never-fast rule -- against an UNCAPPED skip loop (~30ms frames) the
        # quantizer must halve to avoid running fast, so skip made the game
        # SLOWER (20Hz -> 16.5Hz). Capped at 30, dt=33.3ms quantizes to
        # tick-every-frame: correct 30Hz logic, render at 15 -- the trade skip
        # promises. dt-driven carts are indifferent to the cap either way.
        if ((FPS_GOVERNOR or self.frameskip) and self.wm.top_is_player()
                and self.cart_error is None):
            cart = self.cart
            if cart is not None and cart.get("type") == "game":
                try:
                    f = int(cart.get("fps") or 30)
                except (TypeError, ValueError):
                    f = 30
                return 60 if f >= 60 else 30
        return 60

    def _running_cart_shows_bar(self):
        """True while a TOOL/APP cart is PLAYING (screen "desktop", not crashed): it runs
        WITH a minimal bar (title + status + context-X) so it's EXITABLE (Part 4), unlike a
        GAME which owns the full 320x240 with NO chrome and exits via hold-BACKSPACE. This
        is the bar-visibility-by-type rule: game -> hide the bar; tool/app -> show it. The
        Player reads it to decide whether to draw/route the tool bar and to suppress its
        own hold-to-exit gesture (a tool's BACKSPACE stays a free text key). False for
        games, the crash frame (the crash bar handles that), and every non-play screen.

        A GAME that reads text (textmode(True), e.g. the typing game Letter Blitz) does NOT
        get this bar -- forcing it over a game was rejected as a hack (it overlays the game's
        own top strip). Such a cart MUST provide its OWN exit instead: it calls quit()
        (make_api) from a key/affordance it draws. hold-BACKSPACE can't reach a text-mode
        cart (BACKSPACE is a typed 0x08 there, no keyboard autorepeat), so the exit is the
        cart's job -- see docs/moy_cart_api.md "A text-mode cart must provide its own exit"."""
        return (self.wm.top_is_player() and self.cart_error is None  # Stage 6d
                and self.cart is not None
                and self.cart.get("type") in ("tool", "app"))

    def _draw_tool_bar(self):
        """Draw the minimal TOOL bar over a running tool/app (Part 4). Same shell-owned
        draw + _pf_bar accounting as _draw_cart_bar; the Player asks for it via this thin
        helper so player.py never reaches the bar surface directly (Stage-2 isolation)."""
        _perf = self.perf_hud or self.perf_capture
        _tb = _ticks_us() if _perf else 0
        self.bar_layer._draw_status_strip("tool")   # minimal bar: title + status + X
        if _perf:
            self._pf_bar = _ticks_diff(_ticks_us(), _tb)   # CHROMEBRK: the bar's share (us)

    def _tool_bar_tap(self, px, py):
        """Route a running-TOOL tap (px, py in GAME coords) to the minimal bar: the
        context-X exits the tool, the wifi icon launches the wifi tool, the ≡ opens the
        system menu, the clock is the Easter egg. Returns True iff the bar consumed the tap
        (so the tool underneath doesn't also act on it). Reuses handle_bar_tap("tool") --
        owner is None for "tool", so a non-button tap falls through to the tool."""
        return self.bar_layer.handle_bar_tap("tool", px, py)

    def _draw_error_panel(self):
        # The on-canvas crash report moved to Player (Stage 2); this stays as the tested
        # ws. entry point the cards surface reuses for its own malformed-card panel
        # (cards_layer sets ws.cart_error then calls ws._draw_error_panel()).
        self.player._draw_error_panel()

    def _open_workspace(self, cart=None):
        # Build a fresh Project for `cart` (default: the launcher selection) + start it,
        # shared by open() [RUN, from a launcher tap, uses the launcher selection] and
        # open_in_editor() [EDIT, from the project-picker, which passes the PICKED cart].
        # Leaves the cart STARTED so PLAY can run it and the editors have live data.
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
        # INVARIANT: a workspace only ever opens a REAL cart. The launcher's pinned "Make"
        # pseudo tile is dispatched to the picker (launch_selected), never run -- but guard
        # the RUN/EDIT path anyway so a stray open() on the Make selection resolves to the
        # first real cart instead of crashing on a non-cart (path/cfg-less) tile.
        if cart is None or cart.get("path") is None:
            cart = next((c for c in self.launcher.items if c.get("path")), cart)
        prev = getattr(self, "_fat_cart", None)
        if prev is not None and prev is not cart:
            self.carts.reslim(prev)            # at most ~one fat cart stays live (#66)
        self.carts.rehydrate(cart)
        self._fat_cart = cart
        self.cart = cart
        self.config = dict(self.cart["cfg"])
        self.cards_layer.reset()      # fresh card selection/scroll for the new cart
        self.editor = None
        self.paint = None
        self.map_ui.reset()
        self.scene_ui.reset()
        self.music_ui.reset()
        self.block_ui.reset()
        self.cart_error = None
        self.save_status = None
        self.sheet = self._build_sheet()
        self.tilemap = self._build_tilemap()
        # No ws.flags projection: the legacy property-forward set can only
        # shrink (tests/test_console_facade.py), so the one reader -- the
        # Player, building the cart namespace -- goes through self.project.
        self.project.flags = self._build_flags()
        self.images = self.cart.get("images") or {}   # paint-image assets (#63)
        self.tables = self.cart.get("tables") or {}    # Sheets docs, table() (#78)
        self.texts = self.cart.get("texts") or {}      # Writer docs, text() (#78)
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

    def app_context(self, app_id, needs=(), prefs_ns=None):
        """Build the narrowed shell interface for one system app
        (docs/app_api_v1.md, runtime/app_context.py).

        `needs` is the app class's own `NEEDS` tuple, and `AppContext` attaches
        ONLY those roles -- so an app reaching for something it did not declare
        raises here rather than growing an invisible dependency. That filter is
        the whole point: Phase 7's `make_system_api(ctx, cart)` is this same
        call with a cart's manifest permissions in place of a class constant."""
        return AppContext(self, app_id, needs, prefs_ns)

    def _init_apps(self):
        """Construct and register every SYSTEM APP from its declaration.

        There is no per-app line in this file. `app_decls.APPS` is GENERATED
        from the `app` blocks in `system_carts/*/manifest.json`, so adding an
        app is a manifest plus a module -- never an edit here
        (docs/app_api_v1.md). The `<id>_app` attributes are kept because the
        shell and the apps address each other by them (`files_app` opens
        `ws.writer_app`), and because 100+ call sites use them.

        Declaration order IS dispatch precedence, which is why `order` lives in
        the manifest rather than being implied by a dict.
        """
        for d in APPS:
            cls = _resolve_app_entry(d["entry"])
            app = cls(self.app_context(d["id"], getattr(cls, "NEEDS", ())),
                      NAMES, _in)
            setattr(self, str(d["id"]) + "_app", app)
            ms = d.get("min_size")
            self.register_app(app, text_mode=bool(d.get("text_mode")),
                              min_size=(tuple(ms) if ms else None))

    def register_app(self, app, text_mode=False, min_size=None):
        """Register a SYSTEM APP (docs/app_api_v1.md). `app` is a content Layer
        exposing:

          id            -- the process kind (router / back-stack / window key)
          is_app(cart)  -- claim a launcher cart as this app's identity
          open()        -- (re)enter the app on every launch
          relayout(w, h, fs)  -- adopt a new canvas size / font scale

        `text_mode=True` marks a TYPING app (clean ASCII keyboard, the Writer
        precedent); `min_size=(w, h)` is the windowed-WM resize minimum in
        fs-scaled units (the ui.py convention). When omitted, MIN_W/MIN_H on
        the app's layout are adopted. TITLE supplies window/taskbar text. A
        launcher tap on the claimed cart opens the app instead of the Player;
        everything else (window chrome, theme tokens, toolkit) comes free."""
        if app.id in self._apps_by_id:
            raise ValueError("duplicate app id: " + str(app.id))
        self._apps.append((app, bool(text_mode)))
        self._apps_by_id[app.id] = app
        self._content_layers[app.id] = app
        self._app_titles[app.id] = str(getattr(app, "TITLE", app.id.upper()))
        if min_size is None:
            layout = getattr(app, "layout", None)
            min_w = getattr(layout, "MIN_W", None)
            min_h = getattr(layout, "MIN_H", None)
            if min_w is not None and min_h is not None:
                min_size = (int(min_w), int(min_h))
        if min_size is not None:
            self._app_min_sizes[app.id] = min_size
        hook = getattr(self.wm, "on_app_registered", None)
        if hook is not None:
            hook(app)
        # The picker HIDES carts an app claims (_picker_items, temporary until
        # #181), but apps register AFTER the store scan builds the grid -- so
        # re-derive it here or the first-built grid keeps the app carts it was
        # built without knowing about. Cheap: a handful of app registrations at
        # boot, each a list rebuild over the already-scanned carts.
        _carts = getattr(self, "carts", None)
        if _carts is not None and _carts.all and getattr(self, "picker", None):
            self.picker.set_items(self._picker_items(_carts.all))

    def app_min_size(self, kind):
        """The registered windowed resize minimum for app `kind`, or None."""
        return self._app_min_sizes.get(kind)

    def app_title(self, kind):
        """The registered app's requested window/taskbar title, or None."""
        return self._app_titles.get(kind)

    # -- the app bar contract: a HOST GUARANTEE, not a per-app ritual ---------
    #
    # On the fullscreen tiers a registered app shows the minimal exitable bar
    # (title + status + the context-X, spec shell_ux_v1.md Section 9). Every app
    # used to hand-write BOTH halves -- `_draw_status_strip("tool")` last in its
    # draw() and `handle_bar_tap("tool", ...)` first in its handle_pointer() --
    # and an app that forgot either became UNEXITABLE, silently, on device only.
    # The router already knows it is drawing a registered app, so it owns the
    # contract: frame()'s draw walk paints the strip AFTER the app's draw()
    # (chrome over content) and handle_pointer's walk routes the band BEFORE the
    # app's handle_pointer(). An app registered in future gets both for free,
    # including one that never heard of the bar -- pinned BEHAVIOURALLY (not by a
    # call-site count) in tests/test_app_api.py: a stub app that draws no strip
    # and routes no bar tap must still show the strip's pixels and still exit on
    # its context-X, and so must all seven shipped apps, parametrized.
    #
    # SCOPE, deliberately narrow: this owns the "tool" strip for REGISTERED APPS
    # ONLY. The other strip kinds -- "menu" (the Editor surfaces), "settings",
    # "home"/"picker" (launcher_layer), "desk" (wm_windowed) and "desktop" (the
    # running cart's crash chrome / a running TOOL CART's bar, _draw_tool_bar
    # above) -- stay with their surfaces. Collapsing the kinds would pick one and
    # silently break the context-X on the rest.
    def _app_bar_route(self, app, px, py):
        """Route a click at (px, py) against registered `app`'s bar band.

        Returns None when the tap is BELOW the band (the app sees it as usual),
        True when the bar consumed it, and False when it did not -- the band
        belongs to the bar either way, so a miss inside it is swallowed rather
        than handed down, which is what each app's `return bool(...)` did."""
        lay = getattr(app, "layout", None)
        band = getattr(lay, "bar_h", None)
        if band is None:                    # an app with no layout of its own
            band = self.bar_layer._bar_h("tool")
        if py >= band:
            return None
        # The context-X in that band is an EXIT path, so hard-commit first: an
        # app that persists on an idle debounce (#111) would otherwise lose the
        # last edit. Optional -- forgetting it costs an autosave, never the exit.
        commit = getattr(app, "commit", None)
        if commit is not None:
            commit()
        return bool(self.bar_layer.handle_bar_tap("tool", px, py))

    def open(self):
        # RUN landing (spec shell_ux_v1.md Section 2): build the workspace + run the
        # cart on the desktop, recording the launcher home as the caller so QUIT pops
        # home. Open to the desktop even if the cart failed to start: frame() shows the
        # error panel there so the kid isn't stranded (a silent stay-on-launcher would
        # be a dead end on the device). Authoring is a separate app (the Editor), reached
        # via the launcher's Make tile -> project-picker, not a tap-mode on the launcher.
        selected = self.launcher.selected()
        self.carts.note_recent(selected)   # #105 desk_mru: every launcher-tap run counts
        self.search_typing = False     # a RUN always ends any in-progress query typing
        # SYSTEM APPS (docs/app_api_v1.md): a cartridge identity backed by a
        # responsive system process. Deliberately NOT the Player: the Player is
        # the fixed 320x240 contract, while an app reflows to a P4/web window.
        for _app, _text in self._apps:
            if _app.is_app(selected):
                self.open_app(_app, selected)
                return
        self._open_workspace()
        self.run(self.project, self.launcher_layer)   # activate desktop, record caller

    def open_app(self, app, cart=None):
        """Spawn a registered system app on `cart` (default: the cart its
        is_app claims) -- the ONE app-launch dispatch, used by the launcher
        tap above and by app-to-app jumps (e.g. Files' OPEN -> Paint). A
        TYPING app (register_app text_mode=True, the Writer precedent) gets
        the clean ASCII keyboard after it opens; the rest are set to button
        mode BOTH ways, so a jump out of a typing app restores the raw
        keyboard. Returns False when no cart carries the app's identity."""
        if cart is None:
            for c in self.carts.all:
                if app.is_app(c):
                    cart = c
                    break
            if cart is None:
                return False
        text = False
        for _app, _text in self._apps:
            if _app is app:
                text = _text
                break
        self.cart = cart
        self.input.text_mode = False
        self.search_typing = False     # #105: an app jump ends any in-progress search typing
        app.open()
        self.wm.goto(app.id)
        self._set_text_mode(bool(text))
        self.ach.note("open", cart.get("path") or cart.get("title"))
        return True

    def is_system_app(self, cart):
        """True when a registered system app's identity claims `cart` -- the
        registry-side predicate (services use it to keep app carts out of
        project lists)."""
        for app, _text in self._apps:
            if app.is_app(cart):
                return True
        return False

    def open_in_editor(self, cart=None):
        # Open `cart` (default: the launcher selection) in the Editor, landing on Config
        # (spec Section 6). The cart is started (ready for PLAY) but not shown; the Editor
        # owns the screen until PLAY runs it. Reached from the Editor's PROJECT-PICKER, which
        # passes the PICKED cart -- never a launcher tap (a launcher tap always RUNS).
        self._ensure_desk()            # make verbs live in the make world (#105)
        self._open_workspace(cart)
        self.editor_app.open(self.project)

    def launch_selected(self):
        """A launcher TAP RUNS the selected cart (spec shell_ux_v1.md, the locked model:
        launcher tap = RUN, always, for every cart type). The one exception is the pinned
        "Make" pseudo tile (slot 0): authoring lives in the MAKE world -- on the windowed
        tier that is the DESK (two-worlds #105), on the fullscreen tiers the Editor's
        PROJECT-PICKER. Both Play and Edit stay reachable -- Play here, Edit through
        the Make tile."""
        sel = self.launcher.selected()
        if sel is not None and sel.get("type") == MAKE_TILE_TYPE:
            if getattr(self.wm, "has_desk", False):
                self.open_desk()
            else:
                self.open_picker()
            return
        self.open()

    def launch_named(self, name):
        """Run the cart `name` names, and return its TITLE (None if none match).

        The ONE lookup behind every remote launch: the serial dev channel's
        `run`, and the browser's PLAY ON DEVICE (moy_webhost's POST /run). Both
        arrive with a string somebody else chose, and the two obvious strings
        disagree -- the browser knows the cart by its TITLE (that is what rides
        every frame payload), a human at a serial prompt types part of a folder
        name, and title and folder differ on device by construction (the device
        seeds from the title slug while the host copies the source folder --
        `appearance.moy` vs `theme_picker.moy`, the mismatch #202's device-seed
        parity test exists for). So both are accepted, exact before partial:

            exact title -> exact folder (with or without .moy) -> title
            substring, in shelf order

        An empty `name` runs the first real cart, which is the dev channel's
        established `run` with no argument. Pseudo tiles (the pinned Make/New
        cards, which carry no store path) are never candidates."""
        # str(): `name` comes off a JSON body a browser wrote, so it is only a
        # string by convention -- a number there must miss, not raise inside the
        # frame loop's tail.
        want = str(name or "").strip().lower()
        items = getattr(self.launcher, "items", []) or []
        exact_title = None
        exact_folder = None
        partial = None
        for i in range(len(items)):
            it = items[i]
            path = it.get("path")
            if not path:
                continue                       # a pseudo tile is not a cart
            if not want:
                partial = i
                break
            title = str(it.get("title") or "").lower()
            folder = path.rsplit("/", 1)[-1].lower()
            if title == want and exact_title is None:
                exact_title = i
            elif (folder == want or folder == want + ".moy") \
                    and exact_folder is None:
                exact_folder = i
            elif want in title and partial is None:
                partial = i
        pick = exact_title
        if pick is None:
            pick = exact_folder
        if pick is None:
            pick = partial
        if pick is None:
            return None
        self.launcher.sel = pick
        self.launch_selected()
        # Return something TRUTHY whenever a launch actually happened, so a
        # caller (moy_webhost's /run) can tell "nothing matched" (None, above)
        # from "launched a cart whose title is empty" -- a titleless cart used to
        # report as a no-match AFTER it had already started on the glass.
        it = items[pick]
        return it.get("title") or it.get("path", "").rsplit("/", 1)[-1] or "cart"

    # -- the desk (two-worlds #105: the windowed tier's MAKE world) ----------

    def open_desk(self):
        """Enter the DESK -- the windowed tier's make world: wallpaper + system
        icons + taskbar, every app a window. The desk is the FLOOR of that
        world (its bar has no X); the PLAY icon is the way back to the
        fullscreen Library. Fullscreen tiers never call this (no has_desk)."""
        self._dirty = True
        self.search_typing = False     # #105: leaving the run-grid ends any query typing
        self._set_text_mode(False)
        # Re-derive the shelf under the two-worlds filter (system apps are
        # desk-only here): the first call runs before the WM swap finished
        # populating anything, so the boot-time entry settles the filter.
        self.launcher.set_items(self._launcher_view_items())
        self.wm.goto("desk")

    def open_library(self):
        """The desk's PLAY icon: drop to the fullscreen Library (the play
        world). Leaving the desk closes its windows (v1 -- autosave means
        nothing is lost); go_home also runs the leave-make-world cleanup
        (flushes Writer/Storybook, releases the world, re-slims the cart)."""
        self.go_home()

    def _ensure_desk(self):
        """Route a make verb (CHANGE / pick) through the desk on the windowed
        tier; a no-op when the desk is already open or on fullscreen tiers."""
        if getattr(self.wm, "has_desk", False) and not self.wm.desk_open():
            self.wm.goto("desk")

    def change_selected(self):
        """CHANGE (visual identity v1 Sections 1.2-1.3): open the launcher's selected
        cartridge IN PLACE in the Studio/Editor, landing on Config (or the gentlest
        editable tab when the cart has no edit schema -- editor_app.open decides,
        deterministically per project). The bridge from playing to making: same
        Project, same persistence path, never a surprise duplicate (Copy stays an
        explicit picker verb). No-op for the pseudo Make tile (one verb: its tap)."""
        sel = self.launcher.selected()
        if sel is None or sel.get("type") in PSEUDO_TILE_TYPES:
            return
        self.open_in_editor(sel)

    # -- the Editor's project-picker (spec shell_ux_v1.md) -------------------

    def open_picker(self):
        """Open the Editor's PROJECT-PICKER (the Make tile's target): a cart grid of every
        editable project + a "+ New" tile. Pushed on the back-stack, so its X pops home and
        picking a cart pushes the Editor above it (so the Editor's "projects" affordance
        returns HERE). Resets the picker's armed delete-confirm (if any) so a stale
        "DELETE? TAP AGAIN" from a previous visit never carries into a fresh one."""
        self._dirty = True             # screen change repaints (#44)
        self.search_typing = False     # #105: leaving the run-grid ends any query typing
        self._set_text_mode(False)     # a grid, not the code editor
        self.editor_picker.reset()
        self.wm.goto("picker")

    def pick_selected(self):
        """Open the picker's selected entry: a real cart -> open it in the Editor; the
        pinned "+ New" tile -> create a game + open it (spec shell_ux_v1.md)."""
        sel = self.picker.selected()
        if sel is not None and sel.get("type") == NEW_TILE_TYPE:
            self.new_cart_and_edit()
            return
        if sel is not None:
            self.open_in_editor(sel)

    def new_cart_and_edit(self):
        """The picker's "+ New": create a fresh GAME cart (`carts.new()` does the
        create, the re-scan and both grids), then open it in the Editor. A no-op
        on a read-only store (a device without SD writes) or a failed write --
        `carts.new()` returns None and the picker simply stays up."""
        new = self.carts.new()
        if new is None:
            return
        # Select the new cart in the picker so returning to it (the Editor's "projects"
        # affordance) lands on the freshly-created project, then open it in the Editor.
        for i, it in enumerate(self.picker.items):
            if it.get("path") == new.get("path"):
                self.picker.sel = i
                break
        self.open_in_editor(new)

    def launch_wifi_tool(self):
        """Launch the WiFi system tool -- the right-zone wifi icon's tap target (Part 3):
        find the wifi.moy tool in the launcher store, SELECT it, and RUN it (tools always
        launch, never the editor -- like launch_selected on a tool). Returns True iff it
        was found + launched; a no-op (False) when the tool isn't installed, so a device
        without it just doesn't respond to the tap rather than crashing. The launcher home
        is the run caller, so exiting the tool (its bar X) returns to wherever we were is
        NOT guaranteed -- it pops home, which is the safe default for an OS shortcut."""
        for i in range(len(self.launcher.items)):
            it = self.launcher.items[i]
            path = it.get("path") or ""
            if it.get("type") == "tool" and (path.endswith("wifi.moy")
                                             or path.endswith("wifi")):
                self.launcher.sel = i
                self.open()            # tools always LAUNCH (Part 2)
                return True
        return False

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

    # -- code / paint editors (#3, #4) ---------------------------------------

    def set_menu_view(self, view):
        # The tab builder moved to EditorApp.set_tab (Stage 3, editor_app.py); this
        # stays as the tested ws. entry point (cards_layer/block_editor_ui + tests +
        # the _open_* forwards call it).
        self.editor_app.set_tab(view)

    def _set_text_mode(self, on):
        # The code editor needs clean 1-byte ASCII (it reads last_key for typing);
        # a running cart wants the raw key matrix so a *held* direction keeps firing
        # (true hold-to-move -- the ASCII path reports each key once on the press
        # edge with no autorepeat). Flip the keyboard between the two on every screen
        # change. Raw needs keyboard fw >= 2025-06-12; without it the keyboard keeps
        # sending ASCII and TDeckKeyboard sticks on the 1-byte + hold-latch path, so
        # this is safe on any firmware. No-op on the host (no keyboard).
        # text_mode is the single source of truth for "typing, don't latch buttons":
        # the device keyboard, in ASCII, otherwise ALSO fires a typed key's game alias
        # (w/a/s/d/z/x -> up/left/down/right/a/b), so a typed name/password would
        # trigger d-pad/shortcut actions. Set it for the code editor too (on=True), and
        # clear it on every other screen (on=False) so it can never leak past the cart
        # /editor that asked for it -- the desktop frame re-derives keyboard mode from
        # input.text_mode (#38/#42). No-op on the host (no keyboard); harmless flag set.
        self.input.text_mode = bool(on)
        kb = self.keyboard
        if kb is not None:
            kb.set_game_mode(not on)

    def _sync_cart_text_mode(self):
        # Cart text input (#38/#42): a RUNNING cart opts into text-keyboard mode by
        # calling textmode(True) (make_api), which sets input.text_mode. Games leave it
        # off (the default) and keep the raw/game keyboard so a held direction keeps
        # firing btn(). When a cart asks for text mode we flip the keyboard to clean
        # 1-byte ASCII (set_game_mode(False)) so key()/keyp() yield typeable bytes;
        # when it turns text mode back off we restore game mode. Idempotent (the
        # keyboard's set_game_mode only talks to the HW on a real transition), called
        # each running-cart frame so a mid-cart textmode() toggle takes effect. No-op
        # on the host (no keyboard) -- there the same flag gates type_char routing in
        # ConsoleDriver. On older keyboard firmware set_game_mode(True) is a no-op
        # (stays ASCII) and the hold-latch fallback applies, so this is safe.
        want_text = bool(getattr(self.input, "text_mode", False))
        kb = self.keyboard
        if kb is not None:
            kb.set_game_mode(not want_text)

    # The per-tab landing entry points moved to EditorApp (Stage 3, editor_app.py);
    # these stay as the tested ws. entry points (bar_layer's tool switcher + the
    # player pause-B + tests call them). _open_menu is the Config-first landing --
    # EditorApp.open(project) -- so it forwards the open workspace.
    def _open_menu(self):
        self.editor_app.open(self.project)

    def _open_paint(self):
        self.editor_app.open_paint()

    def open_theme(self):
        # EDIT ICONS (#52): the theme editor's lifecycle lives on self.theme_layer now;
        # this stays as the reachable entry point (Settings + the device/tests call it).
        self.theme_layer.open()

    def _open_map(self):
        self.editor_app.open_map()

    def _open_scene(self):
        self.editor_app.open_scene()

    def _open_blocks(self):
        self.editor_app.open_blocks()

    def _open_music(self):
        self.editor_app.open_music()

    def _cart_has_handwritten_code(self):
        """True if the current cart's main.py is real, hand-written code that the
        block editor must not overwrite: there is non-trivial source AND it was NOT
        emitted by the block compiler (no BLOCK_MARKER) AND it isn't the throwaway
        new-cart template. A brand-new / template-only cart returns False, so a kid
        can freely start authoring it with blocks."""
        cart = self.cart
        if cart is None:
            return False
        src = cart.get("src") or ""
        if _blocks_mod.is_block_authored_source(src):
            return False                         # already block-authored main.py
        # The default new-cart template is fair game to blockify (it's boilerplate,
        # not the kid's own code) -- treat it as no real code.
        tmpl = getattr(self.carts_store, "NEW_TEMPLATE", None) if self.carts_store else None
        if tmpl is not None and src.strip() == str(tmpl.get("src", "")).strip():
            return False
        # Any remaining non-whitespace source is the kid's own code -> protect it.
        return bool(src.strip())

    def _leave_menu(self):
        # PLAY: leaving a tab RUNS the cart -- moved to EditorApp.leave (Stage 3,
        # editor_app.py). Stays as the tested ws. entry point (cards_layer/map/music/
        # block/code surfaces + host_app.escape + tests dispatch to it).
        self.editor_app.leave()

    def save_code(self):
        """Persist the edited source. Returns True iff it was written. A source
        that won't compile is REFUSED (the good file is left intact) and the
        syntax error is surfaced via self.save_status / cart_error rather than
        silently writing garbage. Non-SD carts (no path) just no-op True."""
        if not (self.editor and self.cart):
            return False
        src = self.editor.text()
        # Always compile-check, even for embedded/non-SD carts, so the kid sees a
        # syntax error before run_code execs it into a hard failure.
        ok, msg = self.carts_store.compile_check(src)
        if not ok:
            self.save_status = "SYNTAX " + msg
            self.cart_error = "Syntax error -- " + msg
            self._set_code_error(msg)        # mark the bad line in the editor (#24)
            return False
        self.code_err = None                 # parses now -> clear the inline marker
        self.code_err_row = None
        self.crash_line = None               # a re-run will re-detect any runtime crash
        if not (self.cart.get("path") and self.can_manage):
            self.save_status = None             # nothing to persist, but src is valid
            self.ach.note("code_save")          # "Code Wizard": valid code saved (#21)
            return True
        # The store-write half moved to Project.commit_code (Stage 1b); the compile-
        # check + code-UI half above stays here (the code surface).
        return self.project.commit_code(src)

    def _set_code_error(self, msg, move=True):
        """Record a syntax error so the code view can mark the offending line
        inline (#24). compile_check formats messages as "line N: <reason>"; pull
        N out for the marker, keep the short reason for the inline note, and move
        the caret onto that line so the fix is one tap away (`move=False` for
        the live typing re-check, which must never yank the caret)."""
        row = None
        short = msg
        if msg.startswith("line "):
            rest = msg[5:]
            p = rest.find(":")
            if p > 0 and rest[:p].strip().isdigit():
                row = int(rest[:p].strip()) - 1
                short = rest[p + 1:].strip()
        self._mark_code_error(row, short, move=move)

    def _mark_code_error(self, row, short, move=True):
        """Record an inline error marker (#24) and, if the editor is open, move
        the caret onto `row` (0-based) so the fix is one tap away. `move=False`
        (the live re-check while the kid types) updates the marker WITHOUT
        yanking the caret."""
        self.code_err = short
        self.code_err_row = row
        if move and row is not None and self.editor is not None:
            ed = self.editor
            ed.row = max(0, min(len(ed.lines) - 1, row))
            ed._clamp_col()
            ed._scroll()

    def run_code(self):
        # Refuse to run un-parseable source: keep the kid in the editor with the
        # syntax error shown rather than dropping to a blank/broken desktop.
        if self.editor is not None:
            if not self.save_code():
                return                               # syntax/save error -> stay in editor
            self.cart["src"] = self.editor.text()   # in-RAM apply (validated above)
        if self._start():
            self.ach.note("run")                # "Lift Off!": a cart was RUN (#21)
            self._set_text_mode(False)
            # PLAY from the code tab (Stage 3b): caller = the Editor, so the cart's
            # exit returns to the code tab (not the launcher home).
            self.run(self.project, self.editor_app)
        else:
            # Compiled but raised at exec/_init: show the error panel on the desktop
            # (still reachable -> the kid can reopen the editor to fix it).
            self.run(self.project, self.editor_app)

    # (The #111 bar UNDO/REDO pair, the code typing burst, the tab-scoped
    # journal walk and the post-walk workspace reload all live on
    # self.history now -- history_router.py, #209 landing E.)

    def save_sprites(self):
        # Store-write moved to Project.commit_sprites (Stage 1b); this stays as the
        # tested ws. entry point PaintLayer's SAVE dispatches to.
        self.project.commit_sprites()

    def _leave_theme(self):
        # CLOSE/back from the theme editor -> the lifecycle lives on self.theme_layer;
        # this stays reachable (PaintLayer's CLOSE tap dispatches ws._leave_theme()).
        self.theme_layer.leave()

    def save_map(self):
        # Store-write moved to Project.commit_map (Stage 1b); this stays as the tested
        # ws. entry point MapEditorUI's SAVE dispatches to.
        self.project.commit_map()

    def save_scene(self):
        # The scene tab's persist verb (#85 Stage 2): the editor serializes its
        # rows and commits through Project.commit_scene (atomic write + manifest
        # registration + the durable undo journal). The ws entry point the bar's
        # SAVE (EditorApp.save_current) dispatches to.
        self.scene_ui.save()

    # -- music / sound editor (#50) ------------------------------------------

    def save_sounds(self):
        # Store-write moved to Project.commit_sounds (Stage 1b); this stays as the
        # tested ws. entry point MusicEditorUI's SAVE dispatches to.
        self.project.commit_sounds()

    # -- cross-cart sprite reuse (#18) ---------------------------------------
    #
    # The shared sheet is a single .moygfx living beside the carts dir. PUT copies
    # the tile a kid is painting INTO that shared sheet; GET copies a tile back
    # OUT of it into whatever cart they're painting next -- so a sprite travels
    # between carts without being repainted. Both go through SpriteSheet.copy_tile
    # (the import primitive) and the moy_carts shared-sheet store.

    def _load_shared_sheet(self):
        """Read the shared sheet into a SpriteSheet (empty one if never saved).

        Spec-shaped (16 x 32) like every cart sheet -- explicit here because it is
        load-bearing twice over. It has to span all 512 tile ids or copy_tile()
        refuses a PUT/GET of anything past id 255 (which is what the old 16x16
        default did, silently, as "CAN'T PUT"); and it is a SpriteSheet like any
        other, so a shape libmoy would refuse has no business being one. An older
        128-line shared.moygfx parses into the top half with ids unchanged."""
        try:
            hexs = self._with_sd(lambda: self.carts_store.load_shared_sheet(self.carts_root))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte load shared sheet failed:", exc)
            return None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs, cols=16, rows=32)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet(16, 32)

    def share_tile_get(self):
        """Import the current tile FROM the shared sheet into this cart's sheet
        (same tile id). The kid then SAVEs the cart sheet to keep it."""
        if not (self.paint and self.sheet):
            return False
        shared = self._load_shared_sheet()
        if shared is None:
            self.paint_status = "NO SHARED"
            return False
        if shared.is_blank():
            self.paint_status = "SHARED EMPTY"   # nothing painted there yet
            return False
        n = self.paint.n
        if self.sheet.copy_tile(shared, n, dst_n=n) is None:
            self.paint_status = "CAN'T GET"
            return False
        self.paint_status = "GOT SPR " + str(n)
        return True

    def share_tile_put(self):
        """Save the current tile TO the shared sheet (persisted), so another cart
        can GET it. Loads the shared sheet, drops this tile in at the same id, and
        writes it back."""
        if not (self.paint and self.sheet):
            return False
        if not (self.carts_root and self.can_manage):
            self.paint_status = None             # writes deferred -- nothing to persist
            return False
        shared = self._load_shared_sheet()
        if shared is None:
            self.paint_status = "CAN'T PUT"
            return False
        n = self.paint.n
        if shared.copy_tile(self.sheet, n, dst_n=n) is None:
            self.paint_status = "CAN'T PUT"
            return False
        try:
            hexs = shared.to_hex()
            self._with_sd(lambda: self.carts_store.save_shared_sheet(hexs, self.carts_root))
        except Exception as exc:  # noqa: BLE001
            self.paint_status = "CAN'T PUT"
            print("Moybyte save shared sheet failed:", exc)
            return False
        self.paint_status = "PUT SPR " + str(n)
        return True

    def send_sprites_to_files(self):
        """Export the open cart's sprite sheet to files/sprites/ as a named user
        file (#108 the "send to Files" producer for the sprites kind). The whole
        sheet travels as one .moygfx (the same hex the cart stores), auto-named
        (sheet_1, ...) and browsable in the Files app; from there it re-imports
        into any project through the file picker (the #18 cross-cart reuse hub).
        Returns the stored file name, or None. Surfaces a paint status."""
        sheet = self.project.sheet if self.project is not None else None
        if sheet is None or not (self.carts_root and self.can_manage):
            self.paint_status = None       # writes deferred -- nothing to persist
            return None
        try:
            hexs = sheet.to_hex()

            def _write():
                name = self.carts_store.new_file_name("sprites", self.carts_root)
                return self.carts_store.save_file("sprites", name, hexs,
                                                  self.carts_root)
            name = self._with_sd(_write)
        except Exception as exc:  # noqa: BLE001 -- surface, never crash the editor
            self.paint_status = "CAN'T SEND"
            print("Moybyte send sprites to files failed:", exc)
            return None
        self.paint_status = "SENT " + str(name).upper()[:8]
        return name

    def apply(self):
        # GO (Config tab): re-run with the new config. Always return to the desktop:
        # on success it runs, on failure frame() paints the error panel there (still
        # reachable). PLAY from the Config tab (Stage 3b): caller = the Editor, so the
        # cart's exit returns to the Config cards, not the launcher home.
        ok = self._start()
        self.run(self.project, self.editor_app)
        if ok:
            self.ach.note("run")                # "Lift Off!": GO re-ran the cart (#21)
            self._save_config()

    def _save_config(self):
        # Moved to Project.commit_config (Stage 1b); this stays as the tested ws. name
        # apply() dispatches to.
        self.project.commit_config()

    def go_home(self):
        self._dirty = True             # screen change repaints (#44)
        self._set_text_mode(False)    # restore the game-button keyboard mode
        # (#111) autosave-only: going home is an exit path for every persistent
        # system app + the Editor, so each is persisted BEFORE the state below is
        # torn down (self.editor/self.project etc.) -- a HOME-key tap reaches
        # here directly, bypassing any per-app CLOSE affordance, and must never
        # drop an edit still sitting in its idle-debounce window.
        #
        # This used to be a hand-written ladder naming four apps and four
        # DIFFERENT verbs (writer.flush / storybook._commit_deck / sheets.flush /
        # artwork._save) -- the bar-contract bug class one level down: an app that
        # persists on a debounce and is not on the list loses the kid's work,
        # silently. `close()` is the uniform LEAVING hook now
        # (ui_refactor_2026-08 Phase 6, docs/app_api_v1.md); it is change-gated,
        # so this costs nothing for an app with nothing pending, and a future app
        # gets it by implementing one method instead of editing this file.
        for _app, _text in getattr(self, "_apps", ()):
            _close = getattr(_app, "close", None)
            if _close is not None:
                _close()
        _editor_app = getattr(self, "editor_app", None)
        if _editor_app is not None and _editor_app.project is self.project:
            _editor_app.save_current()
        if self._editing_icons:
            self.look.save_icons()     # the theme editor has no bar of its own
        self.editor = None
        self.paint = None
        self._editing_icons = False    # never carry the theme-editing flag home
        self.map_ui.reset()
        self.scene_ui.reset()
        self.block_ui.reset()
        self.wm.goto("launcher")       # Stage 6e: pop back to the launcher root
        self.cart = None
        # #66 fragmentation fix: ns = None alone kept the world alive through
        # player._update's closure; release clears the dict in place + collects.
        self.player.release_world()
        # #66 pin-field fix (the repeat-run CLIFF's other half): going HOME drops
        # the whole workspace. The fat cart re-slims NOW (not at the next open --
        # reopening the SAME cart otherwise stays fat forever, and its rehydrated
        # src/sprites strings are mid-heap pins that fragment every later run's
        # churn arena; reopen costs one SD read) and a FRESH empty Project
        # replaces the old one's sheet/tilemap/images/pmem (ws.project is
        # never-None by design, so surfaces keep their invariant). The EDITOR
        # return path (\_exit_to_caller -> menu) keeps both -- that's live
        # editing state, not a corpse.
        _fat = getattr(self, "_fat_cart", None)
        if _fat is not None:
            self.carts.reslim(_fat)
            self._fat_cart = None
        self.project = Project(self)
        self.cart_error = None
        self.save_status = None
        self.show_achievements = False
        self.ach_ui._konami_pos = 0          # fresh Konami run on the home desktop (#21)
        self.ach_ui._clock_taps = 0
        # #197: while WASM MODE is on, HOME is the connection screen. Here and
        # not at each exit site, because "return to the launcher" has many doors
        # -- the bar's context X, hold-BACKSPACE out of a game, an app's own
        # close, a crash -- and every one of them funnels through this method. A
        # per-door re-park is the bug class that left the T-Deck without a web
        # console at all; this is the last line of the one door they share.
        if self.web.parked:
            self.wm.goto("webconsole")

    # -- cart management (SD) ------------------------------------------------
    #
    # Each action mounts the SD card, mutates, and re-scans within a single
    # _with_sd session, then the card is unmounted before the next flush.

    def _launcher_items(self, carts):
        """The LAUNCHER run-grid entries: the pinned "Make" tile first (spec shell_ux_v1.md
        -- tap it to enter the MAKE world), then the runnable carts. WALLPAPERS
        are excluded -- they're a backdrop category chosen in Settings -> wallpaper, not
        run-grid apps (they stay in the Editor picker + the Settings wallpaper picker).
        On the windowed tier (#105 two worlds) SYSTEM-APP carts are excluded too: the
        Library is the game launcher; the tools live as desk icons/windows -- one rule
        a kid can hold ("apps are windows, games are fullscreen"). Kid-made "app"-type
        carts are NOT system apps and stay on the shelf (they run under the Player)."""
        # No Make tile on a read-only store (#151: the web runner/spec player
        # runs can_manage=False -- project management, and with it the whole
        # Editor entry point, is out of scope there). can_manage is wired
        # after construction, so runner boots rebuild launcher.items once
        # wiring is done (web_boot does).
        out = [make_tile()] if getattr(self, "can_manage", True) else []
        desk = getattr(self.wm, "has_desk", False)
        for c in carts:
            if c.get("type") == "wallpaper":
                continue
            if desk and self.is_system_app(c):
                continue
            out.append(c)
        return out

    def _launcher_view_items(self):
        """The launcher grid's current contents: `_launcher_items` narrowed by the
        active search query (#105), if any -- a plain case-insensitive substring
        match on the title. The pinned Make tile always survives a filter (it's
        not a cart to search for, it's the way to keep making one). The single
        place a rescan (carts.apply) and a query edit (set_search_query) both
        call, so the two can never desync on which list is "current"."""
        base = self._launcher_items(self.carts.all)
        q = self.search_query.strip().lower()
        if not q:
            return base
        return [it for it in base
                if it.get("type") in PSEUDO_TILE_TYPES
                or q in it.get("title", "").lower()]

    def open_search(self):
        """Enter the launcher search box (≡ -> SEARCH, #105): capture ASCII
        keystrokes into search_query, filtering the run-grid live. Mirrors the
        wifi-password typing idiom (_set_text_mode swaps the keyboard to clean
        ASCII while a query is being typed -- plain printable range only, so it
        works on the T-Deck's keyboard with no `=[]{}<>%` keys)."""
        self.search_typing = True
        self._set_text_mode(True)
        self._dirty = True

    def close_search(self, clear=True):
        """Leave the search box (ENTER/ESC while typing, or the sysmenu CLEAR
        SEARCH toggle). `clear=True` also drops the query and restores the full
        grid; `clear=False` (ENTER) just stops capturing keystrokes, leaving the
        filtered results up for d-pad/trackball browsing."""
        self.search_typing = False
        self._set_text_mode(False)
        if clear:
            self.search_query = ""
            self.launcher.set_items(self._launcher_view_items())
        self._dirty = True

    def toggle_search(self):
        """The sysmenu SEARCH row's action: open the box if idle, else close (and
        clear) it -- one control both opens and dismisses search."""
        if self.search_typing or self.search_query:
            self.close_search(clear=True)
        else:
            self.open_search()

    def set_search_query(self, q):
        """Replace the search query and re-filter the launcher grid (#105). The
        idle-frame perf floor stays intact: this only runs on a keystroke, never
        per-frame, and an unchanged (empty) query is the byte-identical original
        grid."""
        self.search_query = q
        self.launcher.set_items(self._launcher_view_items())
        self._dirty = True

    def _picker_items(self, carts):
        """The Editor PROJECT-PICKER grid entries: the pinned "+ New" tile first (create a
        game + open it), then every editable cart (wallpapers + built-ins included).

        SYSTEM-APP carts are the one exclusion, and it is TEMPORARY (owner call
        2026-07-31). Files/Paint/Writer/Sheets/Calc/Appearance are not really
        carts: the app is a shell MODULE (runtime/*_app.py, frozen on device),
        and the `.moy` only carries identity, icon art and a few-line fallback
        body for an older shell. Listing them offered a project whose "code" was
        that stub -- editing it changes nothing the kid can see. Hiding them is
        a placeholder for the real fix: #181 / docs/shell_architecture_v1.md
        "privileged system carts", where each app becomes a genuine cart running
        under make_system_api and this filter comes back out.

        Deliberately narrow: only carts an app CLAIMS as its identity are hidden
        (the same is_app() check the desk icons and launcher dispatch use), so a
        kid-made cart of type "app" -- which no shell app claims -- still shows.
        Note this departs from shell_ux_v1's "everything is editable"; the spec
        line is right again once #181 lands."""
        apps = getattr(self, "_apps", ())
        if apps:
            keep = []
            for c in carts:
                claimed = False
                for _app, _t in apps:
                    try:
                        if _app.is_app(c):
                            claimed = True
                            break
                    except Exception:  # noqa: BLE001 -- a bad claim never hides a cart
                        pass
                if not claimed:
                    keep.append(c)
            carts = keep
        return [new_tile()] + list(carts)

    def _real_selected(self, grid):
        """The selected cart on `grid`, or None if it's a pinned pseudo tile (Make/New)
        -- so cart management (dup/del) never acts on a non-cart."""
        sel = grid.selected()
        return sel if (sel and sel.get("path")) else None

    def adjust(self, d):
        # Config mutation stays on Workstation (ws.config is the single source of cart
        # state); the CARD selection lives on cards_layer, so read msel from there.
        f = self.cart["edit"][self.cards_layer.msel]
        # #94: a malformed field definition (bad type, min>max, missing/empty
        # choices, ...) must not crash left/right stepping -- this is the ONE
        # call site draw()'s try/except doesn't cover (a d-pad press routes
        # here directly, not through _draw_cards). _validate_field is the same
        # check cards_layer uses to swap the card for an inline "!" message; a
        # kid who navigated onto that card with up/down just can't step it.
        if self.cards_layer._validate_field(f):
            return
        key = f["key"]
        cur = self.config.get(key, f.get("default"))
        try:
            if f["type"] == "int":
                v = int(cur) + d * f.get("step", 1)
                if "min" in f:
                    v = max(f["min"], v)
                if "max" in f:
                    v = min(f["max"], v)
                self.config[key] = v
                self.project.record_config(key, cur, v)   # #111 phase 4
            elif f["type"] == "choice":
                ch = f["choices"]
                idx = ch.index(cur) if cur in ch else 0
                v = ch[(idx + d) % len(ch)]
                self.config[key] = v
                self.project.record_config(key, cur, v)   # #111 phase 4
        except (TypeError, ValueError, KeyError):  # noqa: BLE001 -- a bad current
            # value (e.g. a non-numeric default some other bug left behind) must
            # not crash the frame loop either; the -/+ just becomes a no-op.
            pass

    def _leave_or_home(self, leave):
        """The B-leaves-the-sub-view / HOME-goes-all-the-way-home shape shared by
        several menu_view input branches (theme/map/music/the default card editor)
        and the block-editor outline: B calls the view's own `leave` callback (it
        differs per view -- e.g. `_leave_theme` vs `_leave_menu`), HOME always goes
        to `go_home`. Verified identical at every call site before extracting."""
        i = self.input
        if i.pressed("b"):
            leave()
        elif i.pressed("home"):
            self.go_home()

    def handle_input(self):
        # Router (docs/history/shell_layers_refactor_v1.md §3): walk the z-ordered layer stack
        # top -> bottom and hand the frame's keys to the first layer that claims them.
        # A modal overlay (About / system menu, #52) sits above the content, so it eats
        # this frame's keys before they can leak to the screen underneath; the active
        # content layer is at the bottom and always consumes.
        i = self.input
        # Multiplayer (#65): advance every extra player slot's press-edge for this
        # frame, aligned with the local InputState.begin_frame() the driver already
        # ran. A no-op (empty loop) with no extra controllers registered, so the
        # single-player path costs one attribute read.
        _pr = getattr(i, "players", None)
        if _pr is not None:
            _pr.begin_frame()
        # Redraw-on-change (#44): a button PRESS edge or a typed key this frame may
        # change visible state (nav, select, screen/menu switch, an edit), so request a
        # repaint. Only the press edge (not release, not a steady hold) is marked: every
        # UI handler acts on i.pressed()/the typed key, never on the release, so a press
        # draws exactly one frame and the UI is static again -- a release/hold that
        # changes nothing costs nothing. Pointer-driven changes (click/drag/cursor move)
        # are caught separately in frame() via the pointer-state snapshot. Conservative
        # but never stale: a press that's a no-op costs one redraw, not a wrong screen.
        # EXCEPT when the keys belong to a RUNNING cart (wm.keys_to_cart): the cart's
        # viewport animates on its own and the chrome around it is unchanged by
        # cart-bound keys, so the mark would only force the windowed desktop's FULL
        # 1024x600 repaint instead of the quiet game-window blit. That mattered
        # because a BLE keyboard reports last_key as LEVEL state (a held byte, not
        # the T-Deck's one-shot press edge): ANY held/mashed key collapsed P4 play
        # from ~30fps to ~10 (measured; the "every keypress slows the game" bug).
        if getattr(i, "_pressed", None) or i.last_key:
            if not self.wm.keys_to_cart():
                self._dirty = True
        # Undo journal (Stage 7): any activity in the code editor (re)arms the idle
        # autosave-commit debounce -- frame() fires the durable commit once the kid
        # STOPS typing for _edit_debounce_ms, so the SD write lands in a gap. Marked
        # here (before routing) so it tracks the last keystroke regardless of who
        # consumes it; frame() only actually commits when the editor is dirty.
        if (self.editor is not None and self.wm.top_is("menu")
                and self.menu_view == "code"
                and (i.last_key or getattr(i, "_pressed", None))):
            self.history.edit_ms = _ticks_ms()
        # Walk the MEMOIZED visible stack top -> bottom (Stage 6c): the WM caches it
        # pre-reversed, so this hot per-frame routing allocates neither the list nor a
        # reversed() iterator on a static top-of-stack.
        for layer in self.wm.visible_stack_rev():
            if layer.handle_input(i):
                return

    # (The desktop/running-cart keyboard handler -- the #71 BACKSPACE-toggles-pause
    # logic -- moved to Player.handle_input (Stage 2, player.py); the "desktop"
    # content layer routes to it via _PlayerLayer.)

    # -- pointer (trackball-as-mouse) ----------------------------------------

    def handle_pointer(self):
        # Router (docs/history/shell_layers_refactor_v1.md §3): publish the game-space pointer
        # (so a cart's touch()/mouse() reads the 320x240 viewport, not the panel, #39),
        # then walk the z-ordered stack top -> bottom and let the first layer that
        # claims the tap handle it. A modal overlay (About / system menu) sits above the
        # content, so it consumes the tap (and clears the game pointer's tap so a running
        # cart never also sees a tap the menu swallowed) before it can leak underneath.
        p = self.pointer
        if p is None:
            return
        # #184: this whole call is ONE lump (`hp`) to the device loop, and a
        # 1.7-1.9s stall lives inside it that no stage timer names. Split it the
        # way #183's 37s `ws` lump was split -- pre-walk bookkeeping vs the
        # per-layer routing walk, with the dearest layer named. Overwritten in
        # place (never reallocated): this runs every loop iteration, and a fresh
        # list per frame would be measurement that creates the churn it measures.
        _perf = self.perf_capture      # the hp split is diag data; the HUD
        _pt0 = 0                       # must not pay the 6 stamps per frame
        if _perf:
            _pt0 = _ticks_us()
            _pf = self._pf_ptr
            _pf[0] = _pf[1] = _pf[2] = 0
            _pf[3] = _pf[4] = None
            _pf[5] = 0
        self._tick_pointer_dt(p)
        px, py, click = p.x, p.y, p.click
        gx, gy = self._game_xy(px, py)
        # Windowed WM (#73): while a window OTHER than the playtest holds input
        # focus, the game-space pointer publishes with click/down stripped, so a
        # background running cart never eats the taps meant for the editor beside
        # it. The fullscreen-stack WM has no hook -> unchanged.
        _pp = getattr(self.wm, "player_has_pointer", None)
        _live = _pp() if _pp is not None else True
        self.input.game_pointer = (gx, gy, click and _live, p.down and _live)
        if _perf:
            self._pf_ptr[1] = _ticks_diff(_ticks_us(), _pt0)   # pre-walk share
        # Idle fast-path (2026-08-03): while a healthy GAME owns the glass and
        # the pointer is doing NOTHING (no click, no held finger, not moved
        # since last frame), the layer walk below routes nothing -- the cart
        # already got its touch state via game_pointer above. Skipping it
        # reclaims ~1ms/frame on the S3. Any edge (move/click/down) walks, so
        # taps, drags and hover all behave exactly as before; the crash panel
        # (cart_error) always walks so EDIT stays reachable.
        if (not click and not p.down and not self._ptr_was_down
                and px == self._ptr_last_x and py == self._ptr_last_y
                and self.cart_error is None and self.wm.top_is_player()):
            return
        # _ptr_was_down makes the RELEASE frame walk: the windowed WM moves
        # focus on down->up, and that frame has no click, no down, no movement.
        self._ptr_was_down = p.down
        self._ptr_last_x = px
        self._ptr_last_y = py
        # THE APP BAR CONTRACT, input half (docs/app_api_v1.md): a tap in a
        # REGISTERED app's bar band belongs to the bar, and the router routes it
        # BEFORE the app's own handle_pointer -- checked inside the walk (not
        # ahead of it) so an open overlay above the content still gets the tap
        # first, exactly as when each app hand-wrote this as its first statement.
        # Only a CLICK can reach the bar, so a move/hover frame pays one compare.
        _appbar = self._apps_by_id if (click and not self.windowed_chrome) else None
        # Memoized, pre-reversed visible stack (Stage 6c) -- no per-frame allocation.
        for layer in self.wm.visible_stack_rev():
            if _appbar is not None:
                _app = _appbar.get(layer.id)
                if _app is not None:
                    _bar = self._app_bar_route(_app, px, py)
                    if _bar is not None:            # the band is the bar's
                        if _bar:
                            return
                        continue
            if not _perf:
                if layer.handle_pointer(px, py, click):
                    return
                continue
            _tk = _ticks_us()
            _claimed = layer.handle_pointer(px, py, click)
            _lus = _ticks_diff(_ticks_us(), _tk)
            _pf = self._pf_ptr
            _pf[5] += 1
            if _lus > _pf[2]:
                _pf[2], _pf[3] = _lus, layer.id
            if _claimed:
                _pf[4] = layer.id
                _pf[0] = _ticks_diff(_ticks_us(), _pt0)
                return
        if _perf:
            self._pf_ptr[0] = _ticks_diff(_ticks_us(), _pt0)

    def _tick_pointer_dt(self, p):
        """Charge this frame's loop tick to the pointer SAMPLE, for kinetic
        scrolling (#113).

        A backend whose touch controller can't produce a sample every frame
        marks the repeat frames `pointer.fresh = False` (the T-Deck's GT911
        stalls 20-45ms on most finger-down reads, #74, so a drag yields ~20-30
        samples/s against a 30-60fps loop). Two rules follow:

        * a stale frame gets `None`, which tells ScrollRegion.drag_move to leave
          the release velocity alone -- the finger did not stand still, we
          simply learned nothing about it, and averaging in a zero would decay
          a real fling toward a stop;
        * its time is BANKED and handed to the next real sample, so a finger
          delta spanning three frames is divided by three frames of time. Charge
          one frame's dt to it instead and the velocity reads ~3x too fast.

        On a backend that samples every frame (the host's mouse, the scripted
        remote gestures) every frame is fresh, so this is exactly
        `self._frame_dt_ms`. BOTH boards' GT911 drivers hold + flag: the P4's
        p4_input.Touch does it for the same reason device_input.Touch does,
        which it did not until 2026-08-15 -- it held the point and flagged
        nothing, so this docstring's old claim that the P4 "samples every frame"
        described the bug rather than the board.

        (A "third rule" -- charge stale stretches past a threshold as real dt
        so a silent still finger decays the fling -- was built here on
        2026-08-19 and REVERTED the same day, on data: the theory said the
        Guition's AXS15231 goes quiet when a finger rests, and a 5s
        held-still trace said otherwise -- 88% of hold frames FRESH, ~55Hz of
        same-position reports whose zero deltas decay the velocity exactly
        like the GT911's. The controller's only true silence is after a LIFT,
        and the defect that motivated the rule was the driver waiting the
        GT911's 400ms hold bound to believe one -- fixed where it belongs, in
        device/axs_touch.py's per-controller bound. The rule meanwhile killed
        real flicks dead: a lift's 400ms silence crossed the threshold and
        decayed the velocity to zero before the release ever fired.)"""
        dt = self._frame_dt_ms
        if getattr(p, "fresh", True):
            self._pointer_dt_ms = min(dt + self._stale_ptr_ms, 100.0)
            self._stale_ptr_ms = 0.0
        else:
            self._pointer_dt_ms = None
            self._stale_ptr_ms = min(self._stale_ptr_ms + dt, 100.0)

    # (The desktop/running-cart pointer handler -- the pause QUIT/CONTINUE + FPS-chip
    # tap + top-bar tool-switcher routing -- moved to Player.handle_pointer (Stage 2,
    # player.py); the "desktop" content layer routes to it via _PlayerLayer. The bar
    # draw/tap it needs stay on the shell, reached via _draw_cart_bar / _cart_bar_tap.)

    def nav(self, dx, dy):
        # Directional input (host arrows / device trackball). In the code editor it
        # moves the CARET (the view follows it); elsewhere the launcher/desktop are
        # pointer-driven, so this is a no-op there.
        if (self.wm.top_is("menu") and self.menu_view == "code"    # Stage 6d
                and self.editor is not None and (dx or dy)):
            self.editor.move(dy, dx)
            self._dirty = True             # caret moved -> redraw (#44)

    # -- frame + drawing -----------------------------------------------------

    def _reset_canvas_state(self):
        # Reset the canvas's TIC-80 draw state (camera/clip/pal/palt, #11) if the
        # backend supports it. Guarded so a backend without draw state (a test stub,
        # or a recording canvas) is a no-op. reset_state() also flushes any pending
        # auto-batch (#63), so cart sprites land before the console overlays draw.
        rs = getattr(self.canvas, "reset_state", None)
        if rs is not None:
            rs()

    def _draw_loading_toast(self):
        """#184: the deferred-transition acknowledgment -- a small top-center
        pill (the hold-to-exit toast's idiom) painted on the frame between a
        tap and its heavy transition. The panel retains this frame for the
        whole load stall, so the kid sees LOADING instead of a frozen shelf.
        Drawn on the system canvas above the whole stack; indexed API only
        (host == device == web)."""
        cv = self.sys_canvas
        fs = self.look.effective_font_scale()
        label = "LOADING..."
        w = (len(label) * 8 + 16) * fs
        h = 16 * fs
        x = (cv.w - w) // 2
        y = 24 * fs                    # clear of the 18px-per-fs top bar
        cv.rect(x, y, w, h, NAMES["black"])
        cv.rectb(x, y, w, h, NAMES["light_grey"])
        cv.print(label, x + 8 * fs, y + 4 * fs, NAMES["white"], fs)

    def _flush_batches(self):
        # Draw any sprites still pending in a canvas's auto-batch (Fold 1, #63) before
        # the frame is composited / flushed to the panel, so nothing queued by the last
        # spr() in a cart's _draw() (or the chrome) is left unpainted. Guarded + covers
        # both the game and system canvas -- one probe when they are the same object
        # (the 320x240 device case; #75: no per-frame tuple, no duplicate probe).
        cv = self.canvas
        fb = getattr(cv, "flush_batch", None)
        if fb is not None:
            fb()
        sc = self.sys_canvas
        if sc is not cv:
            fb = getattr(sc, "flush_batch", None)
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
        return self.wm.composite_game()

    # -- per-run cart canvas (SPEC.md 1/3.1) ---------------------------------

    def bind_run_canvas(self, w, h):
        """Bind a per-run GAME canvas for a cart whose manifest declares a
        smaller raster: the cart draws (and W/H report) the declared size, and
        the WM's viewport()/composite scale it up exactly like a cart-declared
        view -- on a shared-canvas tier (the T-Deck, where the boot canvas IS
        the glass) the boot canvas is promoted to SYSTEM canvas for the run, so
        `sc is gc` stops short-circuiting and the composite runs. Returns True
        when bound (or already native-size); False when this backend has no
        factory -- the caller refuses the cart cleanly (SPEC.md 3.1), never
        runs it at dimensions it did not ask for."""
        stock = self.canvas
        if w == stock.w and h == stock.h:
            return True                 # declared == native raster: nothing to bind
        mk = self.make_game_canvas
        if mk is None:
            return False
        small = self._run_canvas_cache.get((w, h))
        if small is None:
            try:
                small = mk(w, h)
            except Exception as exc:  # noqa: BLE001 -- an alloc failure refuses, not crashes
                print("Moybyte run canvas failed:", _err_text(exc))
                small = None
            if small is None:
                return False
            self._run_canvas_cache[(w, h)] = small
        self._run_canvas = small
        self._run_canvas_stock = stock
        self._run_canvas_shared = self._sys_canvas is None
        if self._run_canvas_shared:
            self._sys_canvas = stock    # promote: the boot canvas is the glass
        self.canvas = small
        return True

    def bind_app_canvas(self):
        """Bind the SYSTEM canvas as a RESPONSIVE app cart's draw surface (#181).

        The sibling of `bind_run_canvas`, and its opposite direction: that one
        gives a cart a SMALLER raster than the glass, this one gives an app cart
        the whole responsive system surface so `_layout(w, h, fs)` means
        something. Because `ws.canvas` then IS `ws.sys_canvas`, every downstream
        consumer degrades for free -- `composite_game` short-circuits on the
        identity check it has always had, `viewport()` reads (0, 0, 1) and
        `game_xy` becomes the identity, so the pointer arrives in the same
        coordinates the app drew in.

        Returns False on a SHARED-canvas tier (the T-Deck, where the boot canvas
        IS the glass and the two are already one object): there is nothing to
        bind and nothing to change, which is exactly why the handheld's pixels
        cannot move. Released by `release_run_canvas`, on every exit path.

        Also returns False in the windowed DESK world, and that one is not a
        degradation but a correctness rule -- measured, 2026-08-19. There a cart
        lives in a WINDOW, and `wm_windowed._draw_player_window` blits
        `ws.canvas` into it: with the system canvas bound, the window blits the
        screen into a rectangle OF that screen and the desktop renders as a
        recursive smear of its own bar. Giving a cart its own window surface is a
        `wm_windowed` change, which `docs/history/ui_refactor_2026-08.md` Section 6 puts
        out of scope -- so in the desk world a responsive cart keeps the fixed
        raster and is told (320, 240) by `_layout`, which is the truth. From the
        fullscreen Library (the play world, where `windowed_chrome` is False
        because the desk is popped) it gets the whole surface."""
        sc = self._sys_canvas
        if self.windowed_chrome:
            return False
        if sc is None or sc is self.canvas or self._run_canvas is not None:
            # ...or a cart-declared small canvas already holds the slot: a
            # manifest asking for 128x128 AND a _layout is contradictory, and
            # the declared size is the one with a SPEC contract behind it.
            return False
        self._run_canvas = sc
        self._run_canvas_stock = self.canvas
        self._run_canvas_shared = False
        self.canvas = sc
        self.app_full_canvas = True
        return True

    def release_run_canvas(self):
        """Undo bind_run_canvas / bind_app_canvas at run death
        (Player.release_world) -- the cart_quit pattern, idempotent.
        Identity-guarded so a backend that swapped ws.canvas mid-run (the
        web-view Tee) is never clobbered."""
        self.app_full_canvas = False
        stock = self._run_canvas_stock
        if stock is None:
            return
        if self.canvas is self._run_canvas:
            self.canvas = stock
        if self._run_canvas_shared and self._sys_canvas is stock:
            self._sys_canvas = None     # back to the shared-canvas degradation
        self._run_canvas = self._run_canvas_stock = None
        self._run_canvas_shared = False

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

    def _animating(self, dt):
        """True when SOMETHING on screen changes every frame on its own, so the UI
        must keep redrawing even without input:
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
        # A running cart on the desktop draws every frame (unless it crashed, when the
        # error panel is static). Stage 5 retired the pause frame, so there is no
        # paused-but-idle state to exclude here anymore.
        if kind == "desktop" and self.cart_error is None and (
                self._update is not None or self._draw is not None):
            return True
        # Windowed WM (#73): a running cart's WINDOW keeps animating even when
        # another window sits above it on the stack (Settings over a game, the
        # editor beside a playtest). No hook on the fullscreen-stack WM.
        _ka = getattr(self.wm, "keeps_animating", None)
        if _ka is not None and _ka(dt):
            return True
        # A music-editor preview must keep ticking the mixer + redrawing the PLAY/STOP
        # button (and clearing the flag when the effect ends) without input (#50).
        if kind == "menu" and self.menu_view == "music" \
                and self.music_ui.music_preview is not None:
            return True
        # A live wallpaper animates the home/settings/desk backdrop.
        if kind in ("launcher", "settings", "desk") and self.wallpaper.is_animating(dt):
            return True
        # The P4 Bluetooth keyboard picker advances through scan/pair/discovery
        # asynchronously. A static wallpaper would otherwise close the redraw
        # gate after its first frame and hide newly-found devices/status changes.
        if kind == "settings" and self.settings_layer.bluetooth_animating():
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
        # Transient overlays redraw while they're up. Three plain int reads (#209
        # landing B): the achievement objects wrote these at their event, so this
        # gate never calls into them -- and one clock read serves all three.
        now = _ticks_ms()
        if self._confetti_until and _ticks_diff(self._confetti_until, now) > 0:
            return True
        if self._egg_until and _ticks_diff(self._egg_until, now) > 0:
            return True
        if self._toast_until and _ticks_diff(self._toast_until, now) > 0:
            return True
        if self.notice_active():
            return True
        return False

    def _needs_redraw(self, dt):
        """Decide whether frame() must repaint+flush this frame. True when something
        marked the UI dirty, an animation source is live, or the pointer state the
        last frame drew has changed (cursor move/hide, tap, drag)."""
        if self._dirty:
            return True
        if self._animating(dt):
            return True
        if self._ptr_state() != self._last_ptr:
            return True
        return False

    # -- content-layer draw bodies (routed from the frame() stack loop) -------
    #
    # (The running-cart content body -- the cart tick + pause/crash chrome -- moved to
    # Player.tick (Stage 2, player.py). It still fills the DRAWBRK perf split ws._pf_*
    # exactly as before, and asks the shell for the top bar via _draw_cart_bar (which
    # keeps the _pf_bar CHROMEBRK accounting here). The "desktop" content layer routes
    # to it via _PlayerLayer.)

    # (_draw_menu_backdrop -- the frozen-cart backdrop under the cards/paint/map
    # panels -- was removed by the #39 step-3 conversions: every Editor tab is
    # system-domain now and always fully covered the backdrop anyway, so the tabs
    # just reset the game canvas's draw state and paint their own opaque body.)

    def frame(self, dt):
        # #172: bracket the frame's UNMEASURED edges. `draw` starts at _frame_t0,
        # which is after the journal idle tick, the splash check, the frameskip
        # branch and the redraw gate -- so all of that, plus the dirty/pointer
        # bookkeeping in the tail, sits inside the loop's `frm` but outside
        # DRAWBRK+flush. Comparing those two (an EMA against a windowed mean)
        # put the gap somewhere between -4 and +15ms, which is not a measurement.
        # Bracketing it is.
        # DEEP meters (frame edges, per-layer walk timing, per-op canvas timers,
        # the DRAWBRK/CHROMEBRK EMA tail) run ONLY under perf_capture -- the
        # measurement-session mode. perf_hud alone keeps the LIGHT set (frame
        # total, flush, fps): watching the fps chip must not cost milliseconds.
        # 2026-08-03: the deep set is post-ledger instrumentation, and with
        # run_desktop arming capture unconditionally it was ~1-1.5ms of every
        # frame on the S3 -- a real slice of the fps regression it existed to
        # find. Capture now follows Settings -> PERF DIAG on device.
        _fe0 = _ticks_us() if self.perf_capture else 0
        if dt > 0:
            # EMA so the readout reflects sustained rate, not single-frame jitter.
            self._fps = _ema(self._fps, 1.0 / dt)
            # The loop tick in ms for the input phase (which runs BEFORE frame()
            # and has no dt of its own): feeds the kinetic scroll velocity
            # (#113). Clamped so a hitch can't spike the physics.
            self._frame_dt_ms = min(dt * 1000.0, 100.0)
        # The cover collaborator's two per-frame touches: this budget reset and
        # the deferred drain at the tail. Bound once, here, so both cost a call
        # and no second lookup (#209 landing C -- nothing else in the loop
        # reaches into covers, and neither goes through a forward).
        covers = self.covers
        covers.begin_frame()          # the per-frame cover-build budget, which is
                                      # a TIME slice, not a count
        self._pf_home = None          # home-frame split (launcher_layer, perf_capture)
        # Undo journal (Stage 7): the idle-typing autosave debounce runs BEFORE the
        # redraw gate below, so it fires even on a static (redraw-skipped) editor
        # frame. The router is called DIRECTLY here (#209 landing E, doc 3b: the
        # frame loop never goes through a forward). Unlike `covers` above it is not
        # bound to a local -- one call site, so a local would be one store more
        # than the two attribute loads it saves nothing on.
        self.history.idle_tick()
        # Boot logo: expire the splash before the redraw gate so THIS frame reveals the
        # launcher. While it's live it's an _animating source, so the loop keeps flushing
        # it; marking dirty on expiry guarantees the launcher paints on the next frame.
        if self._splash_until is not None and _ticks_diff(self._splash_until, _ticks_ms()) <= 0:
            self._splash_until = None
            self._dirty = True
        # Frameskip (#77): with the gate ON and a GAME playing (not crashed), every
        # SECOND frame ticks only the cart's logic + audio (player.tick(render=False))
        # and presents nothing -- the panel simply retains the last frame, so the
        # whole render side (cart _draw + composite + flush -- measured to be
        # per-draw-call dispatch, the one tax left) is halved while input/logic keep
        # the full loop rate. Sits BEFORE the redraw gate so a skip frame never
        # consumes dirty state (a pending repaint in a background window survives to
        # the next rendered frame). Games only: tools/apps are event-driven (the
        # redraw gate already keeps them ~free) and their cursor must stay 60.
        # LOCKSTEP (#65): a linked game's world only changes on the shared 30Hz
        # tick, so drawing it at the panel's rate (40-55fps on the two S3
        # boards) repaints an identical frame one to two times in three. Holding
        # the last frame instead hands that time back to the loop -- and the
        # loop's regularity is what decides whether the input buffer can be cut,
        # because a slow frame is what makes a tick late. The Player still runs:
        # it re-sends the input packet on these frames.
        _np = self.netplay
        _linked = (_np is not None and self.wm.top_is_player()
                   and self.cart_error is None)
        if _linked and not _np.pending(_ticks_ms()):
            self.player.tick(dt, render=False)
            return
        # ...and a linked game does NOT also take the frameskip gate. Frameskip
        # is a phase toggle -- it renders every second LOOP frame, so it is half
        # of whatever the loop is doing (~20fps on the Guition's 40, ~27 on the
        # T-Deck's 55), not the 30Hz its own comment claims from back when the
        # loop ran at 60. Stacking it under lockstep would halve a rate that is
        # already the shared tick, for a saving that no longer exists: the whole
        # premise of frameskip is logic at the full loop rate, and here logic IS
        # 30Hz.
        if (not _linked and self.frameskip and self.wm.top_is_player()
                and self.cart_error is None
                and self.cart is not None and self.cart.get("type") == "game"):
            # phase True = render, False = logic-only; the setter resets it False,
            # so the first frame after a toggle (or a fresh run) always RENDERS.
            self._fs_phase = not self._fs_phase
            if not self._fs_phase:
                self.player.tick(dt, render=False)
                return
        else:
            self._fs_phase = False
        # Redraw-on-change (#44): a static UI screen (no animation, no pointer change,
        # nothing marked dirty) is skipped entirely -- no draw, no flush. The panel /
        # host window simply retains the last frame, so an idle UI costs ~0 and the
        # device saves the SPI flush + power. A running cart / live wallpaper / active
        # overlay always reports animating, so it redraws every frame as before.
        if not self._needs_redraw(dt):
            # Nothing to paint. Spend the frame warming the next cover instead
            # (#155) -- after two quiet frames, so the gap between two gestures
            # is not spent on a 108ms flash read. This is the ONLY work the idle
            # branch does, and it never dirties anything: the visible cards are
            # built by the draw path, the prefetch is for the ones off-screen.
            self._quiet_frames += 1
            if self._quiet_frames > 2:
                covers.prefetch_tick()
                # Mint the home retained-frame buffer off the paint path too
                # (idempotent after the first call; the device new_layer
                # pre-collects, ~150ms nobody should wait for).
                self.launcher_layer.prealloc_retained()
            return
        self._quiet_frames = 0
        # Perf HUD (#43/#44): mark the start of this frame's draw work. Cheap (one
        # ticks call); only meaningful for a frame we actually paint, so it's after
        # the redraw gate. _flush_ms is filled around comp.flush() below; _draw_ms
        # is the rest (total span - flush). Both EMA-smoothed at frame end. Also
        # fires when perf_capture is set (device diag sampling) -- not just the HUD.
        _perf = self.perf_hud or self.perf_capture
        _deep = self.perf_capture
        # MICROSECONDS (2026-08-14). Everything in the DRAWBRK/CHROMEBRK family
        # runs on this clock now; see _frame_perf_end for why the ms one was
        # manufacturing the remainder it was being read to explain.
        _frame_t0 = _ticks_us() if _perf else 0
        if _deep:
            # Everything from frame() entry to here: journal idle tick, splash
            # expiry, the frameskip branch, and the redraw gate itself.
            self._pf_pre = _ticks_diff(_ticks_us(), _fe0)
        _cmp = 0            # CHROMEBRK: _composite_game us
        _cur = 0            # CHROMEBRK: _draw_cursor us
        if _deep:
            _bc = getattr(self.canvas, "batch_reset", None)
            if _bc is not None:
                _bc()                  # #63: zero this frame's auto-batch profiling counters
            # Per-frame perf scratch (the running-cart content Layer fills self._pf_*).
            # #75: zeroed ONLY under _perf -- the writers (Player.tick / the bar draws)
            # only fill them under _perf too, and the reads below are _perf-gated, so a
            # kid-mode play frame skips the five attribute stores entirely.
            #
            # These stores MUST live in the _perf branch, not the elif below: a
            # 2026-07-26 edit nested them under "_prof just went off", so under
            # steady capture a frame whose writer didn't fire REPORTED THE
            # PREVIOUS WRITER'S VALUE -- launcher frames carried the last cart
            # frame's logic/render in every HITCH line, and DRAWBRK/CHROMEBRK
            # attribution after that date is suspect (found auditing #172).
            self._pf_upd = 0    # cart _update(dt) ms (game LOGIC); 0 off the cart path
            self._pf_cart = 0   # cart _draw() ms (RENDERING)
            self._pf_audio = 0  # audio.tick(dt) ms (mixer feed) -- split out from render
            self._pf_bar = 0    # CHROMEBRK: _draw_status_strip ms (cart path only)
            self._pf_bg = 0     # #172: backdrop restore (cart path only)
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
        # #172: per-layer draw cost (us) keyed by layer.id. CHROMEBRK's `other`
        # IS this walk -- on the 2026-07-29 T-Deck regression it was 6.7ms of a
        # Brick Siege frame with bar/cmp/cur all reading ~0.00, i.e. every named
        # bucket said "not me". Timing the walk names the layer directly instead
        # of narrowing again; a cost spread evenly across it says the stack
        # machinery, not one layer. Built only under _perf, so the kid-mode path
        # never allocates the dict.
        _lay = {} if _deep else None
        # WM-surface mark (Stage 9, docs/history/shell_ux_technical_plan_v1.md): when a RECORDING system
        # canvas is installed (the opt-in web view), tag each WM-stack surface so the recorder
        # slices the frame into ONE stream per surface (bar / app-content / player-viewport) --
        # the browser then composites them (a second WM backend). `begin_surface` exists only on
        # the recording canvas, so on the RAW canvas (the default) `_surf` is None: no call, no
        # allocation, byte-identical pixels -- the golden set can't move. Probed ONCE per frame.
        _surf = getattr(self.sys_canvas, "begin_surface", None)
        # Surface model §4 skip-draw (docs/surface_model_v1.md): a WM that
        # tracks surface gens may decline a gen-clean layer's draw entirely --
        # the recorder keeps its z-slot as a zero-width skip mark and the wire
        # ships a {"same":1} stub. Same probe shape as _surf: on the S3 the WM
        # has no surface_skip and the canvas no skip_surface -> two Nones, no
        # per-layer cost, byte-identical (the L6 no-op pattern).
        _lskip = getattr(self.wm, "surface_skip", None) if _surf is not None \
            else None
        _sksurf = getattr(self.sys_canvas, "skip_surface", None)
        # FULLSCREEN GAME-DOMAIN PLACEMENT on a COMMAND-ONLY game canvas (#175).
        # There the cart's frame has no pixels for _composite_game to scale up,
        # so its draw span must be BRACKETED instead -- otherwise every
        # fullscreen cart (and the play-world Library, which is game-domain
        # content) lands 1:1 at the desktop origin: the owner's "play menu loads
        # all games in the upper left corner". The bracket uses the WM's own
        # viewport geometry, i.e. exactly what the pixel composite would have
        # applied. Two getattr probes on a raw canvas -> None (the S3/device path
        # is byte-identical, and a raster game canvas keeps the composite).
        _view = None
        if (self._sys_canvas is not None
                and getattr(self.canvas, "buf", None) is None):
            _view = getattr(self.sys_canvas, "view", None)
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
        _lb = getattr(self.wm, "letterbox_inplace", None) if _vp is None else None
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
        # system app gets the minimal exitable "tool" strip drawn over its
        # content by the router -- the app draws no bar of its own. Resolved
        # ONCE per frame (the walk cannot change either term): `_apps_by_id`
        # while the fullscreen chrome rules apply, None in the windowed desk
        # world, where the WM's title strip carries the close instead. See
        # `_app_bar_route` for the input half and the scope note there.
        _appbar = self._apps_by_id if not self.windowed_chrome else None
        for layer in self.wm.draw_stack():          # memoized (Stage 6c) -- no per-frame alloc
            if _prev_domain == "game" and layer.domain == "system":
                if _game_open:                      # close the placement span
                    _view()
                    _game_open = False
                _tc = _ticks_us() if _deep else 0
                self._composite_game()
                _fold_live = True
                if _deep:
                    _cmp = _ticks_diff(_ticks_us(), _tc)   # CHROMEBRK: viewport composite
            if _fold_live and (layer is not self._cursor_layer
                               or (self.pointer is not None
                                   and self.pointer.visible)):
                _dsf = getattr(self.comp, "disarm_scale_fold", None)
                if _dsf is not None:
                    _dsf()
                _fold_live = False
            if (_lskip is not None and _sksurf is not None
                    and layer.domain == "system" and _lskip(layer.id)):
                _sksurf(layer.id, layer.domain)     # z-slot survives, draw doesn't
                _prev_domain = layer.domain
                continue
            if _surf is not None:
                _surf(layer.id, layer.domain)       # start this surface's command stream
            if _vp is not None and layer.domain == "game" and not _game_open:
                _ox, _oy, _sc = _vp()
                # LETTERBOX FIRST. The pixel composite fills the bezel AFTER the
                # cart draws (it owns the framebuffer); a command-only canvas
                # cannot -- that fill would wipe the frame already recorded, so
                # composite_game bails and the play world showed the LIBRARY
                # behind the game (owner, 2026-07-31). Painting it here, before
                # the bracket opens, is the same pixels in the right order.
                self.sys_canvas.cls(0)      # _VIEWPORT_BEZEL: black
                _view(_ox, _oy, _sc, self.canvas.w, self.canvas.h)
                _game_open = True
            if _lb is not None and layer.domain == "game" and not _lb_done:
                _lb()
                _lb_done = True
            if _lay is not None:
                _tk = _ticks_us()
                layer.draw(dt)
                if _appbar is not None and layer.id in _appbar:
                    self.bar_layer._draw_status_strip("tool")   # host guarantee
                _lus = _ticks_diff(_ticks_us(), _tk)
                # SUMMED, not assigned: the windowed WM draws several windows
                # that share one layer id, and each would otherwise clobber the
                # last -- exactly the case where the number has to be a total.
                _lay[layer.id] = _lay.get(layer.id, 0) + _lus
                if layer.id == "cursor":
                    _cur = _lus                     # CHROMEBRK: cursor (us)
            else:
                layer.draw(dt)
                if _appbar is not None and layer.id in _appbar:
                    self.bar_layer._draw_status_strip("tool")   # host guarantee
            _prev_domain = layer.domain
        if _deep:
            # Last PAINTED frame's split (the skip/quiet gates return above), so
            # it keeps the same "sample whenever you like" contract as DRAW2.
            self._pf_layers = _lay
            # ...and its TOTAL, which is what turns CHROMEBRK's `other` from a
            # residual into a partition: the walk is one measured bucket, and
            # what remains after it is the router machinery alone.
            _st = 0
            for _v in _lay.values():
                _st += _v
            self._pf_stack = _st
        if _game_open:                              # game was the TOP layer
            _view()
        if self._deferred:
            # #184: the acknowledgment frame -- a transition queued this
            # iteration paints its LOADING toast on top of everything; the
            # flush below presents it, and the frame TAIL then runs the
            # transition. The panel retains this frame for the whole stall.
            if _fold_live:                          # #190: toast paints the root
                _dsf = getattr(self.comp, "disarm_scale_fold", None)
                if _dsf is not None:
                    _dsf()
                _fold_live = False
            self._draw_loading_toast()
        # #63: nothing should be left in an auto-batch by the time we present. The cart
        # sprites were flushed at _reset_canvas_state; the console's own chrome draws
        # Images immediately (never queued). This final flush is the last-line guard so
        # a queued run can never survive to the next frame's cls().
        self._flush_batches()
        # Perf HUD (#43/#44): time the panel DMA flush in isolation, then back out
        # the draw span (everything before it this frame). On the host _NullComp the
        # flush is a near-zero no-op (no real panel), so flush reads ~0 and draw ~=
        # total -- the real flush-vs-draw split only shows on device. The flush call
        # is unconditional + identical either way; the timing is two cheap ticks
        # calls gated on perf_hud OR perf_capture (device diag sampling), so the
        # render path itself is unchanged.
        if _perf:
            self._frame_perf_end(_frame_t0, _cmp, _cur)
        else:
            self.comp.flush()
        # We painted this frame: clear the dirty flag and snapshot the pointer state
        # we just drew, so the NEXT frame only repaints if something changes again.
        self._dirty = False
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
        self._last_ptr = self._ptr_state()
        self._frames_drawn += 1
        if _deep:
            # The tail after the flush: dirty clear, the covers/fling re-arms,
            # and the pointer snapshot. Small by inspection -- measured so that
            # `pre` can be read as the whole of the unnamed edge, not a guess.
            self._pf_post = _ticks_diff(_ticks_us(), _fe0) - self._pf_pre \
                - int(self._raw_draw * 1000) - int(self._raw_flush * 1000)
        if self._deferred:
            # #184: the flush above PRESENTED this frame's LOADING
            # acknowledgment -- now run the queued transition(s) behind it.
            # Last thing in the frame so the stall sits outside every timing
            # bracket (the DEFER diag line names its cost instead).
            self._run_deferred()

    def _frame_perf_end(self, frame_t0, cmp_us, cur_us):
        """The #43/#44 perf-capture frame tail (extracted from frame() so the hot
        router stays readable): time the panel DMA flush in isolation, back out
        the draw span, and EMA the DRAWBRK/CHROMEBRK splits. Only called when
        perf_hud/perf_capture is on -- the kid-mode path flushes directly, so the
        render path itself is unchanged. The timing fields stay on the
        Workstation (the device diag contract -- perf_sample/perf_breakdown/
        perf_chrome read them).

        EVERY BRACKET IN HERE IS MICROSECONDS (2026-08-14), converted to ms once,
        at the EMA. It used to be ticks_ms, and that quietly broke the one number
        the shell's frame budget was being argued from. `chrome` is a residual
        (draw - upd - cart - audio) and `other` was a residual OF a residual
        (chrome - bar - cmp - cur): six integer-ms differences, each truncating
        toward zero, all of their loss landing in the last term. That is up to
        ~6ms of manufactured cost in a bucket that read ~7.6ms on the S3 and was
        the largest unexplained item in an 18ms frame -- i.e. the instrument was
        a plausible whole explanation for what it was being used to investigate.

        `other` is also no longer the last term. The stack walk is measured
        (self._pf_stack, us) and subtracted as `stk`, so what remains is the
        ROUTER itself -- the draw_stack walk, the surface/fold probes,
        _flush_batches, and this function's own bookkeeping -- and it is a
        partition, not a leftover."""
        _upd = self._pf_upd                     # us
        _cart = self._pf_cart                   # us
        _audio = self._pf_audio                 # us
        _bar = self._pf_bar                     # us
        _flush_t0 = _ticks_us()
        self.comp.flush()
        _flush = _ticks_diff(_ticks_us(), _flush_t0)
        _total = _ticks_diff(_ticks_us(), frame_t0)
        _draw = _total - _flush
        if _draw < 0:
            _draw = 0
        self._flush_ms = _ema(self._flush_ms, _flush / 1000.0)
        self._draw_ms = _ema(self._draw_ms, _draw / 1000.0)
        # Everything below is the DEEP tail (DRAWBRK/CHROMEBRK splits + the
        # HITCH logger's raw copies): diag-session data, and 6 boxed floats +
        # ~12 EMA calls of churn per frame -- perf_hud alone stops here (the
        # chip shows fps/draw/flush, all set above).
        if not self.perf_capture:
            return
        # DRAWBRK split: cart _update (logic) / cart _draw (render) / audio.tick /
        # console chrome (remainder = bar + cursor + overlays).
        _chrome = _draw - _upd - _cart - _audio
        if _chrome < 0:
            _chrome = 0
        # raw per-frame copies for the hitch logger (#66 HITCH v3), in ms
        self._raw_upd = _upd / 1000.0
        self._raw_cart = _cart / 1000.0
        self._raw_audio = _audio / 1000.0
        self._raw_chrome = _chrome / 1000.0
        self._raw_flush = _flush / 1000.0
        self._raw_draw = _draw / 1000.0
        self._upd_ms = _ema(self._upd_ms, self._raw_upd)
        self._cart_ms = _ema(self._cart_ms, self._raw_cart)
        self._audio_ms = _ema(self._audio_ms, self._raw_audio)
        self._chrome_ms = _ema(self._chrome_ms, self._raw_chrome)
        # CHROMEBRK sub-split (#66 lever 5): bar / composite / cursor / stack-walk
        # EMAs, so a chrome trim targets the real cost instead of guessing.
        #
        # `stk` is the layer walk MINUS the pieces already named: upd/cart/audio
        # and the bar all run inside layer.draw() (the cart's content layer, then
        # the shell bar the Player asks for), and the cursor is its own row. What
        # is left is every OTHER layer's draw plus the content layer's non-cart
        # tail. Double-counting here would push `other` negative and clamp it to
        # zero, which reads as "all accounted for" -- the failure mode this whole
        # change exists to remove -- so the subtraction is deliberate and the
        # clamp below is a floor, not a fit.
        _stk = self._pf_stack - _upd - _cart - _audio - _bar - cur_us
        if _stk < 0:
            _stk = 0
        self._bar_ms = _ema(self._bar_ms, _bar / 1000.0)
        self._cmp_ms = _ema(self._cmp_ms, cmp_us / 1000.0)
        self._cur_ms = _ema(self._cur_ms, cur_us / 1000.0)
        self._stk_ms = _ema(self._stk_ms, _stk / 1000.0)
        # #172: the declared-backdrop restore. NOT a fourth peer of the split --
        # it is already inside _cart_ms (Player.tick charges it to render, where
        # the cart's own cls would have landed). Tracked separately only so
        # DRAWBRK can say how much of render is the backdrop.
        self._bg_ms = _ema(self._bg_ms, self._pf_bg / 1000.0)

    # -- boot logo ------------------------------------------------------------

    def arm_splash(self, ms=None):
        """Show the moybyte boot logo for the next `ms` (default _SPLASH_MS) before the
        launcher appears. Called by the boot entries (device run_desktop, interactive
        host), NOT by construction -- so unit tests that drive frame() see the launcher
        on the first frame."""
        self._splash_until = _ticks_ms() + (int(ms) if ms else _SPLASH_MS)
        self._dirty = True

    def _splash_image(self):
        """The Moy mascot as a blittable (see the module-level splash_image)."""
        return splash_image()

    def _draw_splash(self):
        """Paint the boot logo on the system canvas (see draw_splash)."""
        draw_splash(self.sys_canvas)

    # -- desktop shell drawing (#28) -----------------------------------------

    # (The #71 pause chrome -- _draw_pause_dim / _draw_pause_buttons -- was retired in
    # Stage 5; the Player now exits on hold-BACKSPACE, with a transient
    # hold-progress toast in its place. See player.py.)

    def _mini_btn(self, label, rect, fill, cv=None):
        # Shared draw toolkit -- the implementation moved to ui.mini_btn (the
        # 2026-07 kernel-shrink direction); this stays the tested ws entry point.
        _uimod.mini_btn(cv if cv is not None else self.canvas, rect, label, fill)

    # _draw_fps / _fps_tap_rect / _draw_perf_hud (the HUD *rendering*) now live on
    # self.perf_ui (perf_hud.py, PerfHud). The perf *query* API below stays here --
    # it's the device diag's measurement contract (ws.perf_sample / perf_breakdown).

    def perf_sample(self):
        """Snapshot of the current per-frame perf numbers for offline sampling:
        (cart_name, fps, flush_ms, draw_ms). Used by the device backend's diag
        sampler (moy_runtime.run_desktop) to log a PERF line every few seconds
        while a cart runs. flush_ms/draw_ms are only meaningful when perf_capture
        (or perf_hud) is on -- run_desktop sets perf_capture=True at boot. Backend-
        agnostic + host-safe: pure reads, no drawing, no hardware. Returns None
        when no cart is actively running (nothing useful to sample)."""
        running = (self.wm.top_is_player() and self.cart is not None  # Stage 6d
                   and self.cart_error is None)
        if not running:
            return None
        cart = self.cart
        name = cart.get("title") or cart.get("path") or "?"
        return (name, self._fps, self._flush_ms, self._draw_ms)

    def perf_net(self):
        """The PERF line's `net=` witness: the #65 lockstep tick rate in ticks/s,
        or **None when no session is gating frames at all**.

        None is not "zero ticks" and must never be printed as 0 -- a board with
        no lever reports absence (the 2026-08-22 doctrine; `EspNowLink.status`
        answers the same way for the same reason). A running match reports a
        real rate: ~30 while it is healthy, lower under stall pressure, 0 while
        it is matched but frozen.

        This is the PERF emitters' ONE entry to the meter, because the meter
        CONSUMES its sample window (netplay.LockstepSession.tps) -- perf_sample()
        stays the `is a cart running?` probe half a dozen diag helpers call, and
        must not carry a number that a second caller would spend."""
        np = self.netplay
        return None if np is None else np.tps(_ticks_ms())

    def perf_breakdown(self):
        """(_upd_ms, _cart_ms, _audio_ms, _chrome_ms): the EMA phase split of draw_ms --
        cart _update (game LOGIC), cart _draw (RENDERING), audio.tick (mixer feed), and
        console chrome (bar + cursor + overlays, the remainder). Used by the device
        diag's DRAWBRK line to find where the per-frame draw cost actually goes (cart
        logic vs rendering vs audio vs chrome). Only meaningful while a cart runs with
        perf_capture/perf_hud on."""
        return (self._upd_ms, self._cart_ms, self._audio_ms, self._chrome_ms)

    def perf_breakdown_raw(self):
        """(upd, cart, audio, chrome, flush, draw) of the LAST drawn frame,
        un-smoothed (#66 HITCH v3). The EMA split (perf_breakdown) hides which
        phase a single hitch frame spent its time in; the hitch logger prints
        this instead. Only meaningful with perf_capture/perf_hud on."""
        return (self._raw_upd, self._raw_cart, self._raw_audio,
                self._raw_chrome, self._raw_flush, self._raw_draw)

    def perf_chrome(self):
        """(bar_ms, composite_ms, cursor_ms, stack_ms, other_ms): the EMA sub-split
        of the DRAWBRK chrome remainder (#66 lever 5).

        bar   the top status bar (_draw_status_strip)
        cmp   the game->system viewport composite (~0 when the canvases are one
              object, i.e. the 320x240 device)
        cur   the cursor layer
        stk   every OTHER layer's draw in the WM stack walk, plus the content
              layer's non-cart tail (2026-08-14)
        other what is left: the router itself -- the draw_stack walk, the
              surface/scale-fold probes, _flush_batches, the perf bookkeeping

        `stk` was added because `other` had become the answer to every question:
        it was a residual of a residual computed from six millisecond-quantized
        terms, so it collected both the real unnamed cost AND up to ~6ms of
        rounding, and on the S3 it read ~7.6ms with every named bucket at ~0.00.
        Both halves of that are fixed -- the brackets are microseconds now, and
        the biggest unnamed component is measured rather than inferred.

        Only meaningful while a cart runs with perf_capture/perf_hud on; feeds
        the device CHROMEBRK diag line so a chrome trim cuts the real cost."""
        other = (self._chrome_ms - self._bar_ms - self._cmp_ms - self._cur_ms
                 - self._stk_ms)
        if other < 0:
            other = 0.0
        return (self._bar_ms, self._cmp_ms, self._cur_ms, self._stk_ms, other)

    def perf_backdrop(self):
        """The EMA ms of the declared-backdrop restore (#172) -- `background()`'s
        per-frame repaint, run by Player.tick before the cart's _draw.

        A SUB-slice of perf_breakdown()'s render, not a fourth bucket: it is the
        cart's own drawing, standing in for the cls() it would otherwise make
        first thing. It used to fall outside every measured span and surface as
        CHROME, which on the T-Deck read as ~4.7ms of shell cost that no
        CHROMEBRK bucket could name. Feeds DRAWBRK's `bg=`."""
        return self._bg_ms

    def perf_frame_edges(self):
        """(pre_ms, post_ms): the parts of frame() OUTSIDE the measured draw span
        (#172).

        `draw` is timed from _frame_t0, which sits after the journal idle tick,
        the splash expiry, the frameskip branch and the redraw gate; the tail
        after the flush is outside it too. Both land inside the device loop's
        `frm` stage, so DRAWBRK + flush has never summed to a whole frame and the
        difference was being inferred by subtracting an EMA from a windowed mean
        -- which spread the answer across -4..+15ms. Feeds LAYERBRK."""
        return (self._pf_pre / 1000.0, self._pf_post / 1000.0)

    def perf_layers(self):
        """((layer_id, ms), ...) for the last PAINTED frame, dearest first (#172).

        The per-layer split of the WM stack walk -- which is precisely what
        CHROMEBRK reports as its unnamed `other` remainder. Not cart-gated (the
        launcher and editor walks are the ones with no other instrument at all).
        Empty tuple when perf capture has never painted a frame. Only meaningful
        with perf_capture/perf_hud on; feeds the device LAYERBRK diag line."""
        lay = self._pf_layers
        if not lay:
            return ()
        rows = [(lid, us / 1000.0) for lid, us in lay.items()]
        rows.sort(key=lambda r: -r[1])
        return tuple(rows)

    def perf_pointer(self):
        """(total_ms, pre_ms, worst_ms, worst_id, claim_id, n) for the last
        handle_pointer call (#184), or None if it never ran under capture.

        `pre` is the bookkeeping before the routing walk (_tick_pointer_dt +
        _game_xy + the focus probe), `worst`/`worst_id` the dearest single
        layer.handle_pointer in the walk, `claim_id` the layer that consumed the
        tap, `n` how many layers were visited. total - pre - worst says whether
        the cost was one layer or spread; a total far BELOW the loop's own hp=
        says the time went somewhere outside this method entirely."""
        pf = self._pf_ptr
        if not pf[5]:
            return None
        return (pf[0] / 1000.0, pf[1] / 1000.0, pf[2] / 1000.0,
                pf[3] or "-", pf[4] or "-", pf[5])

    def perf_batch(self):
        """(flushes, sprites, maxrun) for the auto-batch this frame (#63 profiling). N
        sprites coalesced into ONE blit_batch read flushes=1 / maxrun=N; drawn one-by-one
        read flushes=N / maxrun=1 -- so this PROVES the batch stayed intact at runtime,
        which pixel-parity can't. Counters reset per frame in frame() when perf capture is
        on; a lone item still counts as a (flushes=1, maxrun=1) direct blit."""
        cv = self.canvas
        return (getattr(cv, "_batch_flushes", 0),
                getattr(cv, "_batch_sprites", 0),
                getattr(cv, "_batch_maxrun", 0))

    # -- achievements + Easter-egg drawing (#21) -----------------------------

    # -- system notice banner (#53) ------------------------------------------

    def notice(self, title, sub="", kind="ok", ms=6000):
        """Say something on whatever screen is up, briefly, and then stop.

        For things the MACHINE did on its own -- the achievement toast next door
        is for things the kid did. It expires on a timer with no input, because a
        notice that needs dismissing is a modal, and a modal in front of a kid who
        just wanted to play is worse than the message is worth."""
        self._notice = (str(title), str(sub), kind)
        self._notice_until = _ticks_ms() + int(ms)
        self._dirty = True

    def notice_active(self, now=None):
        if self._notice is None:
            return False
        if _ticks_diff(self._notice_until, now if now is not None else _ticks_ms()) <= 0:
            self._notice = None
            return False
        return True

    def announce_update(self):
        """Put the firmware-update verdict on the desktop (#53).

        An update lands during a REBOOT: the screen that asked for it is gone by
        the time there is an answer, so unless the machine volunteers it the kid
        learns nothing -- a successful update looks like a slow reboot, and a
        rolled-back one looks exactly the same. Reading it here does NOT clear it;
        Settings -> UPDATE still has it for anyone who missed the banner."""
        u = getattr(self, "updater", None)
        verdict = getattr(u, "boot_verdict", None)
        if not verdict:
            return False
        if verdict[0] == "ok":
            self.notice("MOYBYTE UPDATED", "now %s" % u.version_label(), "ok")
        else:
            self.notice("UPDATE UNDONE", "still on %s" % u.version_label(), "warn")
        return True

    def _draw_notice(self):
        """The system banner: a wide strip under the top bar, title + one small line.

        Sized off `layout` rather than the frozen 320x240 numbers the achievement
        toast uses, because this one has to look deliberate on a 1024x600 desktop
        too."""
        cv = self.sys_canvas
        lay = self.layout
        fs = lay.fs
        title, sub, kind = self._notice
        th = self.theme_colors
        accent = th["play"] if kind == "ok" else NAMES["orange"]
        w = min(lay.w - 16 * fs, max(180 * fs, (len(title) + 2) * 8 * fs * 2))
        h = 34 * fs
        x = (lay.w - w) // 2
        y = lay.status_h + 6 * fs
        cv.rect(x, y, w, h, th["surface"])
        cv.rectb(x, y, w, h, accent)
        cv.rect(x, y, w, 3 * fs, accent)          # a lit edge, not a full title bar
        self._glyph("gear", (x + 5 * fs, y + 8 * fs, 14 * fs, 14 * fs), accent, cv)
        cv.print(title[:22], x + 22 * fs, y + 7 * fs, th["ink"], 2 * fs)
        if sub:
            cv.print(sub[:26], x + 22 * fs, y + 22 * fs, th["ink_dim"], 1 * fs)

    def _draw_toast(self):
        """A small celebratory banner near the top: a trophy + "ACHIEVEMENT!" + the
        achievement name + its glyph. Drawn last each frame over whatever screen is
        up, so it never disturbs the content beneath and expires on its own. Indexed
        API only (host == device)."""
        cv = self.sys_canvas
        ach_id, title, glyph = self.ach.toast
        x, y, w, h = 36, 26, 248, 38
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["yellow"])
        cv.rect(x, y, w, 12, NAMES["yellow"])
        self._glyph("trophy", (x + 2, y - 1, 12, 12), NAMES["black"], cv)
        cv.print("ACHIEVEMENT UNLOCKED!", x + 16, y + 2, NAMES["black"], 1)
        self._glyph(glyph, (x + 6, y + 16, 16, 16), NAMES["yellow"], cv)
        cv.print(title[:24], x + 28, y + 20, NAMES["white"], 2)

    # _draw_egg / _draw_confetti / _draw_achievements (the egg popup, Konami
    # confetti, and achievements-list overlay) now live on self.ach_ui
    # (achievements_ui.py, AchievementsUI). frame() calls self.ach_ui._draw_*.

    def _btn(self, label, rect, fill, cv=None):
        # Defaults to the GAME canvas (paint/map editors -- a 320x240 viewport); the
        # responsive code/block editors (#39 step 2) pass cv=self.sys_canvas. The
        # implementation (incl. the frozen scale-2 baseline quirk) moved to
        # ui.game_btn; this stays the tested ws entry point.
        _uimod.game_btn(cv if cv is not None else self.canvas, rect, label, fill)

    def _icon_btn(self, kind, label, rect, fill, cv=None):
        """A button that leads with an icon glyph (pre-literate) and keeps the
        word as a small secondary cue beside it -- so a reader still gets the
        label and a kid who can't read still gets the picture. Implementation:
        ui.game_icon_btn."""
        _uimod.game_icon_btn(cv if cv is not None else self.canvas, rect,
                             kind, label, fill, self._glyph)

    # (_draw_error_panel -- the on-canvas crash report -- moved to Player (Stage 2,
    # player.py): crash chrome is the Player's own UX, per spec Section 2's "guarantees
    # the cart will exit".)

    def _draw_cursor(self):
        # The pointer lives in SYSTEM-canvas space (it ranges over the panel size),
        # so the cursor draws on the system canvas, on TOP of the composited viewport
        # (#39). Scaled with the font so it stays visible on a big panel; at scale 1
        # / a 320x240 system canvas this is exactly today's 1x cursor on the canvas.
        if self._splash_until is not None:
            return                        # no cursor over the boot logo
        if self.pointer is not None and self.pointer.visible:
            self.sys_canvas.spr(CURSOR, self.pointer.x, self.pointer.y,
                            self.look.font_scale)

    def _glyph(self, kind, rect, c, cv=None):
        # Draw a centered icon glyph in color `c`. Defaults to the GAME canvas (the
        # editors/cart-overlay callers); the desktop/system callers pass cv=
        # self.sys_canvas so the glyph follows the system font scale (#39). The shared
        # blit + the glyph encoding live in the module-level _blit_glyph so Launcher
        # (canvas-only) renders the identical vocabulary.
        _blit_glyph(cv if cv is not None else self.canvas, kind, rect, c)

    def _wifi_icon_kind(self):
        """The right-zone wifi STATUS glyph (Part 3): "wifi" when the injected wifi
        service reports a live link, "wifi_off" (wifi-with-a-red-slash) when there's no
        connection. Reads the backend's status() -> (connected, ssid, ip); defaults to
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
        the sheet's own cache."""
        sheet = self.look.icon_sheet
        if sheet is None:
            return None
        slot = _ICON.get(kind)
        if slot is None:
            return None
        return sheet.tile_image(slot, transparent=0)

    def _icon(self, kind, x, y, cv=None):
        """Blit the top-bar icon `kind` (a 16x16 IconSheet sprite) at (x, y). The
        themeable replacement for _glyph on the bar; falls back to _glyph (the 12x12
        bitmap, centered) when the icon sheet/slot is missing, so the bar never crashes
        on a half-wired theme. cv defaults to the system canvas (the bar lives there);
        the running-cart bar passes the game canvas explicitly. The icon scales with
        the canvas's system font scale (#39) so it grows on a larger panel -- the GAME
        canvas is always scale 1, so the cart bar is byte-identical."""
        cv = cv if cv is not None else self.sys_canvas
        fs = getattr(cv, "font_scale", 1)
        if fs < 1:
            fs = 1
        img = self._bar_image(kind)
        if img is not None:
            cv.spr(img, x, y, fs)                        # 16px art upscaled by font scale
        else:
            self._glyph(kind, (x, y, _BAR_ICON * fs, _BAR_ICON * fs),
                        self.theme_colors.get("chrome_ink_dim", 6), cv)


def wire_workstation_core(ws, store, carts_root, make_api, wifi,
                          make_audio=None, lua_runtime=None, can_manage=None,
                          before_slim=None, pointer=None, inp=None,
                          keyboard=None):
    """The board-agnostic Workstation service wiring, in the ONE canonical order
    every backend used to hand-copy (host build_workstation + both boards'
    run_desktop -- the P4 literally carried a "same order as host" comment).
    The caller builds its board-specific backends (api/audio/wifi/lua/SD/OTA)
    and hands them in; board glue that must land between the store hookup and
    the #66 cart diet (the T-Deck's _with_sd + OTA updater) goes through
    `before_slim(ws)`. can_manage defaults to "the store root is known"; the
    host passes True. Ends with the three boot loads (system.json settings /
    icon theme / achievements) -- install a WindowedWM AFTER this returns, so
    the persisted font scale is applied before the root layout context is
    captured (#73/#58)."""
    ws.make_api = make_api
    if make_audio is not None:
        ws.make_audio = make_audio
    if lua_runtime is not None:
        ws.lua_runtime = lua_runtime
    ws.carts_store = store
    ws.carts_root = carts_root
    ws.can_manage = (carts_root is not None) if can_manage is None else can_manage
    ws.wifi = wifi
    if before_slim is not None:
        before_slim(ws)
    ws.carts.slim()   # #66 live-set diet: heavy payloads reload from the store
    if pointer is not None:
        ws.pointer = pointer
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
    ws.load_system()                # #28: system.json + the saved wallpaper
    ws.look.load_icon_sheet()       # Stage 1: the 16x16 bar IconSheet (theme or baked)
    ws.load_achievements()          # #21: unlocked badges survive reboots
