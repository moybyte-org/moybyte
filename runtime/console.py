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

from editors import (CodeEditor, IconSheet,
                     SpriteSheet, _SheetSprite)
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
        _BLK_TITLE_Y, _BLK_X0, _BLK_W, _BLK_Y0, _BLK_ROW_H, _BLK_INDENT, _BLK_ROWS,
        _BLK_AREA, _BLK_ADD, _BLK_DEL, _BLK_UP, _BLK_DN, _BLK_SAVE, _BLK_CODE,
        _BLK_CLOSE, _BLK_MENU, _BLK_MENU_ROW_H, _BLK_MENU_ROWS, _BLK_KBD,
        _BLK_KBD_DEL, _BLK_KBD_OK, _BLK_KBD_X, _BLK_NUM, _BLK_NUM_GX, _BLK_NUM_GY,
        _BLK_NUM_BW, _BLK_NUM_BH, _BLK_NUM_BPR, _BLK_NUM_KEYS, _BLK_NUM_DEL,
        _BLK_NUM_BLOCK, _BLK_NUM_OK, _BLK_NUM_X, _CAT_LABEL, _NEW_VAR_ITEM,
        _NEW_VAR_LABEL, _NEW_LIST_ITEM, _NEW_LIST_LABEL, _NUM_LITERAL_ITEM,
        _NUM_LITERAL_LABEL, _blk_plain_label, _BLK_HINTS,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.block_editor_ui import (
        BlockEditorUI, BlockLayout,
        _BLK_TITLE_Y, _BLK_X0, _BLK_W, _BLK_Y0, _BLK_ROW_H, _BLK_INDENT, _BLK_ROWS,
        _BLK_AREA, _BLK_ADD, _BLK_DEL, _BLK_UP, _BLK_DN, _BLK_SAVE, _BLK_CODE,
        _BLK_CLOSE, _BLK_MENU, _BLK_MENU_ROW_H, _BLK_MENU_ROWS, _BLK_KBD,
        _BLK_KBD_DEL, _BLK_KBD_OK, _BLK_KBD_X, _BLK_NUM, _BLK_NUM_GX, _BLK_NUM_GY,
        _BLK_NUM_BW, _BLK_NUM_BH, _BLK_NUM_BPR, _BLK_NUM_KEYS, _BLK_NUM_DEL,
        _BLK_NUM_BLOCK, _BLK_NUM_OK, _BLK_NUM_X, _CAT_LABEL, _NEW_VAR_ITEM,
        _NEW_VAR_LABEL, _NEW_LIST_ITEM, _NEW_LIST_LABEL, _NUM_LITERAL_ITEM,
        _NUM_LITERAL_LABEL, _blk_plain_label, _BLK_HINTS,
    )

# The map (tilemap) editor's UI layer (issue #32, extracted from this file): the
# panned view + tile palette + pan/zoom + gesture handling. Re-exported under
# their pre-extraction names (_MV_*, _TP_*, _MAP_ZOOM/_MAP_ERASE/_MAP_SAVE/
# _MAP_CLOSE/_MAP_PAN_THRESH, _PAN_*) for the same `console.X`/`C.X` reasons as
# the block editor above, with the same bare-or-package fallback.
try:
    from map_editor_ui import (
        MapEditorUI,
        _MV_X0, _MV_Y0, _MV_AVAIL_W, _MV_AVAIL_H, _MV_FIT_COLS, _MV_FIT_ROWS,
        _mv_default_cell, _MV_ZOOMS, _MAP_ZOOM, _TP_X0, _TP_Y0, _TP_CELL,
        _TP_COLS, _TP_ROWS, _TP_PAGE, _TP_AREA, _TP_PREV, _TP_NEXT, _TP_SKY,
        _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN, _MAP_ERASE, _MAP_SAVE, _MAP_CLOSE,
        _MAP_PAN_THRESH,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.map_editor_ui import (
        MapEditorUI,
        _MV_X0, _MV_Y0, _MV_AVAIL_W, _MV_AVAIL_H, _MV_FIT_COLS, _MV_FIT_ROWS,
        _mv_default_cell, _MV_ZOOMS, _MAP_ZOOM, _TP_X0, _TP_Y0, _TP_CELL,
        _TP_COLS, _TP_ROWS, _TP_PAGE, _TP_AREA, _TP_PREV, _TP_NEXT, _TP_SKY,
        _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN, _MAP_ERASE, _MAP_SAVE, _MAP_CLOSE,
        _MAP_PAN_THRESH,
    )

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
        _MU_PLAY, _MU_SAVE, _MU_LOOP, _MU_CLOSE, _MU_NOTE_NAMES, _MU_WAVE_LABELS,
        _mu_note_name, _mu_pad_rect,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.music_editor_ui import (
        MusicEditorUI,
        _MU_TITLE_Y, _MU_VIEW, _MU_LIST_X, _MU_LIST_Y0, _MU_ROW_H, _MU_ROWS,
        _MU_LIST_W, _MU_LIST_AREA, _MU_OBJ_PREV, _MU_OBJ_NEXT, _MU_PAD_X,
        _MU_PAD_Y, _MU_PAD_W, _MU_PAD_H, _MU_PAD_GAP, _MU_SPEED_DN, _MU_SPEED_UP,
        _MU_PLAY, _MU_SAVE, _MU_LOOP, _MU_CLOSE, _MU_NOTE_NAMES, _MU_WAVE_LABELS,
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
# load_achievements/_save_achievements/_achievement_unlocked) stays on Workstation
# (tested ws.ach.* + device ws.load_achievements()). Same bare-or-package fallback.
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
        _PerfLayer, _AchOverlayLayer, _SysMenuLayer, _AboutLayer)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layers import (
        _LegacyLayer, _PlayerLayer, _BlocksLayer, _UpdateLayer, _MapLayer, _MusicLayer,
        _PerfLayer, _AchOverlayLayer, _SysMenuLayer, _AboutLayer)

# The unified top bar + bottom dock surface (#46, extracted from this file -- see
# bar_layer.py). bar_layer.py is the SINGLE SOURCE of the bar/dock geometry constants
# (_STATUS_H / _BAR_* / the tool-switcher button rects / _DOCK_*); they're imported
# back here (re-exported under the same names) because console.py's own Layout + a few
# derived constants + the golden harness/tests reference them as console._X -- rather
# than duplicate them (drift), the same way block_editor_ui.py owns its _BLK_*. NAMES
# and _in are injected into the one BarLayer a Workstation builds (circular-import dodge).
try:
    from bar_layer import (
        BarLayer, _BAR_ICON, _BAR_GAP, _BAR_STRIDE, _BAR_Y, _SYSMENU_BTN, _HOME_BTN,
        _MENU_BTN, _PAINT_BTN, _MAP_BTN, _BLOCKS_BTN, _MUSIC_BTN, _BAR_BATT, _BAR_WIFI,
        _BAR_CLOCK, _STATUS_H, _DOCK_SLOTS, _DOCK_GLYPH, _DOCK_LABEL)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import (
        BarLayer, _BAR_ICON, _BAR_GAP, _BAR_STRIDE, _BAR_Y, _SYSMENU_BTN, _HOME_BTN,
        _MENU_BTN, _PAINT_BTN, _MAP_BTN, _BLOCKS_BTN, _MUSIC_BTN, _BAR_BATT, _BAR_WIFI,
        _BAR_CLOCK, _STATUS_H, _DOCK_SLOTS, _DOCK_GLYPH, _DOCK_LABEL)

# The "Make it mine" config-card editor surface (#3/#15, extracted -- see
# cards_layer.py). cards_layer.py is the single source of the card geometry constants
# (_RUN_BTN/_CODE_BTN/_CLOSE_BTN + _CARD_*); imported back here so tests + a couple of
# console call sites resolve console._X. CART STATE stays on Workstation: ws.config /
# ws.apply / ws.adjust; CardsLayer mutates ws.config in place + dispatches through them.
try:
    from cards_layer import (
        CardsLayer, _RUN_BTN, _CODE_BTN, _CLOSE_BTN, _CARD_X, _CARD_W, _CARD_Y0,
        _CARD_H, _CARD_VIEW_BOTTOM, _CARD_SCROLL_UP, _CARD_SCROLL_DN)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cards_layer import (
        CardsLayer, _RUN_BTN, _CODE_BTN, _CLOSE_BTN, _CARD_X, _CARD_W, _CARD_Y0,
        _CARD_H, _CARD_VIEW_BOTTOM, _CARD_SCROLL_UP, _CARD_SCROLL_DN)

# The sprite/icon PAINT editor surface (#4/#30, extracted -- see paint_layer.py). ONE
# renderer serves both the cart sprite sheet (menu_view=="paint") and the system icon
# sheet (menu_view=="theme", EDIT ICONS), keyed on ws._editing_icons. paint_layer.py is
# the single source of the paint geometry constants (_PG_*/_SW*/_SPR_*/_PAINT_*),
# imported back here for tests + tools. The SHEETS + ws.paint handle + save persistence
# stay on Workstation; PaintLayer reads them + dispatches SAVE/GET/PUT/CLOSE to ws.
try:
    from paint_layer import (
        PaintLayer, ThemeLayer, _PG_X0, _PG_Y0, _PG_CELL, _PG_SPAN, _PG_AREA, _SW_X0,
        _SW_Y0, _SW, _SW_COLS, _SW_AREA, _SPR_PREV, _SPR_NEXT, _PAINT_SIZE, _PAINT_SAVE,
        _PAINT_CLOSE, _PAINT_GET, _PAINT_PUT)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.paint_layer import (
        PaintLayer, ThemeLayer, _PG_X0, _PG_Y0, _PG_CELL, _PG_SPAN, _PG_AREA, _SW_X0,
        _SW_Y0, _SW, _SW_COLS, _SW_AREA, _SPR_PREV, _SPR_NEXT, _PAINT_SIZE, _PAINT_SAVE,
        _PAINT_CLOSE, _PAINT_GET, _PAINT_PUT)

# The Settings app surface (#28/#39/#53, extracted -- see settings_layer.py). The
# aggregator: rows + scroll + drawing move to SettingsLayer, which owns NO config -- it
# reads ws state (system/wallpaper_id/font_scale/diag_live/web_hook) and dispatches every
# mutation to ws setters; the wallpaper cluster stays single-sourced on ws (the launcher
# shares that backdrop). settings_layer.py is the single source of the _SET_* geometry
# constants (also used by console's Layout), imported back here for Layout + tests.
try:
    from settings_layer import (
        SettingsLayer, _SET_X, _SET_W, _SET_ROW_Y0, _SET_ROW_H, _SET_BACK, _SET_ACH,
        _SET_TITLE_HIT)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.settings_layer import (
        SettingsLayer, _SET_X, _SET_W, _SET_ROW_Y0, _SET_ROW_H, _SET_BACK, _SET_ACH,
        _SET_TITLE_HIT)

# The Python code editor surface (#24/#39, extracted -- see code_layer.py). CodeLayer
# owns the full-screen text view + drawing + code-UI state; the shared ws.editor handle
# (like ws.paint) + save_code/run_code + the code-error state + code_layout stay on ws.
# code_layer.py is the single source of the code geometry constants (_CODE_*/_ED_*/
# _SYM_*/_CODE_SYMBOLS) + the MicroPython-safe syntax highlighter, imported back here for
# console's CodeLayout + the crash panel (_CODE_LH) + tests.
try:
    from code_layer import (
        CodeLayer, _CODE_X0, _CODE_Y0, _CODE_LH, _CODE_AREA, _ED_RUN, _ED_SAVE, _ED_CLOSE,
        _CODE_SYMBOLS, _SYM_Y, _SYM_H, _SYM_CELL, _SYM_AREA)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import (
        CodeLayer, _CODE_X0, _CODE_Y0, _CODE_LH, _CODE_AREA, _ED_RUN, _ED_SAVE, _ED_CLOSE,
        _CODE_SYMBOLS, _SYM_Y, _SYM_H, _SYM_CELL, _SYM_AREA)

# Self-contained support widgets (extracted -- see widgets.py): the cursor blittable
# _Blit, the Pointer cursor, the Achievements milestone tracker (+ its ACHIEVEMENTS
# catalog), Pmem (cart persistent RAM), the _SilentAudio no-op backend, and the reusable
# Popup dropdown. A dependency-free leaf; imported back here so console.Pointer /
# console.Popup / console.ACHIEVEMENTS / ... resolve for Workstation + host_app + tests.
try:
    from widgets import (
        _Blit, Pointer, Achievements, Pmem, _SilentAudio, Popup, ACHIEVEMENTS,
        _PLAY_GOAL, _POPUP_X, _POPUP_Y, _POPUP_W, _POPUP_ROW_H, _POPUP_PAD_X, _POPUP_SEP_H)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import (
        _Blit, Pointer, Achievements, Pmem, _SilentAudio, Popup, ACHIEVEMENTS,
        _PLAY_GOAL, _POPUP_X, _POPUP_Y, _POPUP_W, _POPUP_ROW_H, _POPUP_PAD_X, _POPUP_SEP_H)

# The desktop wallpaper backdrop component (#28, extracted -- see wallpaper.py). The
# SHARED backdrop the launcher home + Settings both draw (ws.wallpaper.draw). It owns
# the rendering + the compiled-cart cache; ws.wallpaper_id + the picker/query API stay
# on Workstation as the single source (select_wallpaper drives the component).
try:
    from wallpaper import Wallpaper
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wallpaper import Wallpaper

# The desktop home / launcher surface (#28, extracted -- see launcher_layer.py): the
# Launcher grid CLASS (its instance stays ws.launcher, the single source everything
# reads) + LauncherHomeLayer (the "launcher" content Layer -- home composition + grid
# nav). Launcher takes NAMES + _blit_glyph injected for its tile art; ws.open() stays.
try:
    from launcher_layer import Launcher, LauncherHomeLayer
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.launcher_layer import Launcher, LauncherHomeLayer

# The open cart's live WORKSPACE (Stage 1 of docs/shell_ux_technical_plan_v1.md,
# extracted from this file -- see project.py). Project holds the open cart's DATA
# (cart/config/sheet/tilemap/images/pmem) + the builders + the commit_* persistence
# verbs; the six data fields are exposed back here as forwarding properties, so every
# surface file + test is unchanged. Project keeps a `ws` back-reference (the seam the
# plan keeps for Stage 1) and reaches ws.<X> for the Workstation-owned deps.
try:
    from project import Project
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.project import Project

# The cart PLAYER (Stage 2 of docs/shell_ux_technical_plan_v1.md, extracted from this
# file -- see player.py). Player is the run-loop black box: start a cart under the
# frozen make_api, tick it, feed it input, guarantee it exits. The "desktop" content
# layer delegates to the one ws.player; ws._start stays a one-line forward, and the
# nine cart-run fields the Player owns (cart_error/crash_line/cart_paused/ns/_update/
# _draw/_cart_key_prev + the pause-only _bks_prev/_cart_start_ms) are exposed back as
# forwarding properties, so every surface file + test is unchanged. The pause-button
# geometry moved with the pause machinery and is re-exported here so console._PAUSE_*
# still resolves for tests. Same bare-or-package fallback as project.py.
try:
    from player import (Player, _PAUSE_BTN_W, _PAUSE_BTN_H, _PAUSE_BTN_GAP,
                        _PAUSE_BTN_Y, _PAUSE_CONTINUE_BTN, _PAUSE_QUIT_BTN)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.player import (Player, _PAUSE_BTN_W, _PAUSE_BTN_H, _PAUSE_BTN_GAP,
                        _PAUSE_BTN_Y, _PAUSE_CONTINUE_BTN, _PAUSE_QUIT_BTN)

# The EDITOR app (Stage 3 of docs/shell_ux_technical_plan_v1.md, extracted from this
# file -- see editor_app.py). EditorApp owns the tab ladder (Config -> Blocks -> Code
# -> Sprites -> Map -> Music) + the current-tab state (EditorApp.tab) + the lazy tab
# builders + the PLAY trigger. ws.menu_view becomes a forwarding projection of
# EditorApp.tab (the string-keyed router's key, unchanged); ws.set_menu_view/_open_*/
# _leave_menu stay one-line forwards (tested surface). Same bare-or-package fallback.
try:
    from editor_app import EditorApp
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editor_app import EditorApp

# The block vocabulary/compiler (#29). Imported under whichever name it's known by:
# bare `blocks` on the device (frozen top-level) and on the host once host_app has
# aliased it, or `runtime.blocks` when a test loads console/moy_runtime directly
# without that alias (the device path is plain `import blocks`). Mirrors
# moy_carts._import_blocks so neither module hard-depends on import order.
try:
    import blocks as _blocks_mod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import blocks as _blocks_mod


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


# (_wrap + _exc_cart_line -- word-wrap the crash text + find a cart traceback line --
# moved to player.py (Stage 2) with the crash panel + Player.start, their only users.)


# (_Blit -- the minimal cursor/composite blittable -- moved to widgets.py, imported
# back above; _from_ascii below + the #39 composite build it.)


def _from_ascii(rows, mapping, transparent="."):
    h = len(rows)
    w = max(len(r) for r in rows) if rows else 0
    pix = []
    for y in range(h):
        row = rows[y]
        for x in range(w):
            ch = row[x] if x < len(row) else transparent
            pix.append(-1 if ch == transparent else (mapping[ch] & 63))
    return _Blit(w, h, pix, -1)


# Mouse-style pointer sprite (O=black outline, F=white fill), hotspot at top-left.
CURSOR = _from_ascii([
    "O.......", "OO......", "OFO.....", "OFFO....", "OFFFO...", "OFFFFO..",
    "OFFFFFO.", "OFFFFFFO", "OFFFOOO.", "OFOOFO..", "OO..OFO.", "O...OFO.", "....OO..",
], {"O": 0, "F": 7}, ".")

NAMES = {
    "black": 0, "dark_blue": 1, "dark_purple": 2, "dark_green": 3, "brown": 4,
    "dark_grey": 5, "light_grey": 6, "white": 7, "red": 8, "orange": 9,
    "yellow": 10, "green": 11, "blue": 12, "indigo": 13, "pink": 14, "peach": 15,
}


def color(name_or_index):
    if isinstance(name_or_index, str):
        return NAMES.get(name_or_index, 7)
    return int(name_or_index) & 63


# (The code-editor syntax highlighter -- _highlight + _HL_* -- moved to code_layer.py
# with the rest of the code editor; it was code-only. The Pointer cursor +
# CURSOR_IDLE_MS moved to widgets.py, imported back at the top of this file.)


# --- Pointer UI layout (320x240) -------------------------------------------
# The unified top bar's geometry (Stage 1) -- _STATUS_H, _BAR_ICON/_BAR_GAP/
# _BAR_STRIDE/_BAR_Y, the fixed 320x240 tool-switcher button rects (_SYSMENU_BTN /
# _HOME_BTN / _MENU_BTN / _PAINT_BTN / _MAP_BTN / _BLOCKS_BTN / _MUSIC_BTN),
# _BAR_BATT / _BAR_WIFI / _BAR_CLOCK, and _DOCK_SLOTS / _DOCK_GLYPH / _DOCK_LABEL --
# now lives in bar_layer.py (its own surface, #46) and is imported back at the top of
# this file, so console._X still resolves for Layout + the golden harness/tests.
# The pause-screen button geometry (_PAUSE_BTN_* / _PAUSE_CONTINUE_BTN /
# _PAUSE_QUIT_BTN, #71) moved to player.py with the pause machinery (Stage 2) and is
# imported back at the top of this file, so console._PAUSE_* still resolves for tests.
# The cards-menu geometry (_RUN_BTN / _CODE_BTN / _CLOSE_BTN + _CARD_*) lives in
# cards_layer.py (its own surface, #3/#15) and is imported back at the top of this
# file, so console._X still resolves for tests.
# --- Desktop shell (#28): home = wallpaper + cart icon grid + dock ----------
# The home screen is now a Picotron/TIC-80-style desktop: a wallpaper backdrop, a
# grid of tappable cart icons, the unified 18px top bar (clock + wifi/batt/gear +
# NEW/DUP/DEL management icons), and (in Settings) the bottom dock. The top bar's
# icons are 16x16 sprites from the editable IconSheet (Stage 1); the rest of the
# chrome uses the indexed API + petme128 font + the _glyph vocabulary, so host ==
# device. (_STATUS_H lives in bar_layer.py, imported back at the top of this file.)
_DOCK_Y = 218           # bottom dock strip top
_DOCK_H = 22
# Cart icon grid: a page of COLS x ROWS tiles between the status strip and dock.
_ICON_COLS = 4
_ICON_ROWS = 2
_ICON_W = 70            # tile footprint (icon box + label)
_ICON_H = 84
_ICON_GAP_X = 6
_ICON_GAP_Y = 6
_ICON_X0 = 8            # left margin so the COLS tiles + gaps center in 320px
_ICON_Y0 = _STATUS_H + 8
_ICON_BOX = 40          # the inner art box of a tile (the tappable icon proper)
# (The home NEW/DUP/DEL management icons are hit-tested via Layout's responsive
# lay.new_btn/dup_btn/del_btn -- the old fixed _NEW_BTN/_DUP_BTN/_DEL_BTN placeholders
# were dead, so they were dropped with the bar-geometry move to bar_layer.py.)
# Page chevrons (when more carts than one page): tap to flip pages.
_PAGE_PREV = (2, 110, 14, 24)
_PAGE_NEXT = (304, 110, 14, 24)
# Bottom dock (persistent tool switcher, TIC-80 style): one tap to jump between
# home / code / draw / map / run / settings. Six evenly-spaced slots across 320px.
# (_DOCK_SLOTS/_DOCK_GLYPH/_DOCK_LABEL live in bar_layer.py, imported back above;
# the per-slot width/gap/geometry below stays here for Layout.)
_DOCK_W = 52
_DOCK_GAP = 1
_DOCK_X0 = 2
# Settings screen (#28) geometry (_SET_*) lives in settings_layer.py (its own surface)
# and is imported back at the top of this file, so console._X still resolves for the
# Layout class + tests.
# Code editor (#24) geometry (_CODE_*/_ED_*/_SYM_*/_CODE_SYMBOLS) lives in code_layer.py
# (its own surface) and is imported back at the top of this file, so console._X resolves
# for the CodeLayout class + the crash panel (_CODE_LH) + tests. (_CODE_AREA is
# re-exported too -- test_responsive_editors pins lay.code_area() against it.)
# Paint editor (#4/#30) geometry (_PG_*/_SW*/_SPR_*/_PAINT_*) lives in paint_layer.py
# (its own surface) and is imported back at the top of this file, so console._X still
# resolves for tests + tools. (_PAINT_BTN -- the desktop overlay -- is in bar_layer.py.)
# Map (tilemap) editor (#32) constants + MapEditorUI now live in map_editor_ui.py
# (imported above) -- this used to be ~80 lines of module-level constants right here.
# Block editor (#29 Part 2) constants, BlockLayout, and BlockEditorUI now live in
# block_editor_ui.py (imported above) -- this used to be ~120 lines of module-level
# constants + a class right here.
# Music/sound editor (#50) constants + MusicEditorUI now live in music_editor_ui.py
# (imported above) -- this used to be ~60 lines of module-level constants right here.
# Trackball cursor sensitivity (#2). _CURSOR_BASE is the per-pulse step; the
# quadratic _CURSOR_ACCEL term adds light acceleration so a fast roll crosses the
# 320px screen in far fewer pulses while a slow, single-pulse roll stays precise.
# These are a FEEL tweak meant to be finalized on real hardware (the trackball's
# pulses-per-revolution sets the true "rolls to cross").  Before: BASE=4, ACCEL=1
# (1 pulse -> 5px, ~64 px/s at a steady 1 pulse/frame). After: BASE=7, ACCEL=2
# (1 pulse -> 9px; a 6-pulse flick -> 6*7 + 2*36 = 114px, so ~3 brisk rolls cross).
_CURSOR_BASE = 7
_CURSOR_ACCEL = 2

# Baseline the responsive layout reproduces EXACTLY (#39 graceful degradation).
_BASE_W = 320
_BASE_H = 240
_FONT_W = 8                 # petme128 cell width at scale 1 (one char advance)
# Letterbox/bezel fill (#39): the solid MOY64 index the system canvas shows around
# the integer-scaled 320x240 game viewport (the borders of the fixed-aspect frame).
_VIEWPORT_BEZEL = 0         # black


class Layout:
    """Responsive desktop-shell geometry (#39): the status strip, cart icon grid,
    page chevrons, management buttons, bottom dock, and Settings rows derived from
    the SYSTEM canvas size (w, h) + the system font scale (1/2/3) -- instead of the
    hand-placed 320x240 constants. The desktop reflows to fill a larger panel and
    the chrome scales with the font.

    The single hard contract: at (w, h, fs) == (320, 240, 1) every field equals the
    frozen module constant, byte-for-byte -- the T-Deck path is unchanged. The exact
    baseline is reproduced VERBATIM (the `_base` branch) rather than re-derived, so
    no reflow formula's integer-floor can drift a pixel at the default size; the
    responsive formulas only run on a larger canvas / bigger font. The editors stay
    a fixed 320x240 viewport in step 1, so their constants are NOT routed here.

    `font_w` is the on-screen char-cell width (8 * fs) so callers center/space text
    that the SystemCanvas renders at `fs`; chrome heights/margins scale with fs too."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        fs = self.fs
        self.font_w = _FONT_W * fs
        self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1)

        # -- status strip + dock (heights/positions scale with the font) --------
        self.status_h = _STATUS_H * fs
        self.dock_h = _DOCK_H * fs
        self.dock_y = self.h - self.dock_h
        n = len(_DOCK_SLOTS)
        if self._base:
            self.dock_w, self.dock_gap, self.dock_x0 = _DOCK_W, _DOCK_GAP, _DOCK_X0
        else:
            self.dock_gap = _DOCK_GAP * fs
            # Fill the width with n evenly-spaced slots, snug to the edges.
            self.dock_w = max(_FONT_W,
                              (self.w - 2 * _DOCK_X0 - (n - 1) * self.dock_gap) // n)
            span = n * self.dock_w + (n - 1) * self.dock_gap
            self.dock_x0 = max(0, (self.w - span) // 2)

        # -- cart icon grid (reflows COLS x ROWS to fill the band) ---------------
        # The launcher/home screen no longer draws the bottom dock (#46), so the cart
        # grid reclaims the height below the status strip down to the canvas floor
        # (grid_bottom), not just down to the dock. (Settings keeps the dock, so its
        # panel still stops at dock_y -- that bound is unchanged.)
        self.icon_w = _ICON_W * fs
        self.icon_h = _ICON_H * fs
        self.icon_gap_x = _ICON_GAP_X * fs
        self.icon_gap_y = _ICON_GAP_Y * fs
        self.icon_box = _ICON_BOX * fs
        self.icon_y0 = self.status_h + 8 * fs
        self.grid_bottom = self.h - 4 * fs           # launcher grid floor (no dock)
        if self._base:
            self.cols, self.rows = _ICON_COLS, _ICON_ROWS
            self.icon_x0 = _ICON_X0
        else:
            self.cols = max(1, (self.w + self.icon_gap_x) //
                            (self.icon_w + self.icon_gap_x))
            band = self.grid_bottom - self.icon_y0
            self.rows = max(1, (band + self.icon_gap_y) //
                            (self.icon_h + self.icon_gap_y))
            grid_w = self.cols * self.icon_w + (self.cols - 1) * self.icon_gap_x
            self.icon_x0 = max(0, (self.w - grid_w) // 2)
        self.page = self.cols * self.rows

        # -- unified top bar: icon size + clusters (Stage 1) -------------------
        # Every bar control is a 16x16 IconSheet sprite (16px icons, 1px margin in the
        # 18px bar -> y = _BAR_Y). Icons scale with the font (24px at fs=2, etc.) so
        # the bar grows on a larger system canvas. status_gh stays the 12*fs glyph box
        # the non-bar chrome (dock/settings/toasts) still uses via _glyph.
        self.status_gh = 12 * fs                      # legacy glyph box (dock/settings)
        ic = _BAR_ICON * fs                           # bar icon side, scaled
        stride = ic + _BAR_GAP * fs                   # left-edge step between bar icons
        self.bar_icon = ic
        self.bar_stride = stride
        edge = 2 * fs                                 # margin from the canvas edges

        # -- right zone (OS-owned, Stage 4 #46 zoned bar -- the macOS-menu-bar
        # model): batt hard against the right edge, then wifi, then the ≡ system-
        # menu toggle (moved off the left edge so every OS control lives on ONE
        # side), then a slot RESERVED for the Stage-5 context X (not drawn/tapped
        # yet -- carved out now so its arrival doesn't reflow the rest of this
        # cluster again), then the clock text filling the remaining space to
        # their left.
        self.batt_btn = (self.w - edge - ic, _BAR_Y, ic, ic)
        self.wifi_btn = (self.batt_btn[0] - stride, _BAR_Y, ic, ic)
        self.sysmenu_btn = (self.wifi_btn[0] - stride, _BAR_Y, ic, ic)
        self.context_x_btn = (self.sysmenu_btn[0] - stride, _BAR_Y, ic, ic)  # reserved
        self.clock_w = 5 * self.font_w                # "HH:MM" (5 chars)
        self.clock_x = max(edge, self.context_x_btn[0] - edge - self.clock_w)

        # -- left zone (Stage 4: fully LENT to the active app's draw_zone/zone_tap
        # -- launcher/Settings/the Editor): NEW / DUP / DEL start right at the left
        # edge now that ≡ isn't there any more.
        self.new_btn = (edge, _BAR_Y, ic, ic)
        self.dup_btn = (self.new_btn[0] + stride, _BAR_Y, ic, ic)
        self.del_btn = (self.dup_btn[0] + stride, _BAR_Y, ic, ic)

        # -- selected-cart name slot: between the management cluster and the clock.
        self.status_name_x = self.del_btn[0] + self.del_btn[2] + edge
        self.status_name_maxc = max(
            4, (self.clock_x - edge - self.status_name_x) // self.font_w)
        # The full lent left zone (Stage 4): from the left edge to just before the
        # right zone's clock text -- the rect BarLayer hands to draw_zone/zone_tap.
        self.zone_left = (edge, _BAR_Y, max(0, self.clock_x - 2 * edge), ic)

        # -- page chevrons (centered vertically in the icon band) ----------------
        if self._base:
            self.page_prev, self.page_next = _PAGE_PREV, _PAGE_NEXT
        else:
            cy = (self.icon_y0 + self.grid_bottom) // 2 - 12 * fs
            self.page_prev = (2, cy, 14 * fs, 24 * fs)
            self.page_next = (self.w - 2 - 14 * fs, cy, 14 * fs, 24 * fs)

        # -- Settings rows + panel (scale row height with the font) --------------
        self.set_row_h = _SET_ROW_H * fs
        if self._base:
            self.set_x = _SET_X
            self.set_w = _SET_W
            self.set_row_y0 = _SET_ROW_Y0
            self.settings_panel = (8, 16, 304, 198)         # frozen baseline
            self.set_back = _SET_BACK
            self.set_ach = _SET_ACH
            self.set_title_hit = _SET_TITLE_HIT
        else:
            # The Settings panel fills the band between the status strip and dock.
            py0 = self.status_h + 2 * fs
            ph = self.dock_y - py0 - 2 * fs
            self.settings_panel = (8 * fs, py0, self.w - 16 * fs, ph)
            self.set_x = self.settings_panel[0] + 10 * fs
            self.set_w = self.settings_panel[2] - 20 * fs
            self.set_row_y0 = py0 + 24 * fs
            pr = self.settings_panel[0] + self.settings_panel[2]   # panel right edge
            self.set_back = (pr - 20 * fs, py0 + 2 * fs, 18 * fs, 14 * fs)
            self.set_ach = (pr - 46 * fs, py0 + 2 * fs, 22 * fs, 14 * fs)
            self.set_title_hit = (self.settings_panel[0] + 14 * fs, py0 + 2 * fs,
                                  10 * self.font_w, 16 * fs)

    # -- derived rects (mirror the old module-constant arithmetic) ----------
    def dock_slot_rect(self, k):
        x = self.dock_x0 + k * (self.dock_w + self.dock_gap)
        return (x, self.dock_y + 1, self.dock_w, self.dock_h - 2)

    def settings_row_rect(self, i):
        return (self.set_x, self.set_row_y0 + i * self.set_row_h,
                self.set_w, self.set_row_h - 2)

    def tile_rect(self, i, page):
        """Grid-cell rect for cart index `i` on `page`, or None if off that page."""
        start = page * self.page
        if i < start or i >= start + self.page:
            return None
        k = i - start
        col = k % self.cols
        row = k // self.cols
        x = self.icon_x0 + col * (self.icon_w + self.icon_gap_x)
        y = self.icon_y0 + row * (self.icon_h + self.icon_gap_y)
        return (x, y, self.icon_w, self.icon_h)

    def clock_hit(self):
        # The clock-text region in the top bar's right cluster (Time Traveler egg #21).
        return (self.clock_x, 0, self.clock_w, self.status_h)


class CodeLayout:
    """Responsive code-editor geometry (#39 step 2): the top bar (title + run/save/
    close icons), the COLS x ROWS text grid, the caret/gutter, and the bottom symbol
    palette -- all derived from the SYSTEM canvas size (w, h) + font scale, instead
    of the hand-placed 320x240 constants. On a larger panel the editor shows MORE
    visible lines + WIDER columns; at a bigger font everything (cell, bar, palette)
    grows with the text.

    The single hard contract (mirrors `Layout`): at (w, h, fs) == (320, 240, 1)
    every field equals the frozen `_CODE_*`/`_SYM_*`/`_ED_*` module constant, byte
    for byte -- so the degradation path is exactly today. That baseline is
    reproduced VERBATIM (the `_base` branch); the responsive formulas only run on a
    larger canvas / bigger font.

    `cell` is the on-screen char-cell width (8 * fs); `lh` the line height. `cols` /
    `rows` are how many fit -- the CodeEditor's view window adopts them so it scrolls
    the right span."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        fs = self.fs
        self.cell = _FONT_W * fs                  # char-cell width (8*fs)
        self.lh = _CODE_LH * fs                   # line height
        self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1)
        # -- symbol palette (bottom strip): one cell per coding symbol -----------
        self.sym_cell = _SYM_CELL * fs
        self.sym_h = _SYM_H * fs
        self.sym_y = self.h - self.sym_h
        self.sym_area = (0, self.sym_y, self.sym_cell * len(_CODE_SYMBOLS), self.sym_h)
        # -- top bar (title + action icons), code area origin --------------------
        self.x0 = _CODE_X0 * fs
        self.y0 = _CODE_Y0 * fs
        if self._base:
            self.run_btn, self.save_btn, self.close_btn = _ED_RUN, _ED_SAVE, _ED_CLOSE
        else:
            bw = 16 * fs
            bh = 14 * fs
            gap = 1 * fs
            x_close = self.w - (15 * fs)
            x_save = x_close - bw - gap
            x_run = x_save - bw - gap
            self.run_btn = (x_run, 1 * fs, bw, bh)
            self.save_btn = (x_save, 1 * fs, bw, bh)
            self.close_btn = (x_close, 1 * fs, 15 * fs, bh)
        # -- the COLS x ROWS text grid (fills between the top bar + palette) ------
        if self._base:
            self.cols = CodeEditor.COLS          # 38
            self.rows = CodeEditor.ROWS          # 20
        else:
            avail_w = self.w - self.x0
            self.cols = max(8, avail_w // self.cell)
            avail_h = self.sym_y - self.y0
            self.rows = max(4, avail_h // self.lh)

    def code_area(self):
        return (self.x0, self.y0, self.cols * self.cell, self.rows * self.lh)


# BlockLayout (#39 step 2) now lives in block_editor_ui.py (imported above).


# --- Button icon glyphs (the pre-literate icon vocabulary) ------------------
# 1-bit, recolorable pixel bitmaps designed on a 12x12 grid at the native button
# size (boxes are 14-16px), then centered in each button's rect and blitted in
# the requested palette color via the indexed primitives only -- so they render
# identically on host (runtime/canvas.py) and the frozen device console. Each
# glyph is a tuple of 12 ints: row r, bit (11 - col) set => pixel on. Constant
# (no per-frame allocation; freezes into firmware at ~15*12 ints).
#
# Hand-authored at this grid, adapted from the Pixelarticons set
# (https://pixelarticons.com, MIT License (c) Gerrit Halfmann) -- a purpose-built
# pixel-icon vocabulary; shapes traced down to 12x12 and hand-cleaned for
# legibility at button size. MIT permits this use; this comment is the notice.
_GLYPH_SIZE = 12
_GLYPHS = {
    "run":    (0x000, 0x180, 0x1C0, 0x1E0, 0x1F0, 0x1F8, 0x1F8, 0x1F0, 0x1E0, 0x1C0, 0x180, 0x000),
    "save":   (0x000, 0x7FE, 0x402, 0x5FA, 0x402, 0x402, 0x4F2, 0x492, 0x492, 0x492, 0x7FE, 0x000),
    "close":  (0x000, 0x204, 0x30C, 0x198, 0x0F0, 0x060, 0x060, 0x0F0, 0x198, 0x30C, 0x204, 0x000),
    "edit":   (0x01E, 0x03E, 0x07C, 0x0F8, 0x0F0, 0x1E0, 0x3C0, 0x780, 0x700, 0x600, 0x400, 0x000),
    "paint":  (0x006, 0x00C, 0x018, 0x030, 0x060, 0x0E0, 0x1F0, 0x1F0, 0x1F0, 0x0E0, 0x000, 0x000),
    "home":   (0x000, 0x060, 0x0F0, 0x1F8, 0x3FC, 0x7FE, 0x204, 0x264, 0x264, 0x264, 0x3FC, 0x000),
    "minus":  (0x000, 0x000, 0x000, 0x000, 0x000, 0x7FE, 0x7FE, 0x000, 0x000, 0x000, 0x000, 0x000),
    "plus":   (0x000, 0x000, 0x060, 0x060, 0x060, 0x7FE, 0x7FE, 0x060, 0x060, 0x060, 0x000, 0x000),
    "turtle": (0x000, 0x0F8, 0x1FC, 0x3FE, 0x3FE, 0x3FE, 0x3FE, 0x2ED, 0x653, 0x000, 0x000, 0x000),
    "rabbit": (0x220, 0x220, 0x220, 0x360, 0x1C0, 0x3E0, 0x7F4, 0x7F0, 0x3E0, 0x000, 0x000, 0x000),
    "star":   (0x000, 0x060, 0x060, 0x0F0, 0xFFC, 0x7F8, 0x3F0, 0x3F0, 0x618, 0x618, 0x000, 0x000),
    "dot":    (0x000, 0x000, 0x000, 0x0F0, 0x1F8, 0x1F8, 0x1F8, 0x0F0, 0x000, 0x000, 0x000, 0x000),
    "get":    (0x000, 0x060, 0x060, 0x060, 0x264, 0x1F0, 0x0E0, 0x040, 0x7FE, 0x402, 0x7FE, 0x000),
    "put":    (0x000, 0x040, 0x0E0, 0x1F0, 0x264, 0x060, 0x060, 0x060, 0x7FE, 0x402, 0x7FE, 0x000),
    "heart":  (0x000, 0x30C, 0x79E, 0x7FE, 0x7FE, 0x7FE, 0x3FC, 0x1F8, 0x0F0, 0x060, 0x000, 0x000),
    # "map": a 3x3 tile grid (the tilemap editor's nav/open icon, #32) -- full
    # h-lines at rows 1/5/9, v-lines at cols 1/5/9, so it reads as a placed grid.
    "map":    (0x000, 0x7FE, 0x444, 0x444, 0x444, 0x7FE, 0x444, 0x444, 0x444, 0x7FE, 0x000, 0x000),
    # "blocks": two stacked Scratch-style notched bricks (the #29 block-editor icon).
    "blocks": (0x000, 0x3F8, 0x7FC, 0x7FC, 0x3F8, 0x000, 0x1FC, 0x3FE, 0x3FE, 0x1FC, 0x000, 0x000),
    # Desktop-shell dock icons (#28). "code" = angle brackets </>; "gear" = a
    # settings cog; "note" = a music note (the #16 music slot, greyed until it
    # lands); "app" = a generic window (default cart icon).
    "code":   (0x000, 0x048, 0x08C, 0x118, 0x230, 0x230, 0x118, 0x08C, 0x048, 0x000, 0x000, 0x000),
    "gear":   (0x000, 0x060, 0x276, 0x3FC, 0x1F8, 0x18C, 0x18C, 0x1F8, 0x3FC, 0x276, 0x060, 0x000),
    "note":   (0x000, 0x07E, 0x042, 0x042, 0x042, 0x042, 0x0C6, 0x1CE, 0x1CE, 0x0C4, 0x000, 0x000),
    # "music": a beamed pair of eighth notes -- the #50 sound-editor switcher's
    # fallback glyph (the bar normally blits the 16x16 IconSheet "music" sprite).
    "music":  (0x000, 0x07E, 0x042, 0x042, 0x042, 0x0C2, 0x1C2, 0x1CE, 0x00E, 0x00C, 0x000, 0x000),
    "app":    (0x000, 0x7FE, 0x402, 0x7FE, 0x402, 0x402, 0x402, 0x402, 0x402, 0x402, 0x7FE, 0x000),
    "wifi":   (0x000, 0x000, 0x1F8, 0x204, 0x0F0, 0x108, 0x060, 0x000, 0x060, 0x000, 0x000, 0x000),
    "batt":   (0x000, 0x000, 0x180, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x000, 0x000, 0x000),
    # Achievements (#21): "trophy" (the unlocked-badge cue), "lock" (a locked/secret
    # entry), "smile" (the "Oh! You found me!" Easter-egg character), "key" (the
    # explorer reward), and "spark" (the celebratory confetti pip).
    "trophy": (0x000, 0x7FE, 0x7FE, 0x3FC, 0x3FC, 0x1F8, 0x0F0, 0x060, 0x060, 0x1F8, 0x1F8, 0x000),
    "lock":   (0x000, 0x0F0, 0x108, 0x108, 0x108, 0x7FE, 0x7FE, 0x792, 0x792, 0x7FE, 0x7FE, 0x000),
    "smile":  (0x0F0, 0x308, 0x404, 0x492, 0x492, 0x404, 0x444, 0x438, 0x404, 0x308, 0x0F0, 0x000),
    "key":    (0x000, 0x1C0, 0x220, 0x220, 0x1C0, 0x080, 0x080, 0x0E0, 0x080, 0x0E0, 0x000, 0x000),
    "spark":  (0x000, 0x060, 0x060, 0x060, 0x366, 0x1FC, 0x060, 0x1FC, 0x366, 0x060, 0x060, 0x000),
    # "menu": the hamburger (≡) -- three full-width bars. The system-menu (#52) toggle;
    # always a _glyph bitmap (NOT a themeable IconSheet slot) so it can't go blank on a
    # device whose saved theme predates this icon.
    "menu":   (0x000, 0x000, 0x7FE, 0x7FE, 0x000, 0x7FE, 0x7FE, 0x000, 0x7FE, 0x7FE, 0x000, 0x000),
}


def _blit_glyph(cv, kind, rect, c):
    """Draw an icon glyph (no background) centered in `rect`, in color `c`, onto
    canvas `cv`. The shared pre-literate icon vocabulary -- a 12x12 1-bit pixel
    bitmap (see _GLYPHS) blitted via the indexed primitives only (rect spans), so
    it renders identically on host and device. Unknown kinds draw NOTHING, so
    every caller can keep a text label as the guaranteed fallback. Module-level so
    both Workstation._glyph and Launcher (which only holds a canvas) share one
    implementation -- the glyph encoding lives in exactly one loop."""
    bits = _GLYPHS.get(kind)
    if bits is None:                                # unknown -> nothing (fallback contract)
        return
    x, y, w, h = rect
    n = _GLYPH_SIZE
    # Scale the icon mask with the canvas's system font scale (#39) so glyphs grow
    # alongside text on a larger system canvas. A plain (game) Canvas has font_scale
    # 1, so this is byte-identical to the original 1x path everywhere else.
    fs = getattr(cv, "font_scale", 1)
    if fs < 1:
        fs = 1
    span = n * fs
    ox = x + (w - span) // 2                          # center the (scaled) mask in the rect
    oy = y + (h - span) // 2
    for r in range(n):
        row = bits[r]
        if not row:
            continue
        yy = oy + r * fs
        run = 0                                     # length of the current on-run
        for col in range(n):                        # walk L->R, coalescing runs
            if row & (1 << (n - 1 - col)):
                run += 1
            elif run:
                cv.rect(ox + (col - run) * fs, yy, run * fs, fs, c)
                run = 0
        if run:
            cv.rect(ox + (n - run) * fs, yy, run * fs, fs, c)


# --- the unified top bar's icon theme (Stage 1) -----------------------------
#
# The top bar's chrome controls are 16x16 sprites drawn from an EDITABLE IconSheet
# (so the bar is themeable), not the hardcoded _GLYPHS bitmaps -- which collapses the
# ~120 glyph rect-spans/frame the labeled button rows cost into ~12 cached sprite
# blits (a measured ~15ms/frame device win). `_ICON` is the slot map: a chrome kind ->
# its sprite id in the 8x4 IconSheet (row-major). The IconSheet is loaded from
# system_icons.moygfx when present, else baked from `_ICON_ART` below. The _glyph
# vocabulary stays for NON-chrome uses (the cards/paint/blocks editors).
_ICON = {
    "home": 0, "edit": 1, "code": 2, "paint": 3, "map": 4, "blocks": 5,
    "gear": 6, "wifi": 7, "batt": 8, "new": 9, "dup": 10, "del": 11,
    "close": 12, "run": 13, "save": 14, "music": 15,
    "moy": 16,          # the moybyte mascot (boot logo); not a bar control
}

# The baked default theme: each icon is 16 row-strings of 16 chars over the 16-color
# base palette. A char is a palette nibble (hex), or "." for transparent. Authored
# high-contrast (mostly white 7 outlines + a couple of accents) so they read at 16px
# on the black bar. Kept readable here so the theme is hand-editable; _default_icon_
# sheet() bakes it into an IconSheet's pixels at the _ICON slots.
_ICON_ART = {
    "home": (
        "................", ".......77.......", "......8888......", ".....888888.....",
        "....88888888....", "...8888888888...", "..888888888888..", ".77777777777777.",
        ".7ffffffffffff7.", ".7f77ff11ff77f7.", ".7f77ff11ff77f7.", ".7ffffff11ffff7.",
        ".7ffffff11ffff7.", ".7ffffff11ffff7.", ".77777777777777.", "................",
    ),
    "edit": (
        ".............77.", "............7ee7", "...........7ee7.", "..........7aa7..",
        ".........7aa7...", "........7aa7....", ".......7aa7.....", "......7aa7......",
        ".....7aa7.......", "....7aa7........", "...7ff7.........", "..7ff7..........",
        ".700f...........", "700.............", "................", "................",
    ),
    "code": (
        "................", "................", ".....c....c.....", "....cc....cc....",
        "...cc......cc...", "..cc........cc..", ".cc..........cc.", "cc............cc",
        ".cc..........cc.", "..cc........cc..", "...cc......cc...", "....cc....cc....",
        ".....c....c.....", "................", "................", "................",
    ),
    "paint": (
        "..............77", ".............799", "............7997", "...........7997.",
        "..........7997..", ".........7997...", "........7997....", ".......7667.....",
        "......76667.....", ".....7eeee7.....", "....7eeeee7.....", "....7eeeee7.....",
        ".....7eeee7.....", "......7ee7......", ".......77.......", "................",
    ),
    "map": (
        "................", ".77777777777777.", ".7bbb7ccc7bbb77.", ".7bbb7ccc7bbb77.",
        ".77777777777777.", ".7ccc7bbb7ccc77.", ".7ccc7bbb7ccc77.", ".77777777777777.",
        ".7bbb7ccc7bbb77.", ".7bbb7ccc7bbb77.", ".77777777777777.", "................",
        "................", "................", "................", "................",
    ),
    "blocks": (
        "................", "..bbbbb.........", ".bb...bbbbbb....", ".bbbbbb....b....",
        ".bccccccccccb...", ".cc........cc...", ".cccccc....cc...", ".ccaaaccccccc...",
        ".caaaaaaaaaac...", ".aa........aa...", ".aaaaaa....aa...", ".aaaaaaaaaaaa...",
        "................", "................", "................", "................",
    ),
    "gear": (
        "......6..6......", ".....66..66.....", "..6..666666..6..", "..66666666666...",
        "..6677777766....", ".66777777776666.", ".667700007766...", "66677000007766..",
        "66677000007766..", ".667700007766...", ".66777777776666.", "..6677777766....",
        "..66666666666...", "..6..666666..6..", ".....66..66.....", "......6..6......",
    ),
    "wifi": (
        "................", "....77777777....", "..77........77..", ".7....7777....7.",
        "....77....77....", "...7........7...", "......7777......", ".....7....7.....",
        "........7.......", "................", ".......77.......", "......7887......",
        ".......77.......", "................", "................", "................",
    ),
    "batt": (
        "................", "................", "....77777777.7..", "...7........7.7.",
        "...7.bbbbbb.7.7.", "...7.bbbbbb.7.7.", "...7.bbbbbb.7.7.", "...7.bbbbbb.7.7.",
        "...7........7.7.", "....77777777.7..", "................", "................",
        "................", "................", "................", "................",
    ),
    "new": (
        "..7777777777....", "..7........7....", "..7...bb...7....", "..7...bb...7....",
        "..7.bbbbbb.7....", "..7.bbbbbb.7....", "..7...bb...7....", "..7...bb...7....",
        "..7........7....", "..7........7....", "..7........7....", "..7777777777....",
        "................", "................", "................", "................",
    ),
    "dup": (
        "....7777777.....", "....7......7....", "..7777777..7....", "..7......7.7....",
        "..7......777....", "..7........7....", "..7........7....", "..7........7....",
        "..7........7....", "..7........7....", "..77777777777...", "................",
        "................", "................", "................", "................",
    ),
    "del": (
        "................", ".....88888......", "...888888888....", ".88888888888888.",
        "................", ".7777777777777..", ".7.7.7.7.7.7.7..", ".7.7.7.7.7.7.7..",
        ".7.7.7.7.7.7.7..", ".7.7.7.7.7.7.7..", ".7.7.7.7.7.7.7..", "..77777777777...",
        "..777777777.....", "................", "................", "................",
    ),
    "close": (
        "................", ".88..........88.", "..88........88..", "...88......88...",
        "....88....88....", ".....88..88.....", "......8888......", "......8888......",
        ".....88..88.....", "....88....88....", "...88......88...", "..88........88..",
        ".88..........88.", "................", "................", "................",
    ),
    "run": (
        "................", "...bb...........", "...bbbb.........", "...bbbbbb.......",
        "...bbbbbbbb.....", "...bbbbbbbbbb...", "...bbbbbbbbbbbb.", "...bbbbbbbbbb...",
        "...bbbbbbbb.....", "...bbbbbb.......", "...bbbb.........", "...bb...........",
        "................", "................", "................", "................",
    ),
    "save": (
        "................", ".7777777777777..", ".7cc7777777cc7..", ".7cc7777777cc7..",
        ".7cc7777777cc7..", ".7ccccccccccc7..", ".7c777777777c7..", ".7c7bbbbbbb7c7..",
        ".7c7bbbbbbb7c7..", ".7c7777777b7c7..", ".7c7777777b7c7..", ".7ccccccccccc7..",
        ".77777777777....", "................", "................", "................",
    ),
    "music": (
        ".....77777777...", "....7cccccccc7..", "...7cc......cc..", "...cc.......cc..",
        "...cc.......cc..", "...cc.......cc..", "...cc.......cc..", "...cc.......cc..",
        ".7ccc.....7ccc..", "7cccc....7cccc..", "7cccc....7cccc..", ".7cc......7cc...",
        "................", "................", "................", "................",
    ),
    # "Moy", the moybyte mascot: one big pixel (a byte) with a square bite chomped
    # from the top-right corner, two eyes + a smile + stubby feet. "Grape" skin --
    # body = indigo (d/13), shadow = dark-purple (2), sheen = light-grey (6), eyes
    # white (7), outlined in black (0). The boot logo (see _draw_splash); not a bar
    # control, so it has no _glyph fallback (the splash simply omits it if absent).
    "moy": (
        "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
        ".0dddddddd0.....", ".0dddddddd0000..", ".0dd77d77ddddd0.", ".0dd70d70ddddd0.",
        ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
        ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
    ),
}

# Bump whenever the baked _ICON_ART above changes: a saved system_icons.moygfx theme
# written at an OLDER version is treated as stale and re-seeded to these new defaults
# at load (mirrors cart versioning, #47), so an already-themed device/desktop picks up
# new icons without a manual wipe. A bump discards a user's custom icon edits, exactly
# like a built-in cart re-seed. (v1 = the first full restyle; v2 = added the "moy"
# mascot slot for the boot logo.)
_ICON_VERSION = 2


def _nibble(ch):
    """One _ICON_ART char -> a palette index, or -1 for transparent ('.')."""
    if ch == ".":
        return -1
    try:
        return int(ch, 16) & 15
    except ValueError:
        return -1


def _default_icon_sheet():
    """Bake `_ICON_ART` into a fresh IconSheet at the `_ICON` slots -- the theme used
    when no system_icons.moygfx exists. Each art entry is painted into its 16x16 tile
    via tset, so the result serializes/loads through the same .moygfx hex as any sheet.
    Unmapped/short rows just leave that tile blank (transparent)."""
    sheet = IconSheet()
    t = sheet.TILE
    for kind, rows in _ICON_ART.items():
        n = _ICON.get(kind)
        if n is None or n >= sheet.count:
            continue
        for ly in range(t):
            row = rows[ly] if ly < len(rows) else ""
            for lx in range(t):
                ch = row[lx] if lx < len(row) else "."
                c = _nibble(ch)
                if c >= 0:
                    sheet.tset(n, lx, ly, c)
    sheet.dirty = False
    return sheet


def _cursor_delta(n):
    # n = net pulses this frame on one axis. Precise on a slow roll
    # (1 pulse -> _CURSOR_BASE + _CURSOR_ACCEL px), accelerates super-linearly on a
    # fast roll (the a*a term dominates as pulses-per-frame climbs).
    a = n if n >= 0 else -n
    if a == 0:
        return 0
    d = a * _CURSOR_BASE + _CURSOR_ACCEL * a * a
    return d if n > 0 else -d


def _clamp_scroll(top, cur, visible, count):
    """Nudge a persistent scroll offset `top` the minimum amount needed so `cur`
    stays inside a window of `visible` rows out of `count` total -- move only when
    the cursor exits the current window (a stable scrolloff, not a re-center), then
    clamp to the valid range. Shared by the settings list (_settings_scroll) and
    the block-editor outline (_blk_reveal) -- verified identical clamp math at both
    call sites. NOT used by the music editor's _mu_visible_top, which re-centers the
    window on the cursor every call instead of nudging a persistent offset -- a
    different (and intentionally different) scrolling feel, not a third copy of
    this."""
    if cur < top:
        top = cur
    elif cur >= top + visible:
        top = cur - visible + 1
    return max(0, min(top, max(0, count - visible)))


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


# (_line_cells -- the drag-to-draw Bresenham helper -- moved to paint_layer.py with
# the rest of the paint editor; it was paint-only.)


# (_TYPE_GLYPH / _TYPE_COLOR -- the launcher tile-type icon/color maps -- moved to
# launcher_layer.py with the Launcher grid.)


# (The Achievements milestone tracker (#21) + its ACHIEVEMENTS catalog moved to
# widgets.py; ACHIEVEMENTS is imported back at the top of this file for the
# AchievementsUI construction + tests.)


# (The Launcher grid class moved to launcher_layer.py alongside LauncherHomeLayer;
# its instance is still ws.launcher, built in __init__ -- the single source.)


# (Pmem (cart persistent RAM), the _SilentAudio no-op backend, and the reusable
# Popup dropdown primitive (#52) moved to widgets.py, imported back at the top of
# this file.)


# Boot logo: how long the moybyte splash (Moy + wordmark) holds before the launcher
# is revealed. Armed only by the real boot entries (device run_desktop, interactive
# host), never by unit construction, so tests see the launcher on the first frame().
_SPLASH_MS = 1500


class Workstation:
    def __init__(self, comp, canvas, input, carts=None, sys_canvas=None,
                 font_scale=1):
        self.comp = comp
        # Two rendering domains (#39). The GAME canvas is the fixed 320x240 indexed
        # surface the cart + cart API draw on -- carts are UNCHANGED. The SYSTEM
        # canvas is the panel/window surface the desktop/launcher/settings + status
        # strip + dock render on, responsive to its size + the system font scale; a
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
        # `font_scale` is the REQUESTED system-UI scale (persisted). It only takes
        # visible effect on a distinct SYSTEM canvas that can render scaled text; in
        # the degradation case (no system canvas -- e.g. the T-Deck, whose framebuf
        # text can't scale) the effective scale is 1, so the chrome layout matches the
        # 8px text actually drawn. The requested value is still kept + persisted, so a
        # bigger panel later honours it.
        self.font_scale = max(1, int(font_scale))
        if self._sys_canvas is not None:
            self._sys_canvas.set_font_scale(self.font_scale)
        self.layout = Layout(self.sys_canvas.w, self.sys_canvas.h,
                             self._effective_font_scale())
        # Responsive editor geometry (#39 step 2): the code + block editors now draw
        # on the SYSTEM canvas at native size, so their layout (visible cols/rows,
        # button rects, palette/menu) derives from (w, h, font_scale) -- exactly the
        # _base-verbatim pattern Layout uses for the desktop. Rebuilt by _relayout on
        # a size/font change. (Sprite/paint + map editors stay a 320x240 viewport --
        # step 3.)
        self.code_layout = CodeLayout(self.sys_canvas.w, self.sys_canvas.h,
                                      self._effective_font_scale())
        # The block editor's UI (issue #29 Part 2, extracted from this class -- see
        # block_editor_ui.py): one instance, built once here and delegated to from
        # handle_input/handle_pointer/frame's menu_view == "blocks" branches plus
        # set_menu_view/_relayout/_leave_menu/go_home/open. NAMES/_in/_err_text/
        # _clamp_scroll are injected (see that module's docstring for why).
        self.block_ui = BlockEditorUI(self, NAMES, _in, _err_text, _clamp_scroll)
        self.block_ui.relayout(self.sys_canvas.w, self.sys_canvas.h,
                               self._effective_font_scale())
        self.input = input
        self.make_api = None       # injected: make_api(canvas, input, cfg, sheet, audio, tilemap, pmem, wifi)->ns
        self.make_audio = None      # injected: make_audio(engine)->audio backend (host/device)
        self.audio = None           # the per-cart audio backend (built on open, #16)
        # WiFi (#38): a SYSTEM service shared across carts (the connection persists
        # when a cart exits), not per-cart. run_desktop/build_workstation injects
        # the backend here; it's exposed to a cart's namespace ONLY when the cart's
        # manifest permissions include "network" (capability-gated -- see _start).
        self.wifi = None            # injected wifi backend (host FakeWifi / device WLAN)
        self.carts_store = None     # injected: cart store module (moy_carts API)
        # OTA firmware updater (#53): injected by the device (moy_ota.OtaUpdater); None
        # on the host. When present AND the build is OTA-capable, Settings grows an
        # "UPDATE FW" row that flashes a new image from /sd/update to the inactive slot.
        self.updater = None
        self._updater_ok = None     # cached updater.available() (cheap, but not per-frame)
        self._online_ok = None      # cached updater.online_available() (#53 Phase 3)
        # The firmware-update SCREEN (#53, extracted from this class -- see
        # update_ui.py): its confirm/download/install/done lifecycle + pump +
        # drawing, delegated to from handle_input/handle_pointer/frame's
        # screen == "update" branches and from _activate_settings_action. The
        # transient screen state (_upd_phase/_upd_msg/_upd_bin/...) lives on it;
        # the queries + channel config above/below stay here.
        self.update_ui = UpdateUI(self, NAMES, _in, _err_text)
        self.launcher = Launcher(carts if carts else [], self.layout, NAMES, _blit_glyph)
        # Screen states (#28): "launcher" is now the DESKTOP home (wallpaper + cart
        # icon grid + dock); "desktop" is a running cart; "menu" is the cards/code/
        # paint/map editors; "settings" is the Settings app.
        self.screen = "launcher"      # "launcher" | "desktop" | "menu" | "settings" | "update"
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
        # runs (start() re-inits it). The nine fields it owns -- ns/_update/_draw,
        # cart_error/crash_line, cart_paused/_bks_prev, _cart_start_ms/_cart_key_prev --
        # live on it now and are exposed back as forwarding properties (below), so every
        # surface file + test reading ws.cart_error/ws._update/... is unchanged.
        self.player = Player(self, NAMES, _in)
        # The EDITOR app (Stage 3, editor_app.py): owns the tab ladder + the active-tab
        # state (EditorApp.tab). Built idle here (BEFORE anything can set menu_view,
        # which is now a forwarding projection of editor_app.tab -- see below). The tab
        # machine (set_menu_view/_open_*/_leave_menu) moved onto it; ws keeps one-line
        # forwards so every surface file + test is unchanged. NAMES/_in are injected
        # (Stage 4, docs/shell_ux_technical_plan_v1.md): the Editor now lends the top
        # bar's left zone (draw_zone/zone_tap, bar_layer.py) so it needs the shared
        # draw toolkit + rect hit-test, like the other zone-owning surfaces.
        self.editor_app = EditorApp(self, NAMES, _in)
        self._run_caller = None       # who to return to on QUIT (run() records it; Stage 2
                                      # only ever the home root, so pop == go_home)
        # (The cards menu's selection/scroll state -- msel/mtop -- lives on
        # self.cards_layer now, built in _build_layers with the rest of the stack.)
        # (The active menu sub-view -- "cards"|"code"|"paint"|"map"|"blocks"|"music"|
        # "theme" -- lives on self.editor_app.tab now (Stage 3); ws.menu_view is a
        # forwarding projection of it, so every reader/writer is unchanged.)
        self.editor = None            # CodeEditor while menu_view == "code"
        # (cart/config/sheet/tilemap/images/pmem live on self.project now -- Stage 1;
        # ns/_update/_draw/cart_error/crash_line/cart_paused/_cart_start_ms/_cart_key_prev/
        # _bks_prev live on self.player now -- Stage 2; both exposed as forwarding
        # properties, so ws.sheet/ws.cart_error/... are unchanged.)
        self.paint = None             # PaintEditor while menu_view == "paint"
        # The map (tilemap) editor's UI (#32, extracted from this class -- see
        # map_editor_ui.py): one instance, delegated to from handle_input/
        # handle_pointer/frame's menu_view == "map" branches plus set_menu_view/
        # _open_map/open/go_home.
        self.map_ui = MapEditorUI(self, NAMES, _in)
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
        self.keyboard = None          # set by run_desktop (for raw/text mode toggle)
        # (The code editor's keyboard-edge tracker (_ekey_prev) + drag-scroll origin
        # (_drag) + highlight memo (_hl_cache) live on self.code_layer now.)
        # (The paint drag-stroke origin -- _paint_drag -- lives on self.paint_layer.)
        # (The launcher's trackball-hover state (_lhover) lives on self.launcher_layer.)
        self.pointer = None           # set by run_desktop
        # Desktop wallpaper (#28): a chosen wallpaper-type cart compiled into its
        # own namespace and run (its _draw, optionally _update) as the BACKDROP each
        # home/settings frame -- the Picotron "wallpaper is a cart" model. None until
        # _select_wallpaper picks one; a solid MOY64 fill is the zero-cart fallback.
        self.system = {}              # system settings dict (moy_carts system.json)
        self.wallpaper_id = None      # chosen wallpaper: cart slug or "fill:<color>" --
                                      # the single source; select_wallpaper drives it.
        # The wallpaper RENDERING + compiled-cart cache is its own component (#28); both
        # the launcher home + Settings draw it via self.wallpaper.draw(dt).
        self.wallpaper = Wallpaper(self, NAMES)
        self._icon_cache = {}         # cart path -> desktop-icon sprite Image (or None)
        # Unified top bar (Stage 1): the editable 16x16 IconSheet the bar draws its
        # chrome icons from. Injected by build_workstation / run_desktop (loaded from
        # system_icons.moygfx, else the baked default theme); None falls back to _glyph.
        # _bar_img_cache memoises tile_image(slot) per kind so the SAME _SheetSprite is
        # reused every frame -- on the device that keeps its per-Image RGB565 blit cache
        # alive (one cached blit per icon), the whole point of moving the bar to sprites.
        self.icon_sheet = None
        self._bar_img_cache = {}      # icon kind -> cached _SheetSprite (or None); backs
                                      # ws._icon (the shared draw toolkit), so it stays here.
        # The unified top bar + bottom dock (#46) is its own surface now (BarLayer, Phase 2
        # of docs/shell_layers_refactor_v1.md): the running-cart strip cache (#43), the
        # per-second clock cache (#66), the dock geometry, and the bar/dock tap slices live
        # on self.bar_layer; set_icon_sheet bumps its cache gen via bar_layer.invalidate().
        self.bar_layer = BarLayer(self, NAMES, _in)
        # Themeable top bar (Stage 2): True while the PAINT editor is repainting the
        # SYSTEM icon sheet (Settings -> EDIT ICONS) rather than a cart's sprites.
        # It changes where SAVE writes (system_icons.moygfx, not the cart) and where
        # CLOSE/back returns (Settings, not the running cart). menu_view == "theme"
        # reuses the cart PAINT renderer/input over self.icon_sheet (PaintEditor is
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
        self.paint_status = None      # last sprite-reuse (GET/PUT) result text (#18)
        self.can_manage = True        # writes enabled? run_desktop sets this from
                                      # whether SD is the cart source (carts_root)
        # SD session wrapper: mounts SD for the duration of fn(), then releases it
        # so the render loop's flushes never collide on the shared bus. On device
        # run_desktop swaps in moybyte_sd.with_sd_live (native moy_sd attach). The
        # default is a host passthrough.
        self._with_sd = lambda fn: fn()
        self.show_fps = True          # bottom-right FPS readout while a cart runs
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
        # (dock + cursor + overlays, the remainder). Surfaced via perf_breakdown().
        self._upd_ms = 0.0            # smoothed cart _update(dt) ms (game LOGIC)
        self._cart_ms = 0.0           # smoothed cart _draw() ms (RENDERING)
        self._audio_ms = 0.0          # smoothed audio.tick(dt) ms (mixer feed)
        self._chrome_ms = 0.0         # smoothed chrome ms (= draw - upd - cart - audio)
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
        # canvases are one object, i.e. today's 320x240 device), the cursor, and the
        # unmeasured remainder (textmode sync + state reset + overlays + batch guard).
        # Only measured on the running-cart path with perf capture on; surfaced via
        # perf_chrome() -> the device CHROMEBRK diag line.
        self._bar_ms = 0.0
        self._cmp_ms = 0.0
        self._cur_ms = 0.0
        # (The clock-text cache moved to self.bar_layer with the rest of the bar #66.)
        # Live diagnostics gate (#68 "kid mode"): False (the kid default) means the
        # device backend SKIPS the diag costs a player can feel -- the 30s forced GC
        # sample (~130-230ms) and the periodic diag->SD write (~115ms) -- and hushes
        # the live serial echo. The RAM ring still collects (us-cheap) and still
        # flushes on crash / cart exit, so "play -> crash -> read diag.log" works
        # either way. Settings -> PERF DIAG toggles + persists it (system.json);
        # run_desktop reads it each cycle. Host: measurement-only, nothing to gate.
        self.diag_live = False
        # Achievements (#21): a small set of fun milestones + the hidden Easter-egg
        # rewards. Starts empty/volatile; load_achievements() wires the SD store +
        # the unlock beep. The Workstation calls ach.note(event) at the flow points
        # below (open/run/save_*/editor opens) and draws ach.toast each frame.
        self.ach = Achievements()
        # The Easter-egg subsystem + achievement/egg drawing (#21, extracted from
        # this class -- see achievements_ui.py): the 3 hidden eggs + their trigger/
        # popup state (_konami_pos/_clock_taps/_secret_taps/egg_msg/egg_until/
        # _confetti_until) + _draw_egg/_draw_confetti/_draw_achievements. The
        # achievement core (ach/show_achievements/load_achievements/...) stays here.
        # Egg trigger state is reset on screen changes (go_home/settings/desktop tap)
        # via self.ach_ui.* so a stray sequence never carries between contexts.
        self.ach_ui = AchievementsUI(self, NAMES, ACHIEVEMENTS)
        self.show_achievements = False  # the locked/unlocked list overlay (Settings entry)
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
        # Web view (#41/#22): the device injects a small controller exposing
        # .enabled (bool), .toggle(), and .url() so Settings can grow a "WEB VIEW"
        # ON/OFF row that serves the running console to a browser over WiFi. None on
        # the host (the host already has tools/web_console.py) -> the row is hidden.
        self.web_hook = None
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
        # Per-frame perf scratch (#43/#66): the running-cart content Layer fills these
        # during its draw so the router's frame-end DRAWBRK/CHROMEBRK accounting can read
        # the split without threading it back through the loop. Zeroed each frame().
        self._pf_upd = 0
        self._pf_cart = 0
        self._pf_audio = 0
        self._pf_bar = 0
        # The compositor/router layer stack (docs/shell_layers_refactor_v1.md). Built
        # once here; _visible_stack()/_draw_stack() z-order + gate them per frame.
        self._build_layers()

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
        # The Python code editor (#24/#39): the full-screen text view. Owns the drawing
        # + code-UI state (keyboard edge / drag / highlight memo); the shared ws.editor
        # handle + save_code/run_code + code-error state + code_layout stay on ws.
        self.code_layer = CodeLayer(self, NAMES, _in)
        # The desktop home / launcher (#28): the home composition + grid nav. The Launcher
        # GRID instance stays ws.launcher (the single source); this Layer draws it.
        self.launcher_layer = LauncherHomeLayer(self, NAMES, _in)
        # Content layers (exactly one active per frame, chosen by screen/menu_view). Every
        # surface is now its own Layer/component; only the running-cart "desktop" + the
        # theme wrapper remain thin _LegacyLayer shims over Workstation methods.
        self._content_layers = {
            "launcher": self.launcher_layer,
            "settings": self.settings_layer,
            "update": _UpdateLayer(self),
            "desktop": _PlayerLayer(self),   # Stage 2: the run loop is ws.player

            "code": self.code_layer,
            "blocks": _BlocksLayer(self),
            "music": _MusicLayer(self),
            "theme": self.theme_layer,
            "paint": self.paint_layer,
            "map": _MapLayer(self),
            "cards": self.cards_layer,
        }
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
        self._sysmenu_layer = _SysMenuLayer(self)
        self._about_layer = _AboutLayer(self)
        self._cursor_layer = L("cursor", "system", draw=lambda dt: self._draw_cursor())

    def _content_layer(self):
        """The active content layer, keyed by screen/menu_view (never the splash --
        the boot logo is a draw-time takeover, see _draw_stack). This is the layer
        keyboard input routes to."""
        if self.screen == "menu":
            return self._content_layers.get(self.menu_view) or self._content_layers["cards"]
        return self._content_layers.get(self.screen) or self._content_layers["launcher"]

    @property
    def _active_content(self):
        """The active content Layer (spec alias for _content_layer())."""
        return self._content_layer()

    def _overlay_stack(self):
        """The transient system-domain overlays drawn on top of the content, in draw
        order (bottom -> top), plus the always-on cursor. This is the single place the
        overlay visibility + z-order rules live (mirrors the pre-refactor tail of
        frame()); the cursor is last so it sits above everything."""
        out = []
        # Perf HUD first: it's GAME-domain (drawn on the 320x240 canvas right after the
        # running cart, before the composite), so it must precede any system overlay.
        if self.show_fps and self.screen == "desktop":
            out.append(self._perf_layer)
        au = self.ach_ui
        if au._confetti_until and _ticks_diff(au._confetti_until, _ticks_ms()) > 0:
            out.append(self._confetti_layer)
        if self.show_achievements:
            out.append(self._ach_layer)
        if au._egg_active():
            out.append(self._egg_layer)
        if self.ach.toast_active():
            out.append(self._toast_layer)
        if self.sysmenu.open:
            out.append(self._sysmenu_layer)
        if self._about:
            out.append(self._about_layer)
        out.append(self._cursor_layer)
        return out

    def _visible_stack(self):
        """The full z-ordered layer stack, bottom -> top: the active content layer,
        the visible overlays, then the cursor. The single source of z-order +
        visibility -- drawing walks it bottom -> top (with the one game->system
        composite at the domain boundary), input routing walks it top -> bottom so the
        overlay that owns the event claims it before the content underneath."""
        return [self._content_layer()] + self._overlay_stack()

    def _draw_stack(self):
        """The draw-order stack. Same as _visible_stack() except the boot logo, when
        armed, takes the content slot (overlays still draw over it; the cursor is
        suppressed inside _draw_cursor during splash)."""
        content = self._splash_layer if self._splash_until is not None \
            else self._content_layer()
        return [content] + self._overlay_stack()

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

    # -- desktop wallpaper (#28) ---------------------------------------------
    #
    # The home screen renders a chosen wallpaper-type cart as a live backdrop:
    # exactly the Picotron model where a wallpaper is just a fullscreen cart. We
    # reuse the cart-run machinery (compile + _init/_update/_draw) but in a SEPARATE
    # namespace so it never collides with the foreground cart. Fallback options are
    # plain solid MOY64 fills ("fill:<color>"), so there's always a valid choice
    # even with zero wallpaper carts installed (and a cheap option for the device).

    _FILL_WALLPAPERS = ("fill:dark_blue", "fill:black", "fill:indigo", "fill:dark_purple")

    def wallpaper_carts(self):
        """The wallpaper-type carts available as backdrops (discovery: scan the
        launcher items by type, Moybyte's equivalent of Picotron's wallpapers
        folder). Returns the cart dicts in launcher order."""
        return [c for c in self.launcher.items if c.get("type") == "wallpaper"]

    def wallpaper_options(self):
        """All selectable wallpaper ids: each wallpaper cart's slug, then the
        built-in solid fills (always present so there's a valid pick)."""
        out = []
        for c in self.wallpaper_carts():
            out.append(self._wp_id_for(c))
        out.extend(self._FILL_WALLPAPERS)
        return out

    def _wp_id_for(self, cart):
        # A stable id for a wallpaper cart: its folder name (slug) so the choice
        # survives a reboot. Embedded/path-less carts fall back to the title slug.
        path = cart.get("path")
        if path:
            name = path.rsplit("/", 1)[-1]
            if name.endswith(".moy"):
                name = name[:-4]
            return name
        return self.carts_store.slug(cart["title"]) if self.carts_store else cart["title"]

    def _wp_cart_by_id(self, wp_id):
        for c in self.wallpaper_carts():
            if self._wp_id_for(c) == wp_id:
                return c
        return None

    def load_system(self):
        """Load the system settings (moy_carts system.json) and apply the saved
        wallpaper + font scale (#39). Safe no-op if no store/root is wired (embedded
        boot)."""
        if self.carts_store is not None and self.carts_root is not None:
            try:
                self.system = self._with_sd(
                    lambda: self.carts_store.load_system(self.carts_root)) or {}
            except Exception as exc:  # noqa: BLE001 -- a bad store must not crash boot
                print("Moybyte system load failed:", _err_text(exc))
                self.system = {}
        # System font scale (#39): apply the persisted choice (1/2/3) so the desktop
        # boots at the saved text size. set_font_scale relays it into the system
        # canvas + relayouts; persist=False so loading doesn't re-write the store.
        self.set_font_scale(self.system.get("font_scale", self.font_scale),
                            persist=False)
        self.select_wallpaper(self.system.get("wallpaper"), persist=False)
        # #68: apply the persisted diagnostics gate (kid-mode default OFF).
        self.set_diag_live(self.system.get("diag_live", False), persist=False)

    def set_icon_sheet(self, sheet):
        """Adopt the top-bar IconSheet (Stage 1) and drop the per-kind image cache so
        the next frame rebuilds its sprites (and, on the device, their RGB565 copies)
        from the new theme. None reverts the bar to the _glyph fallback."""
        self.icon_sheet = sheet
        self._bar_img_cache = {}
        self.bar_layer.invalidate()   # repaint the cached cart bar with the new theme (#43)

    def load_icon_sheet(self):
        """Build the top-bar IconSheet (Stage 1): use the saved system_icons.moygfx theme
        only if its stored version is >= the baked _ICON_VERSION; otherwise bake the
        default theme. A saved theme older than _ICON_VERSION is STALE (the shipped
        icons changed) -> re-seed it: bake the new default and overwrite the saved theme
        + version, so an already-themed device/desktop picks up new icons automatically
        (mirrors cart versioning, #47). A missing theme stays write-free (the common
        "absent = default" case). Safe on an embedded/no-store boot (baked default)."""
        hexs, saved_ver = None, 0
        store = self.carts_store
        load = getattr(store, "load_system_icons", None) if store is not None else None
        if load is not None and self.carts_root is not None:
            loadver = getattr(store, "load_system_icons_version", None)

            def _read_theme():
                return (load(self.carts_root),
                        loadver(self.carts_root) if loadver is not None else _ICON_VERSION)
            try:
                hexs, saved_ver = self._with_sd(_read_theme)
            except Exception as exc:  # noqa: BLE001 -- a bad theme falls back to default
                print("Moybyte icons load failed:", _err_text(exc))
                hexs = None
        sheet = None
        if hexs and saved_ver >= _ICON_VERSION:        # current/newer saved theme -> keep it
            try:
                sheet = IconSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                sheet = None
        if sheet is None:
            sheet = _default_icon_sheet()
            # Re-seed a STALE (or corrupt) saved theme to the new default so the new
            # icons land; skip when nothing was saved (no churn) or the store predates
            # versioning (no loadver -> _read_theme reported current, never stale).
            if hexs and self.carts_root is not None \
                    and getattr(store, "save_system_icons", None) is not None:
                try:
                    self._with_sd(lambda: store.save_system_icons(
                        sheet.to_hex(), self.carts_root, _ICON_VERSION))
                except Exception as exc:  # noqa: BLE001
                    print("Moybyte icons re-seed failed:", _err_text(exc))
        self.set_icon_sheet(sheet)

    # -- system font scale (#39) ---------------------------------------------
    #
    # The system-UI font is settings-resizable (petme128 nearest-neighbor x1/x2/x3),
    # persisted in system.json (mirroring the #28 wallpaper setting) and applied live.
    # The GAME canvas keeps plain 8x8 text regardless -- scaling lives in the system
    # canvas + the responsive Layout, so a cart is never affected.

    FONT_SCALES = (1, 2, 3)

    def _effective_font_scale(self):
        """The scale actually applied to the system canvas + layout. It is the
        requested font_scale ONLY when a distinct system canvas exists (one that can
        render scaled text); in the degradation case (the T-Deck / a shared 320x240
        canvas, whose framebuf text can't scale) it is 1, so the chrome geometry
        always matches the 8px text actually drawn -- no mis-laid-out desktop."""
        return self.font_scale if self._sys_canvas is not None else 1

    def set_font_scale(self, scale, persist=True):
        """Set the system-UI font scale (clamped to FONT_SCALES), relay the effective
        scale into the system canvas + relayout the desktop, and (by default) persist
        it. The game canvas text is always 8px; the effective scale is 1 without a
        distinct system canvas (so the choice is remembered but only shows on a panel
        that can render it)."""
        try:
            scale = int(scale)
        except (TypeError, ValueError):
            scale = 1
        if scale not in self.FONT_SCALES:
            scale = self.FONT_SCALES[0]
        self.font_scale = scale
        if self._sys_canvas is not None:
            self._sys_canvas.set_font_scale(self._effective_font_scale())
        self._relayout()
        if persist:
            self._persist_font_scale()

    def cycle_font_scale(self, d):
        """Step the font scale by d through FONT_SCALES (Settings < / > stepper);
        applies + persists immediately so the desktop text resizes live."""
        scales = self.FONT_SCALES
        cur = self.font_scale if self.font_scale in scales else scales[0]
        nxt = scales[(scales.index(cur) + d) % len(scales)]
        self.set_font_scale(nxt, persist=True)

    def _relayout(self):
        """Rebuild the responsive layout from the live system-canvas size + the
        EFFECTIVE font scale and re-push it into the launcher (so its grid reflows).
        Called on a font-scale change (and could be called on a resize)."""
        w, h, fs = self.sys_canvas.w, self.sys_canvas.h, self._effective_font_scale()
        self.layout = Layout(w, h, fs)
        self.launcher.set_layout(self.layout)
        # Editor layouts reflow too (#39 step 2); an open code editor adopts the new
        # visible window live so a font/size change reflows it without losing the buffer.
        self.code_layout = CodeLayout(w, h, fs)
        self.block_ui.relayout(w, h, fs)
        if self.editor is not None:
            self.editor.set_view_size(self.code_layout.cols, self.code_layout.rows)

    def _persist_font_scale(self):
        self.system["font_scale"] = self.font_scale
        self._persist_system()

    def set_diag_live(self, on, persist=True):
        """Flip the #68 diagnostics gate (Settings -> PERF DIAG) and persist it.
        The device loop (moy_runtime.run_desktop) reads self.diag_live each cycle,
        so the change takes effect within a frame -- no reboot."""
        self.diag_live = bool(on)
        self._dirty = True
        if persist:
            self.system["diag_live"] = self.diag_live
            self._persist_system()

    def tap_mode(self):
        """The launcher's tap default (spec Section 4): "maker" (DEFAULT -- tap opens
        the Editor on Config) or "player" (tap plays). Read by launch_selected."""
        return self.system.get("tap_mode", "maker")

    def cycle_tap_mode(self, d):
        """Toggle the launcher tap default MAKER<->PLAYER and persist it (Settings ->
        TAP OPENS). Two modes, so any step flips."""
        self.system["tap_mode"] = "player" if self.tap_mode() == "maker" else "maker"
        self._dirty = True
        self._persist_system()

    def _persist_system(self):
        """Write self.system to system.json when a writable store is wired. Shared by the
        persisting Settings toggles (font, wallpaper, OTA channel)."""
        if not (self.carts_store is not None and self.carts_root is not None
                and self.can_manage):
            return
        try:
            self._with_sd(lambda: self.carts_store.save_system(self.system, self.carts_root))
        except Exception as exc:  # noqa: BLE001 -- a failed write just isn't remembered
            print("Moybyte system save failed:", _err_text(exc))

    def _ota_channel(self):
        """The selected OTA update channel ("stable" default / "unstable" beta). Drives
        which manifest UPDATE ONLINE checks; persisted in system.json."""
        return self.system.get("ota_channel", "stable")

    def _cycle_channel(self, d):
        """Toggle the OTA channel STABLE<->UNSTABLE and persist. Two channels, so any
        step flips. This only changes what UPDATE ONLINE checks -- the running firmware
        is unchanged until a manifest is actually installed (and the bootloader's
        rollback still guards a bad beta image)."""
        self.system["ota_channel"] = (
            "stable" if self._ota_channel() == "unstable" else "unstable")
        self._persist_system()

    def load_achievements(self):
        """Load the unlocked achievements (moy_carts achievements.json) and wire the
        store + unlock-beep into a fresh Achievements (#21). Safe no-op on an
        embedded/no-store boot -- then the achievements stay in volatile RAM (still
        awarded + toasted this session, just not remembered). Call after the store +
        carts_root are injected (host build_workstation / device run_desktop)."""
        unlocked = []
        if self.carts_store is not None and self.carts_root is not None:
            try:
                unlocked = self._with_sd(
                    lambda: self.carts_store.load_achievements(self.carts_root)) or []
            except Exception as exc:  # noqa: BLE001 -- a bad store must not crash boot
                print("Moybyte achievements load failed:", _err_text(exc))
                unlocked = []
        self.ach = Achievements(unlocked, on_save=self._save_achievements,
                                on_unlock=self._achievement_unlocked)

    def _save_achievements(self, ids):
        """Persist the unlocked-id list through the SD wrapper, when writes are on.
        A failed/disabled write just isn't remembered (the badge still shows this
        session) -- never fatal."""
        if not (self.carts_store is not None and self.carts_root is not None
                and self.can_manage):
            return
        self._with_sd(lambda: self.carts_store.save_achievements(ids, self.carts_root))

    def _achievement_unlocked(self, ach_id):
        """Celebrate a fresh unlock with a short rising beep, when audio is wired.
        Best-effort -- a silent backend (or none) just skips it. The toast is the
        primary, always-present feedback; the beep is the cherry on top."""
        au = self.audio
        if au is not None:
            try:
                au.beep(880, 0.08)
                au.beep(1320, 0.12)
            except Exception:  # noqa: BLE001
                pass

    # -- hidden Easter eggs (#21) now live on self.ach_ui (achievements_ui.py,
    # AchievementsUI): the 3 eggs + their trigger/popup state + _show_egg/
    # _egg_active + _draw_egg/_draw_confetti/_draw_achievements. The achievement
    # core above (load_achievements/_save_achievements/_achievement_unlocked +
    # self.ach) stays here.

    def select_wallpaper(self, wp_id, persist=True):
        """Choose the desktop backdrop. `wp_id` is a wallpaper cart slug or a
        "fill:<color>" built-in; an unknown/None id falls back to the first
        available option. Compiles the chosen cart into its own namespace (or sets
        a solid fill) and, when persist, writes the choice to system.json."""
        opts = self.wallpaper_options()
        if wp_id not in opts:
            wp_id = opts[0] if opts else self._FILL_WALLPAPERS[0]
        self.wallpaper_id = wp_id
        self.wallpaper.clear()
        if not (isinstance(wp_id, str) and wp_id.startswith("fill:")):
            cart = self._wp_cart_by_id(wp_id)
            if cart is not None:
                self.wallpaper.compile(cart)   # compile into the backdrop component (#28)
        if persist:
            self._persist_wallpaper()

    def _persist_wallpaper(self):
        self.system["wallpaper"] = self.wallpaper_id
        if not (self.carts_store is not None and self.carts_root is not None
                and self.can_manage):
            return
        try:
            self._with_sd(lambda: self.carts_store.save_system(self.system, self.carts_root))
        except Exception as exc:  # noqa: BLE001 -- a failed write just isn't remembered
            print("Moybyte system save failed:", _err_text(exc))

    def cycle_wallpaper(self, d):
        """Step the wallpaper choice by d (Settings < / > stepper); applies +
        persists immediately so the desktop updates live."""
        opts = self.wallpaper_options()
        if not opts:
            return
        cur = self.wallpaper_id if self.wallpaper_id in opts else opts[0]
        nxt = opts[(opts.index(cur) + d) % len(opts)]
        self.select_wallpaper(nxt, persist=True)
        self.ach.note("wallpaper_change")       # "Home Decorator": changed the backdrop (#21)

    # (The wallpaper RENDERING -- _draw_wallpaper + _compile_wallpaper + the compiled-cart
    # cache -- moved to the Wallpaper component (wallpaper.py); self.wallpaper.draw(dt) is
    # called by the launcher home + Settings, and select_wallpaper drives it.)

    def _icon_sheet_for(self, cart):
        """A cached sprite Image for a cart's desktop icon (its sheet tile 0), or
        None when the cart has no art (then the type glyph is drawn). Cached per
        cart path so the grid doesn't rebuild a sheet every frame."""
        key = cart.get("path") or cart.get("title")
        cache = self._icon_cache
        if key in cache:
            return cache[key]
        sheet = self._build_sheet(cart)             # shared sprite-load + fallback
        img = sheet.tile_image(0, -1) if not sheet.is_blank() else None
        cache[key] = img
        return img

    # -- Settings screen (#28) -----------------------------------------------
    #
    # Wallpaper is FUNCTIONAL (applies + persists); the rest are real-looking but
    # no-op controls clearly marked "soon", so the layout is proven without
    # committing to backends. Each row is (key, label, kind): "wallpaper" is the
    # live one; "mock" rows just step a cosmetic placeholder value.


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
        if self.screen != "settings":
            self._settings_return = self.screen   # resume here on exit (cart vs home)
        self._dirty = True             # screen change repaints (#44)
        self.settings_layer.reset()    # reset the selection + scroll window (#53)
        self.screen = "settings"
        self.show_achievements = False
        self.ach_ui._secret_taps = 0              # fresh secret-door run each visit (#21)
        self._set_text_mode(False)

    def _exit_settings(self):
        # Close Settings back to wherever it was opened from: resume the running cart
        # if we came from one (the gear on the in-cart bar), else the launcher home.
        if getattr(self, "_settings_return", "launcher") == "desktop" and self.cart is not None:
            self.run(self.project, self.launcher_layer)   # resume the running cart
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
        self.sysmenu.toggle(self.menu_ui._sysmenu_items())

    def _toggle_web_view(self):
        """Flip the device web view on/off via the injected controller (#41). Guarded
        so a backend hiccup (e.g. WiFi not up yet -> can't bind) can never crash
        Settings; the row just stays OFF and the controller may surface a reason."""
        hook = self.web_hook
        if hook is None:
            return
        self._dirty = True
        try:
            hook.toggle()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte web view toggle failed:", exc)

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

    # -- cart-run forwards (Stage 2, player.py) ------------------------------
    #
    # The Player owns the running cart's live state now; these forwarding properties
    # delegate reads AND writes to it, so every surface file + test that touches
    # ws.cart_error / ws.crash_line / ws.cart_paused / ws.ns / ws._update / ws._draw /
    # ws._cart_key_prev is byte-for-byte unchanged (the exact mirror of the Stage-1
    # project.* forwards above). (_bks_prev + _cart_start_ms have no external reader --
    # they stay private on the Player, no forward.)
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
    def cart_paused(self):
        return self.player.cart_paused

    @cart_paused.setter
    def cart_paused(self, value):
        self.player.cart_paused = value

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
    # keeps working. EditorApp.tab is the source of truth; menu_view is deleted only at
    # the END of the split Stage 6, once the back-stack routes and nothing reads it.
    @property
    def menu_view(self):
        return self.editor_app.tab

    @menu_view.setter
    def menu_view(self, value):
        self.editor_app.tab = value

    # -- run / exit (Stage 2: the run/return stack discipline) ----------------

    def run(self, project, caller):
        """Show `project`'s running cart on the desktop, recording `caller` so QUIT
        knows where to return (spec Section 2's run/return -- a stack discipline, not a
        blocking call, since the frame loop can't block). The cart itself is started by
        the explicit _start() at each call site (open/apply/run_code/_leave_menu); run()
        makes the desktop layer active + records the caller. Today the only caller is
        the home root (go_home's target), so pop-to-caller == go_home -- behavior is
        unchanged. Stage 3 makes the Editor a second caller, proving the decoupling."""
        self._run_caller = caller
        self.screen = "desktop"

    def _exit_to_caller(self):
        """Pop the running cart back to whoever launched it (run()'s recorded caller,
        spec Section 2's launch-and-return). The Editor is the second caller now
        (Stage 3b): a cart run from PLAY returns to the Editor on the tab it left
        (screen -> "menu"; editor_app.tab is preserved -> the SAME tab), proving the
        Player has zero knowledge of who launched it. Any other caller (the launcher
        home root, or None) pops all the way home."""
        if self._run_caller is self.editor_app:
            self._dirty = True             # screen change repaints (#44)
            self.screen = "menu"           # back to the Editor on editor_app.tab
        else:
            self.go_home()

    def _draw_cart_bar(self):
        """Draw the unified top bar over the pause/crash frame (the cart-path chrome).
        The bar is the shell's, not the Player's, so its draw + the _pf_bar (CHROMEBRK)
        accounting stay here; the Player asks for it via this thin helper so player.py
        never reaches the bar surface directly (the Stage-2 isolation guarantee)."""
        _perf = self.perf_hud or self.perf_capture
        _tb = _ticks_ms() if _perf else 0
        self.bar_layer._draw_status_strip("desktop")   # unified top bar (tool switcher)
        if _perf:
            self._pf_bar = _ticks_diff(_ticks_ms(), _tb)   # CHROMEBRK: the bar's share

    def _cart_bar_tap(self, px, py):
        """Route a pause/crash-frame tap to the top-bar tool switcher (bar-owned),
        returning True iff a tool icon consumed it. Same isolation reason as
        _draw_cart_bar: the Player calls this instead of reaching the bar surface."""
        return self.bar_layer.handle_cart_tap(px, py)

    def _draw_error_panel(self):
        # The on-canvas crash report moved to Player (Stage 2); this stays as the tested
        # ws. entry point the cards surface reuses for its own malformed-card panel
        # (cards_layer sets ws.cart_error then calls ws._draw_error_panel()).
        self.player._draw_error_panel()

    def _open_workspace(self):
        # Build a fresh Project for the SELECTED cart + start it (shared by open()
        # [play] and open_in_editor() [edit] -- the two tap-mode landings, Section 4).
        # Leaves the cart STARTED so PLAY can run it and the editors have live data.
        self.project = Project(self)   # a fresh workspace for the cart being opened
        self.cart = self.launcher.selected()
        self.config = dict(self.cart["cfg"])
        self.cart_paused = False
        self.cards_layer.reset()      # fresh card selection/scroll for the new cart
        self.editor = None
        self.paint = None
        self.map_ui.reset()
        self.music_ui.reset()
        self.block_ui.reset()
        self.cart_error = None
        self.save_status = None
        self.sheet = self._build_sheet()
        self.tilemap = self._build_tilemap()
        self.images = self.cart.get("images") or {}   # paint-image assets (#63)
        self.pmem = self._build_pmem()
        self._cart_key_prev = 0       # fresh cart: no stale key edge
        self.input.text_mode = False  # a fresh cart starts in game mode (#38/#42);
                                      # it opts into text input via textmode(True)
        self.menu_view = "cards"
        self._set_text_mode(False)
        self._start()
        # Achievements (#21): opening a cart is "First Steps"; opening _PLAY_GOAL
        # distinct carts is "Cart Explorer". Key by the cart's path/title so it's
        # the SAME identity the launcher uses (distinct carts, not repeat opens).
        self.ach.note("open", self.cart.get("path") or self.cart.get("title"))

    def open(self):
        # PLAY landing (Section 4 player mode): build the workspace + run the cart on
        # the desktop, recording the launcher home as the caller so QUIT pops home.
        # Open to the desktop even if the cart failed to start: frame() shows the
        # error panel there and the EDIT/CODE button stays reachable so the kid can
        # fix it (a silent stay-on-launcher would be a dead end on the device).
        self._open_workspace()
        self.run(self.project, self.launcher_layer)   # activate desktop, record caller

    def open_in_editor(self):
        # EDIT landing (Section 4 maker mode -- the DEFAULT): build the workspace, then
        # drop into the Editor on Config (spec Section 6). The cart is started (ready
        # for PLAY) but not shown; the Editor owns the screen until PLAY runs it.
        self._open_workspace()
        self.editor_app.open(self.project)

    def launch_selected(self):
        """A launcher TAP opens the selected cart in the mode chosen by system.json's
        `tap_mode` (spec Section 4): "player" plays it immediately, "maker" (the
        DEFAULT) drops into the Editor on the Config page. Both actions stay reachable
        regardless of mode -- the Editor has PLAY, and a running cart pauses to EDIT."""
        if self.system.get("tap_mode", "maker") == "player":
            self.open()
        else:
            self.open_in_editor()

    # The four builders moved VERBATIM onto Project (Stage 1, project.py); these stay
    # as one-line forwards so ws._build_sheet(cart)/... keep working (the wallpaper
    # runner + _icon_sheet_for call ws._build_sheet(cart), _start calls _build_audio,
    # and open() calls all four -- all through self.project now).
    def _build_sheet(self, cart=None):
        return self.project._build_sheet(cart)

    def _build_pmem(self):
        return self.project._build_pmem()

    def _build_tilemap(self, cart=None):
        return self.project._build_tilemap(cart)

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

    def _set_code_error(self, msg):
        """Record a syntax error so the code view can mark the offending line
        inline (#24). compile_check formats messages as "line N: <reason>"; pull
        N out for the marker, keep the short reason for the inline note, and move
        the caret onto that line so the fix is one tap away."""
        row = None
        short = msg
        if msg.startswith("line "):
            rest = msg[5:]
            p = rest.find(":")
            if p > 0 and rest[:p].strip().isdigit():
                row = int(rest[:p].strip()) - 1
                short = rest[p + 1:].strip()
        self._mark_code_error(row, short)

    def _mark_code_error(self, row, short):
        """Record an inline error marker (#24) and, if the editor is open, move
        the caret onto `row` (0-based) so the fix is one tap away."""
        self.code_err = short
        self.code_err_row = row
        if row is not None and self.editor is not None:
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

    def save_sprites(self):
        # Store-write moved to Project.commit_sprites (Stage 1b); this stays as the
        # tested ws. entry point PaintLayer's SAVE dispatches to.
        self.project.commit_sprites()

    def save_icons(self):
        """Persist the edited system icon sheet to system_icons.moygfx (Stage 2 / #52),
        the exact mirror of save_sprites/save_shared_sheet: to_hex -> the SAME SD
        wrapper the cart-sprite save uses (host: direct write; device: with_sd_live).
        Then invalidate the bar caches so the NEXT bar draw shows the new pixels live:
        set_icon_sheet drops the per-kind _SheetSprite cache (and with it the device's
        per-Image RGB565 blit cache), and the sheet's gen already bumped on each pset
        so any gen-keyed cache rebuilds too. Surfaces a save status like the cart
        paint editor. A bad store/no SD root is a no-op (writes deferred)."""
        if not (self.icon_sheet and self.carts_root and self.can_manage):
            return
        hexs = self.icon_sheet.to_hex()
        try:
            self._with_sd(lambda: self.carts_store.save_system_icons(hexs, self.carts_root, _ICON_VERSION))
            self.icon_sheet.dirty = False
            self.save_status = "SAVED"
            # Re-adopt the (same) sheet so the bar's per-kind image cache is dropped and
            # the next _draw_status_strip rebuilds its sprites from the freshest pixels.
            self.set_icon_sheet(self.icon_sheet)
            self.ach.note("paint_save")         # "Little Artist": a theme saved (#21)
        except Exception as exc:  # noqa: BLE001
            # Mirror save_sprites: a failed save must be VISIBLE on device (no serial in
            # the run loop), not silent. _err_text-guarded so a weird __str__ can't escape.
            txt = _err_text(exc)
            self.save_status = "SAVE FAILED"
            self.cart_error = "Could not save icons -- " + txt
            print("Moybyte save icons failed:", txt)

    def _leave_theme(self):
        # CLOSE/back from the theme editor -> the lifecycle lives on self.theme_layer;
        # this stays reachable (PaintLayer's CLOSE tap dispatches ws._leave_theme()).
        self.theme_layer.leave()

    def save_map(self):
        # Store-write moved to Project.commit_map (Stage 1b); this stays as the tested
        # ws. entry point MapEditorUI's SAVE dispatches to.
        self.project.commit_map()

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
        """Read the shared sheet into a SpriteSheet (empty one if never saved)."""
        try:
            hexs = self._with_sd(lambda: self.carts_store.load_shared_sheet(self.carts_root))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte load shared sheet failed:", exc)
            return None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet()

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
            self.paint_status = "GET FAILED"
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
            self.paint_status = "PUT FAILED"
            return False
        n = self.paint.n
        if shared.copy_tile(self.sheet, n, dst_n=n) is None:
            self.paint_status = "PUT FAILED"
            return False
        try:
            hexs = shared.to_hex()
            self._with_sd(lambda: self.carts_store.save_shared_sheet(hexs, self.carts_root))
        except Exception as exc:  # noqa: BLE001
            self.paint_status = "PUT FAILED"
            print("Moybyte save shared sheet failed:", exc)
            return False
        self.paint_status = "PUT SPR " + str(n)
        return True

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
        self.cart_paused = False
        self._set_text_mode(False)    # restore the game-button keyboard mode
        self.editor = None
        self.paint = None
        self._editing_icons = False    # never carry the theme-editing flag home
        self.map_ui.reset()
        self.block_ui.reset()
        self.screen = "launcher"
        self.cart = None
        self.ns = None
        self.cart_error = None
        self.save_status = None
        self.show_achievements = False
        self.ach_ui._konami_pos = 0          # fresh Konami run on the home desktop (#21)
        self.ach_ui._clock_taps = 0

    # -- cart management (SD) ------------------------------------------------
    #
    # Each action mounts the SD card, mutates, and re-scans within a single
    # _with_sd session, then the card is unmounted before the next flush.

    def _apply_items(self, items):
        if items:
            self.launcher.set_items(items)

    def new_cart(self):
        if not self.carts_root or not self.can_manage:
            return
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.new_from_template(self.carts_root),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte new cart failed:", exc)

    def dup_cart(self):
        if not self.carts_root or not self.can_manage or not self.launcher.selected():
            return
        sel = self.launcher.selected()
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.duplicate(sel, self.carts_root),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte duplicate failed:", exc)

    def del_cart(self):
        if not self.carts_root or not self.can_manage or len(self.launcher.items) <= 1:
            return  # keep at least one cartridge
        sel = self.launcher.selected()
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.delete(sel),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte delete failed:", exc)

    def adjust(self, d):
        # Config mutation stays on Workstation (ws.config is the single source of cart
        # state); the CARD selection lives on cards_layer, so read msel from there.
        f = self.cart["edit"][self.cards_layer.msel]
        key = f["key"]
        cur = self.config.get(key, f.get("default"))
        if f["type"] == "int":
            v = int(cur) + d * f.get("step", 1)
            if "min" in f:
                v = max(f["min"], v)
            if "max" in f:
                v = min(f["max"], v)
            self.config[key] = v
        elif f["type"] == "choice":
            ch = f["choices"]
            idx = ch.index(cur) if cur in ch else 0
            self.config[key] = ch[(idx + d) % len(ch)]

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
        # Router (docs/shell_layers_refactor_v1.md §3): walk the z-ordered layer stack
        # top -> bottom and hand the frame's keys to the first layer that claims them.
        # A modal overlay (About / system menu, #52) sits above the content, so it eats
        # this frame's keys before they can leak to the screen underneath; the active
        # content layer is at the bottom and always consumes.
        i = self.input
        # Redraw-on-change (#44): a button PRESS edge or a typed key this frame may
        # change visible state (nav, select, screen/menu switch, an edit), so request a
        # repaint. Only the press edge (not release, not a steady hold) is marked: every
        # UI handler acts on i.pressed()/the typed key, never on the release, so a press
        # draws exactly one frame and the UI is static again -- a release/hold that
        # changes nothing costs nothing. Pointer-driven changes (click/drag/cursor move)
        # are caught separately in frame() via the pointer-state snapshot. Conservative
        # but never stale: a press that's a no-op costs one redraw, not a wrong screen.
        if getattr(i, "_pressed", None) or i.last_key:
            self._dirty = True
        for layer in reversed(self._visible_stack()):
            if layer.handle_input(i):
                return

    # (The desktop/running-cart keyboard handler -- the #71 BACKSPACE-toggles-pause
    # logic -- moved to Player.handle_input (Stage 2, player.py); the "desktop"
    # content layer routes to it via _PlayerLayer.)

    # -- pointer (trackball-as-mouse) ----------------------------------------

    def handle_pointer(self):
        # Router (docs/shell_layers_refactor_v1.md §3): publish the game-space pointer
        # (so a cart's touch()/mouse() reads the 320x240 viewport, not the panel, #39),
        # then walk the z-ordered stack top -> bottom and let the first layer that
        # claims the tap handle it. A modal overlay (About / system menu) sits above the
        # content, so it consumes the tap (and clears the game pointer's tap so a running
        # cart never also sees a tap the menu swallowed) before it can leak underneath.
        p = self.pointer
        if p is None:
            return
        px, py, click = p.x, p.y, p.click
        gx, gy = self._game_xy(px, py)
        self.input.game_pointer = (gx, gy, click, p.down)
        for layer in reversed(self._visible_stack()):
            if layer.handle_pointer(px, py, click):
                return

    # (The desktop/running-cart pointer handler -- the pause QUIT/CONTINUE + FPS-chip
    # tap + top-bar tool-switcher routing -- moved to Player.handle_pointer (Stage 2,
    # player.py); the "desktop" content layer routes to it via _PlayerLayer. The bar
    # draw/tap it needs stay on the shell, reached via _draw_cart_bar / _cart_bar_tap.)

    def nav(self, dx, dy):
        # Directional input (host arrows / device trackball). In the code editor it
        # moves the CARET (the view follows it); elsewhere the launcher/desktop are
        # pointer-driven, so this is a no-op there.
        if (self.screen == "menu" and self.menu_view == "code"
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

    def _flush_batches(self):
        # Draw any sprites still pending in a canvas's auto-batch (Fold 1, #63) before
        # the frame is composited / flushed to the panel, so nothing queued by the last
        # spr() in a cart's _draw() (or the chrome) is left unpainted. Guarded + covers
        # both the game and system canvas (the same object in the 320x240 device case,
        # so the second flush is a no-op).
        for cv in (self.canvas, self.sys_canvas):
            fb = getattr(cv, "flush_batch", None)
            if fb is not None:
                fb()

    # -- two-domain composite + viewport coords (#39) ------------------------

    def _viewport(self):
        """The composited game viewport as (ox, oy, scale) -- the top-left of the
        320x240 game canvas inside the system canvas, and its integer scale. (0, 0,
        1) when the two canvases are the same object (degradation)."""
        gc = self.canvas
        sc = self.sys_canvas
        if sc is gc:
            return (0, 0, 1)
        scale = min(sc.w // gc.w, sc.h // gc.h)
        if scale < 1:
            scale = 1
        ox = (sc.w - gc.w * scale) // 2
        oy = (sc.h - gc.h * scale) // 2
        return (ox, oy, scale)

    def _game_xy(self, px, py):
        """Map a SYSTEM-canvas point (where the pointer lives) into GAME-canvas
        coords, so a running cart / the editors (drawn in the 320x240 viewport) hit-
        test correctly. Identity in the degradation case."""
        ox, oy, scale = self._viewport()
        return ((px - ox) // scale, (py - oy) // scale)

    def _composite_game(self):
        """Blit the fixed 320x240 GAME canvas into the SYSTEM canvas as a
        fixed-aspect, integer-scaled, centered viewport, filling the letterbox with
        a solid bezel color. A no-op when the two canvases are the same object (the
        degradation case: 320x240 system canvas == game canvas, pixel-identical to
        today). Index-only (host == device): reads game indices, writes them scaled
        into the system buffer, so no palette resolve is needed."""
        gc = self.canvas
        sc = self.sys_canvas
        # #63: complete any sprites still queued in the game canvas's auto-batch before
        # its buffer is read (usually already flushed by _reset_canvas_state; belt-and-
        # suspenders so a missed reset can never drop a cart's last sprite run).
        _fb = getattr(gc, "flush_batch", None)
        if _fb is not None:
            _fb()
        if sc is gc:
            return
        ox, oy, scale = self._viewport()
        sc.cls(_VIEWPORT_BEZEL)                     # letterbox fill
        gbuf = getattr(gc, "buf", None)
        sbuf = getattr(sc, "buf", None)
        if gbuf is None or sbuf is None:
            # A recording system canvas (the web CommandCanvas) has no framebuffer to
            # copy into -- blit the whole game frame as one scaled sprite so the draw
            # stream carries the viewport. The game canvas must expose its pixels.
            self._composite_via_spr(gc, sc, gbuf, ox, oy, scale)
            return
        gw = gc.w
        sw = sc.w
        sh = sc.h
        vw = gw * scale
        # The viewport always fits a system canvas >= the game (the supported case),
        # so take the fast row-replication path. A degenerate smaller-than-game system
        # canvas (negative offset / overflow) falls to a clipped per-pixel path that
        # can never resize the bytearray.
        fits = ox >= 0 and oy >= 0 and ox + vw <= sw and oy + gc.h * scale <= sh
        if fits:
            for gy in range(gc.h):
                grow = gy * gw
                for s in range(scale):
                    base = (oy + gy * scale + s) * sw + ox
                    if scale == 1:
                        sbuf[base:base + gw] = gbuf[grow:grow + gw]
                    else:
                        out = base
                        for gx in range(gw):
                            sbuf[out:out + scale] = bytes((gbuf[grow + gx],)) * scale
                            out += scale
            return
        for gy in range(gc.h):                      # clipped fallback (defensive)
            grow = gy * gw
            for s in range(scale):
                dy = oy + gy * scale + s
                if dy < 0 or dy >= sh:
                    continue
                dx0 = ox if ox > 0 else 0
                dx1 = min(sw, ox + vw)
                if dx1 <= dx0:
                    continue
                base = dy * sw
                for dx in range(dx0, dx1):
                    sbuf[base + dx] = gbuf[grow + (dx - ox) // scale]

    def _composite_via_spr(self, gc, sc, gbuf, ox, oy, scale):
        """Composite by blitting the game frame as ONE scaled sprite -- the path for a
        recording system canvas (the web CommandCanvas) that has no framebuffer to
        copy into. Records a single spr command per frame carrying the game pixels."""
        if gbuf is None:
            return
        img = _Blit(gc.w, gc.h, list(gbuf), -1)     # opaque (no transparent index)
        sc.spr(img, ox, oy, scale)

    # -- redraw-on-change (#44 step 1) ---------------------------------------

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
        # A running cart on the desktop draws every frame (unless it crashed, when the
        # error panel is static, or it's PAUSED -- the pause menu is a still frame, so
        # an idle paused game costs ~0 like any static screen, #71).
        if self.screen == "desktop" and self.cart_error is None \
                and not self.cart_paused and (
                self._update is not None or self._draw is not None):
            return True
        # A music-editor preview must keep ticking the mixer + redrawing the PLAY/STOP
        # button (and clearing the flag when the effect ends) without input (#50).
        if self.screen == "menu" and self.menu_view == "music" \
                and self.music_ui.music_preview is not None:
            return True
        # A live wallpaper animates the home/settings backdrop.
        if self.screen in ("launcher", "settings") and self.wallpaper.is_animating(dt):
            return True
        # A firmware install (#53) advances a chunk per frame; "done" runs a short
        # reboot countdown; "checking"/"downloading" (Phase 3) step the online flow.
        # All must keep redrawing so progress animates and the work proceeds without input.
        if self.screen == "update" and self.update_ui._upd_phase in (
                "install", "done", "checking", "downloading"):
            return True
        # Transient overlays redraw while they're up.
        if self.ach_ui._confetti_until and _ticks_diff(self.ach_ui._confetti_until, _ticks_ms()) > 0:
            return True
        if self.ach_ui._egg_active():
            return True
        if self.ach.toast_active():
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

    def _draw_menu_backdrop(self):
        # Draw the frozen cart frame as the backdrop under an editor panel (cards /
        # paint / map), then clear its camera/clip/pal/palt (#11) so the panel draws
        # unaffected. Shared by _draw_content_menu (cards/paint) and _MapLayer.
        try:
            if self._draw:
                self._draw()
        except Exception:
            pass
        self._reset_canvas_state()

    def frame(self, dt):
        if dt > 0:
            inst = 1.0 / dt
            # EMA so the readout reflects sustained rate, not single-frame jitter.
            self._fps = inst if self._fps <= 0 else self._fps + (inst - self._fps) * 0.15
        # Boot logo: expire the splash before the redraw gate so THIS frame reveals the
        # launcher. While it's live it's an _animating source, so the loop keeps flushing
        # it; marking dirty on expiry guarantees the launcher paints on the next frame.
        if self._splash_until is not None and _ticks_diff(self._splash_until, _ticks_ms()) <= 0:
            self._splash_until = None
            self._dirty = True
        # Redraw-on-change (#44): a static UI screen (no animation, no pointer change,
        # nothing marked dirty) is skipped entirely -- no draw, no flush. The panel /
        # host window simply retains the last frame, so an idle UI costs ~0 and the
        # device saves the SPI flush + power. A running cart / live wallpaper / active
        # overlay always reports animating, so it redraws every frame as before.
        if not self._needs_redraw(dt):
            return
        # Perf HUD (#43/#44): mark the start of this frame's draw work. Cheap (one
        # ticks call); only meaningful for a frame we actually paint, so it's after
        # the redraw gate. _flush_ms is filled around comp.flush() below; _draw_ms
        # is the rest (total span - flush). Both EMA-smoothed at frame end. Also
        # fires when perf_capture is set (device diag sampling) -- not just the HUD.
        _perf = self.perf_hud or self.perf_capture
        _frame_t0 = _ticks_ms() if _perf else 0
        if _perf:
            _bc = getattr(self.canvas, "batch_reset", None)
            if _bc is not None:
                _bc()                  # #63: zero this frame's auto-batch profiling counters
        # Per-frame perf scratch (the running-cart content Layer fills self._pf_*).
        self._pf_upd = 0    # cart _update(dt) ms (game LOGIC); 0 off the cart path
        self._pf_cart = 0   # cart _draw() ms (RENDERING)
        self._pf_audio = 0  # audio.tick(dt) ms (mixer feed) -- split out from render
        self._pf_bar = 0    # CHROMEBRK: _draw_status_strip ms (cart path only)
        _cmp = 0            # CHROMEBRK: _composite_game ms
        _cur = 0            # CHROMEBRK: _draw_cursor ms
        # Compositor / router (docs/shell_layers_refactor_v1.md §3): draw the z-ordered
        # visible stack bottom -> top. The active content draws first (game-domain
        # content on the fixed 320x240 game canvas); at the game->system domain boundary
        # the router composites that viewport into the system canvas ONCE (#39; the
        # launcher/settings + responsive code/blocks content are system-domain and skip
        # it); the chrome/overlays + cursor then draw on top on the system canvas. The
        # cursor is always the top system layer, so a game-domain content is always
        # composited before it -- reproducing the pre-refactor single composite step.
        _prev_domain = None
        for layer in self._draw_stack():
            if _prev_domain == "game" and layer.domain == "system":
                _tc = _ticks_ms() if _perf else 0
                self._composite_game()
                if _perf:
                    _cmp = _ticks_diff(_ticks_ms(), _tc)   # CHROMEBRK: viewport composite
            if layer.id == "cursor":
                _tk = _ticks_ms() if _perf else 0
                layer.draw(dt)
                if _perf:
                    _cur = _ticks_diff(_ticks_ms(), _tk)   # CHROMEBRK: cursor
            else:
                layer.draw(dt)
            _prev_domain = layer.domain
        _upd = self._pf_upd
        _cart = self._pf_cart
        _audio = self._pf_audio
        _bar = self._pf_bar
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
            _flush_t0 = _ticks_ms()
            self.comp.flush()
            _flush = _ticks_diff(_ticks_ms(), _flush_t0)
            _total = _ticks_diff(_ticks_ms(), _frame_t0)
            _draw = _total - _flush
            if _draw < 0:
                _draw = 0
            self._flush_ms = float(_flush) if self._flush_ms <= 0 \
                else self._flush_ms + (_flush - self._flush_ms) * 0.15
            self._draw_ms = float(_draw) if self._draw_ms <= 0 \
                else self._draw_ms + (_draw - self._draw_ms) * 0.15
            # DRAWBRK split: cart _update (logic) / cart _draw (render) / audio.tick /
            # console chrome (remainder = dock + cursor + overlays).
            _chrome = _draw - _upd - _cart - _audio
            if _chrome < 0:
                _chrome = 0
            # raw per-frame copies for the hitch logger (#66 HITCH v3)
            self._raw_upd = float(_upd)
            self._raw_cart = float(_cart)
            self._raw_audio = float(_audio)
            self._raw_chrome = float(_chrome)
            self._raw_flush = float(_flush)
            self._raw_draw = float(_draw)
            self._upd_ms = float(_upd) if self._upd_ms <= 0 \
                else self._upd_ms + (_upd - self._upd_ms) * 0.15
            self._cart_ms = float(_cart) if self._cart_ms <= 0 \
                else self._cart_ms + (_cart - self._cart_ms) * 0.15
            self._audio_ms = float(_audio) if self._audio_ms <= 0 \
                else self._audio_ms + (_audio - self._audio_ms) * 0.15
            self._chrome_ms = float(_chrome) if self._chrome_ms <= 0 \
                else self._chrome_ms + (_chrome - self._chrome_ms) * 0.15
            # CHROMEBRK sub-split (#66 lever 5): bar / composite / cursor EMAs, so
            # a chrome trim targets the real cost instead of guessing.
            self._bar_ms = float(_bar) if self._bar_ms <= 0 \
                else self._bar_ms + (_bar - self._bar_ms) * 0.15
            self._cmp_ms = float(_cmp) if self._cmp_ms <= 0 \
                else self._cmp_ms + (_cmp - self._cmp_ms) * 0.15
            self._cur_ms = float(_cur) if self._cur_ms <= 0 \
                else self._cur_ms + (_cur - self._cur_ms) * 0.15
        else:
            self.comp.flush()
        # We painted this frame: clear the dirty flag and snapshot the pointer state
        # we just drew, so the NEXT frame only repaints if something changes again.
        self._dirty = False
        self._last_ptr = self._ptr_state()
        self._frames_drawn += 1

    # -- boot logo ------------------------------------------------------------

    def arm_splash(self, ms=None):
        """Show the moybyte boot logo for the next `ms` (default _SPLASH_MS) before the
        launcher appears. Called by the boot entries (device run_desktop, interactive
        host), NOT by construction -- so unit tests that drive frame() see the launcher
        on the first frame."""
        self._splash_until = _ticks_ms() + (int(ms) if ms else _SPLASH_MS)
        self._dirty = True

    def _splash_image(self):
        """The Moy mascot as a 16x16 blittable built straight from _ICON_ART with REAL
        transparency ("." -> -1), cached. Not the icon-sheet tile: an IconSheet is a
        solid indexed grid where a blank pixel is index 0 (black) -- which is also Moy's
        outline colour, so a sheet blit can't tell the outside from the outline and
        boxes the mascot. Building the image here keeps the outline AND lets the dark
        field (and the corner bite) show through."""
        img = getattr(self, "_splash_img", None)
        if img is None:
            art = _ICON_ART.get("moy", ())
            pix = []
            for ly in range(16):
                row = art[ly] if ly < len(art) else ""
                for lx in range(16):
                    pix.append(_nibble(row[lx]) if lx < len(row) else -1)
            img = _SheetSprite(16, 16, pix, -1)
            self._splash_img = img
        return img

    def _draw_splash(self):
        """Paint the boot logo: 'Moy' (the moybyte mascot) scaled up over a dark field,
        with the two-tone `moybyte` wordmark below. Drawn on the SYSTEM canvas (like the
        launcher) so it fills the real panel; host and device share this one path."""
        cv = self.sys_canvas
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
        top = (H - block_h) // 2
        cv.spr(self._splash_image(), (W - side) // 2, top, scale)
        wy = top + side + gap
        # Two-tone wordmark: "moy" in cream, "byte" in the mascot's indigo body colour.
        tw = 7 * word                             # "moybyte" is 7 chars
        wx = (W - tw) // 2
        cv.print("moy", wx, wy, NAMES["white"], 1)
        cv.print("byte", wx + 3 * word, wy, NAMES["indigo"], 1)

    # -- desktop shell drawing (#28) -----------------------------------------

    # (_draw_pause_dim + _draw_pause_buttons -- the #71 pause chrome -- moved to
    # Player (Stage 2, player.py) with the rest of the pause machinery.)

    def _mini_btn(self, label, rect, fill, cv=None):
        # Shared draw toolkit (stays on Workstation per the doc): a tiny labeled button.
        x, y, w, h = rect
        if cv is None:
            cv = self.canvas
        cv.rect(x, y, w, h, fill)
        cv.print(label, x + 2, y + 2, NAMES["black"], 1)

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
        running = (self.screen == "desktop" and self.cart is not None
                   and self.cart_error is None)
        if not running:
            return None
        cart = self.cart
        name = cart.get("title") or cart.get("path") or "?"
        return (name, self._fps, self._flush_ms, self._draw_ms)

    def perf_breakdown(self):
        """(_upd_ms, _cart_ms, _audio_ms, _chrome_ms): the EMA phase split of draw_ms --
        cart _update (game LOGIC), cart _draw (RENDERING), audio.tick (mixer feed), and
        console chrome (dock + cursor + overlays, the remainder). Used by the device
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
        """(bar_ms, composite_ms, cursor_ms, other_ms): the EMA sub-split of the
        DRAWBRK chrome remainder (#66 lever 5) -- the top status bar, the game->
        system viewport composite (~0 when the canvases are one object, i.e. the
        320x240 device), the cursor, and whatever chrome remains unmeasured
        (textmode sync, canvas-state reset, overlays, the final batch guard).
        Only meaningful while a cart runs with perf_capture/perf_hud on; feeds
        the device CHROMEBRK diag line so a chrome trim cuts the real cost."""
        other = self._chrome_ms - self._bar_ms - self._cmp_ms - self._cur_ms
        if other < 0:
            other = 0.0
        return (self._bar_ms, self._cmp_ms, self._cur_ms, other)

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
        # responsive code/block editors (#39 step 2) pass cv=self.sys_canvas so the
        # button + its label scale with the system font. On a plain Canvas font_scale
        # is 1, so this is byte-identical to the original.
        if cv is None:
            cv = self.canvas
        x, y, w, h = rect
        fs = getattr(cv, "font_scale", 1)
        cv.rect(x, y, w, h, fill)
        cv.rectb(x, y, w, h, NAMES["white"])
        # Baseline: the game canvas printed the label at scale 2 (16px) but centered
        # with height 8 (the legacy quirk) -- preserve that VERBATIM at fs==1. On the
        # system canvas SystemCanvas renders petme128 at font_scale (the `2` arg is
        # ignored), so the on-screen text is 8*fs tall -- center it with that.
        if fs <= 1:
            cv.print(label, x + 6, y + (h - 8) // 2, NAMES["black"], 2)
        else:
            cv.print(label, x + 6 * fs, y + (h - 8 * fs) // 2, NAMES["black"], 2)

    def _icon_btn(self, kind, label, rect, fill, cv=None):
        """A button that leads with an icon glyph (pre-literate) and keeps the
        word as a small secondary cue beside it -- so a reader still gets the
        label and a kid who can't read still gets the picture."""
        if cv is None:
            cv = self.canvas
        x, y, w, h = rect
        fs = getattr(cv, "font_scale", 1)
        cv.rect(x, y, w, h, fill)
        cv.rectb(x, y, w, h, NAMES["white"])
        self._glyph(kind, (x + 2 * fs, y, 16 * fs, h), NAMES["black"], cv)
        if label:
            cv.print(label, x + 19 * fs, y + (h - 8 * fs) // 2, NAMES["black"], 1)

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
            self.sys_canvas.spr(CURSOR, self.pointer.x, self.pointer.y, self.font_scale)

    def _glyph(self, kind, rect, c, cv=None):
        # Draw a centered icon glyph in color `c`. Defaults to the GAME canvas (the
        # editors/cart-overlay callers); the desktop/system callers pass cv=
        # self.sys_canvas so the glyph follows the system font scale (#39). The shared
        # blit + the glyph encoding live in the module-level _blit_glyph so Launcher
        # (canvas-only) renders the identical vocabulary.
        _blit_glyph(cv if cv is not None else self.canvas, kind, rect, c)

    def _bar_image(self, kind):
        """The cached 16x16 _SheetSprite for top-bar icon `kind`, or None when the
        icon sheet/slot is missing. Memoised per kind so the SAME image object is
        blitted every frame -- the device caches its RGB565 copy on the image, so the
        bar costs one cached blit per icon (Stage 1's perf goal)."""
        if kind in self._bar_img_cache:
            return self._bar_img_cache[kind]
        img = None
        if self.icon_sheet is not None:
            slot = _ICON.get(kind)
            if slot is not None:
                img = self.icon_sheet.tile_image(slot)   # transparent -1 (icons keyed)
        self._bar_img_cache[kind] = img
        return img

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
                        NAMES["light_grey"], cv)
