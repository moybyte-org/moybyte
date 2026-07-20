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
# their pre-extraction names (_MV_*, _TP_*, _MAP_ZOOM/_MAP_ERASE/_MAP_SAVE/
# _MAP_CLOSE/_MAP_PAN_THRESH, _PAN_*) for the same `console.X`/`C.X` reasons as
# the block editor above, with the same bare-or-package fallback.
try:
    from map_editor_ui import (
        MapEditorUI,
        _MV_X0, _MV_Y0, _MV_AVAIL_W, _MV_AVAIL_H, _MV_FIT_COLS, _MV_FIT_ROWS,
        _mv_default_cell, _MV_ZOOMS, _MAP_ZOOM, _MAP_SIZE, _TP_X0, _TP_Y0,
        _TP_CELL, _TP_COLS, _TP_ROWS, _TP_PAGE, _TP_AREA, _TP_PREV, _TP_NEXT,
        _TP_SKY, _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN, _MAP_ERASE, _MAP_SAVE,
        _MAP_CLOSE, _MAP_PAN_THRESH,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.map_editor_ui import (
        MapEditorUI,
        _MV_X0, _MV_Y0, _MV_AVAIL_W, _MV_AVAIL_H, _MV_FIT_COLS, _MV_FIT_ROWS,
        _mv_default_cell, _MV_ZOOMS, _MAP_ZOOM, _MAP_SIZE, _TP_X0, _TP_Y0,
        _TP_CELL, _TP_COLS, _TP_ROWS, _TP_PAGE, _TP_AREA, _TP_PREV, _TP_NEXT,
        _TP_SKY, _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN, _MAP_ERASE, _MAP_SAVE,
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
        _SceneLayer, _PerfLayer, _AchOverlayLayer, _SysMenuLayer, _AboutLayer)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layers import (
        _LegacyLayer, _PlayerLayer, _BlocksLayer, _UpdateLayer, _MapLayer, _MusicLayer,
        _SceneLayer, _PerfLayer, _AchOverlayLayer, _SysMenuLayer, _AboutLayer)

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
_ui_is_light = _uimod.is_light


# Derived Library covers are sizeable on the desktop tier and DeviceCanvas adds a
# 2-byte RGB565 bake to each cached indexed image.  Keep the cache comfortably
# bounded for the P4 heap while retaining enough variants for the root Library,
# the Make window, and selected/unselected card heights at the same time.
_COVER_CACHE_MAX_ENTRIES = 64
_COVER_CACHE_MAX_PIXELS = 768 * 1024


class _CoverImage:
    """Minimal blittable for a card's COVER art (visual identity v1 Section
    11.4): both canvas backends' spr() read only .w/.h/.pix/.transparent, the
    same contract as editors._SheetSprite."""

    def __init__(self, w, h, pix):
        self.w = w
        self.h = h
        self.pix = pix
        self.transparent = -1
        # Covers are opaque MOY64 bitmaps, just like Paint images.  This marker
        # selects DeviceCanvas's native blit_indices bake and the web recorder's
        # compact base64 image command instead of the generic per-pixel paths.
        self._paint = True


# How long one _CoverJob.step may run inside a frame. Small enough to hide in
# any frame budget; big enough that a 320x240 cover lands in a couple dozen
# frames (~a second of pop-in at UI rates).
_COVER_SLICE_MS = 8
# Per-palette-index run templates for the RLE fill (built lazily, 64 x 255B):
# a run decodes as ONE C-level slice copy instead of a per-pixel loop.
_COVER_RUNS = None


class _CoverJob:
    """A RESUMABLE cover build. Decoding a 320x240 RLE cover + cover-cropping
    it to the card in one go measured 0.5-1.7s per cover on the T-Deck (#66)
    -- one frozen frame per cover even under the one-build-per-frame budget.
    So the build is a little state machine instead: step(t0) advances the
    decode (RLE runs -> the full indexed bitmap, slice-assign fills) and then
    the crop (nearest-sample rows via a precomputed column map) until
    _COVER_SLICE_MS of the frame is spent, and _cover_for re-steps it on the
    following frames until `done`. Any malformed input just finishes with
    img=None -- a corrupt cover means no cover, never a crash."""

    def __init__(self, runs, w, h):
        global _COVER_RUNS
        self.done = False
        self.img = None
        self.w = int(w)
        self.h = int(h)
        self.sw, self.sh, self.packed = runs
        self.pix = bytearray(self.sw * self.sh)
        self.pos = 0                  # decode write cursor (pixels)
        self.i = 0                    # decode read cursor (packed bytes)
        self.out = None               # crop dest (created when decode ends)
        self.dy = 0                   # crop row cursor
        self.xmap = None
        if _COVER_RUNS is None:
            _COVER_RUNS = tuple(bytes((v,)) * 255 for v in range(64))

    def step(self, t0):
        """Advance until ~_COVER_SLICE_MS after t0. Sets self.done (and
        self.img) when the build finishes or the input turns out corrupt."""
        try:
            self._step(t0)
        except Exception:  # noqa: BLE001 -- corrupt cover -> no cover
            self.img = None
            self.done = True

    def _step(self, t0):
        packed = self.packed
        n = len(packed)
        pix = self.pix
        total = self.sw * self.sh
        while self.i < n:
            i = self.i
            pos = self.pos
            for _ in range(128):      # a batch of runs between clock checks
                if i >= n:
                    break
                count = packed[i]
                value = packed[i + 1]
                if count < 1 or value > 63 or pos + count > total:
                    self.img = None
                    self.done = True
                    return
                if count == 1:
                    pix[pos] = value
                else:
                    pix[pos:pos + count] = _COVER_RUNS[value][:count]
                pos += count
                i += 2
            self.i = i
            self.pos = pos
            if _ticks_diff(_ticks_ms(), t0) >= _COVER_SLICE_MS:
                return
        if self.pos != total:         # short stream: corrupt -> no cover
            self.img = None
            self.done = True
            return
        # -- crop phase: match the card's aspect with a centered source
        # window, then nearest-sample it to exactly (w, h), a row per check.
        w, h, sw, sh = self.w, self.h, self.sw, self.sh
        if self.xmap is None:
            cw_ = min(sw, sh * w // h) or 1
            ch_ = min(sh, sw * h // w) or 1
            ox = (sw - cw_) // 2
            self._oy = (sh - ch_) // 2
            self._ch = ch_
            self.xmap = [ox + dx * cw_ // w for dx in range(w)]
            self.out = bytearray(w * h)
        xmap = self.xmap
        out = self.out
        while self.dy < h:
            dy = self.dy
            base = (self._oy + dy * self._ch // h) * sw
            di = dy * w
            for dx in range(w):
                out[di] = pix[base + xmap[dx]]
                di += 1
            self.dy = dy + 1
            if _ticks_diff(_ticks_ms(), t0) >= _COVER_SLICE_MS:
                return
        self.img = _CoverImage(w, h, bytes(out))
        self.done = True


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
# cart-run fields the Player owns (cart_error/crash_line/ns/_update/_draw/_cart_key_prev
# + the private _cart_start_ms) are exposed back as forwarding properties, so every
# surface file + test is unchanged. Stage 5 retired the #71 pause machinery (the
# cart_paused/_bks_prev fields + the _PAUSE_* button geometry are gone) -- the Player
# now exits on hold-BACKSPACE (games) / the bar X (tools). Same bare-or-package fallback as project.py.
try:
    from player import Player
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.player import Player

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

# The window manager (Stage 6 of docs/shell_ux_technical_plan_v1.md, extracted from
# this file -- see wm.py). FullscreenStackWM owns the game<->system viewport composite
# (#39), the process back-stack `screen` projects onto (Stage 6b), and the MEMOIZED
# visible/draw layer stack (Stage 6c -- rebuilt only on a push/pop or overlay-gate
# change, so a static top-of-stack allocates no per-frame list). ws._composite_game/
# _game_xy/_viewport stay one-line forwards (tested surface + many surfaces call
# ws._game_xy). Same bare-or-package fallback as project.py/player.py/editor_app.py.
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
# themeable top-bar IconSheet defaults (_ICON/_ICON_ART/_default_icon_sheet + _ICON_VERSION),
# the cursor sprite (CURSOR), and the small pure helpers (_in/_clamp_scroll/_cursor_delta/
# _ticks_*/_err_text/_from_ascii) -- now live in chrome.py (extracted so the Workstation
# kernel is alone in this file). Imported back + re-exported under the pre-extraction names
# so every `console.X` and bare reference below -- tests included -- still resolves. chrome.py
# is a leaf (it imports only editors/widgets + surface geometry constants, none of which
# import back), so there is no cycle. Same bare-or-package fallback as the surfaces above.
try:
    from chrome import (
        _ticks_ms, _ticks_diff, _err_text, _from_ascii, CURSOR, NAMES, color,
        _DOCK_Y, _DOCK_H, _ICON_COLS, _ICON_ROWS, _ICON_W, _ICON_H, _ICON_GAP_X,
        _ICON_GAP_Y, _ICON_X0, _ICON_Y0, _ICON_BOX, _PAGE_PREV, _PAGE_NEXT,
        _DOCK_W, _DOCK_GAP, _DOCK_X0, _CURSOR_BASE, _CURSOR_ACCEL,
        _BASE_W, _BASE_H, _FONT_W, Layout, CodeLayout, _GLYPH_SIZE, _GLYPHS,
        _blit_glyph, _ICON, _ICON_ART, _ICON_VERSION, _nibble, _default_icon_sheet,
        _cursor_delta, _clamp_scroll, _in, _SPLASH_MS,
        THEMES, DEFAULT_THEME, theme_colors,
    )
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (
        _ticks_ms, _ticks_diff, _err_text, _from_ascii, CURSOR, NAMES, color,
        _DOCK_Y, _DOCK_H, _ICON_COLS, _ICON_ROWS, _ICON_W, _ICON_H, _ICON_GAP_X,
        _ICON_GAP_Y, _ICON_X0, _ICON_Y0, _ICON_BOX, _PAGE_PREV, _PAGE_NEXT,
        _DOCK_W, _DOCK_GAP, _DOCK_X0, _CURSOR_BASE, _CURSOR_ACCEL,
        _BASE_W, _BASE_H, _FONT_W, Layout, CodeLayout, _GLYPH_SIZE, _GLYPHS,
        _blit_glyph, _ICON, _ICON_ART, _ICON_VERSION, _nibble, _default_icon_sheet,
        _cursor_delta, _clamp_scroll, _in, _SPLASH_MS,
        THEMES, DEFAULT_THEME, theme_colors,
    )


# The heavy per-cart payloads the launcher list does NOT need (#66 live-set diet):
# kept resident they are ~300-500KB of permanently-live strings the GC MARK phase
# pays for on every collect (~0.2ms/KB on device -- most of the 93-161ms pauses).
# slim_carts() strips them after the icons are cached; opening a cart rehydrates
# from the store, and switching carts re-slims the previous one.
_HEAVY_CART_KEYS = ("src", "sprites", "sounds", "map", "images", "blocks", "scenes")


def _ema(cur, sample):
    """One-pole EMA (alpha 0.15) with a <=0 "unseeded" bootstrap -- the perf
    readouts' smoothing, written once (frame() applies it to ~10 fields)."""
    return float(sample) if cur <= 0 else cur + (sample - cur) * 0.15

# Frame pacing knob (#63): True locks GAME carts to a steady cadence (30 default,
# manifest "fps": 60 opt-out); False runs everything uncapped at the loop's own
# fps cap -- the measurement mode (owner call 2026-07-08: uncapped for now, we
# want the REAL per-cart numbers while the engine work settles).
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
        # The compositor/router layer stack (docs/shell_layers_refactor_v1.md). Built
        # once here; _visible_stack()/_draw_stack() z-order + gate them per frame.
        self._build_layers()

    def _init_canvases(self, comp, canvas, sys_canvas, font_scale):
        """The two-domain canvas seam (#39), the WM, the theme and the responsive layouts."""
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
        # The window manager (Stage 6, wm.py): owns the game<->system viewport composite
        # (#39) + -- from Stage 6b/6c -- the process back-stack `screen` projects onto and
        # the memoized layer stack. Built here (before anything reads/writes screen or
        # composites) with a `ws` back-ref to the console's canvases + layer instances.
        self.wm = FullscreenStackWM(self)
        # windowed_chrome is a PROPERTY (world-aware, two-worlds #105): it is
        # True only while the windowed WM's DESK (the make world) is open --
        # see the property below the screen projection.
        # Panel THEME (Settings -> THEME): the chrome token set the panels/window
        # chrome/selection accents read each draw. Default = the moybyte "night"
        # colorway (today's exact colors); load_system applies the persisted pick.
        self.theme_name = DEFAULT_THEME
        self.theme_colors = theme_colors(DEFAULT_THEME)
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

    def _init_components(self, input, carts):
        """Injected-service attach points + the shell processes (Project/Player/
        EditorApp) + the extracted editor/HUD UIs."""
        self.input = input
        self.make_api = None       # injected: make_api(canvas, input, cfg, sheet, audio, tilemap, pmem, wifi)->ns
        self.artwork = ArtworkService(self)  # narrow capability for the shipped Paint app
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
        self.carts_store = None     # injected: cart store module (moy_carts API)
        # #67 dual-runtime seam: factory(ns, src) -> a running Lua cart handle
        # (.init/.update/.draw callables + .close()). build_workstation injects
        # the lupa-backed runtime/lua_host.py; the device injects moy_lua once
        # Phase 1 lands. None = "runtime": "lua" carts open the error panel.
        self.lua_runtime = None
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
        # The scanned cart list is the single source both grids derive from (#carts):
        # the LAUNCHER grid is the pinned "Make" tile + the run-grid carts, and the
        # Editor's PROJECT-PICKER grid is the pinned "+ New" tile + every editable cart.
        # Kept here so wallpaper discovery + the wifi-tool lookup read the FULL list
        # rather than either display grid (the Make/New pseudo tiles never leak out).
        self._all_carts = list(carts) if carts else []
        self._fat_cart = None         # #66 live-set diet: the one rehydrated cart
        # Launcher search (#105): plain-text substring filter over the run-grid,
        # entered via the sysmenu SEARCH item (mirrors the wifi-password typing
        # idiom -- _set_text_mode swaps the keyboard to clean ASCII while typing).
        # search_query persists once typing stops (so the filtered grid stays up
        # for d-pad/trackball browsing); search_typing is just the keystroke-
        # capture sub-state. See _launcher_view_items/open_search/close_search.
        self.search_query = ""
        self.search_typing = False
        self.launcher = Launcher(self._launcher_items(self._all_carts),
                                 self.layout, NAMES, _blit_glyph)
        # The HOME grid exposes the selected card's PLAY/CHANGE row on the desktop-
        # density tiers (visual identity v1 Section 1.2); the picker below keeps the
        # single-verb pick, so the flag stays off there.
        self.launcher.actions = True
        # The shelf's pseudo cards (MAKE STUDIO / + New) draw the real themeable
        # IconSheet pencil/plus sprite big -- keyed so the sheet's 0-filled
        # backdrop doesn't plate the card.
        self.launcher.icon_for = self._icon_image_keyed
        self.launcher.cover_for = self._cover_for
        # The favorite-star corner badge (#105): only the RUN grid plays, so only
        # ws.launcher (not the Editor's project-picker) gets the toggle.
        self.launcher.favorite_for = self.is_favorite
        # Default the highlight to the first RUNNABLE cart (skip the pinned Make tile at
        # slot 0), so a bare RUN/A plays a game rather than opening the picker -- the
        # launcher is RUN-first (spec shell_ux_v1.md); Make is a tap/nav target.
        self.launcher.sel = next((i for i, it in enumerate(self.launcher.items)
                                  if it.get("path")), 0)
        # The Editor's project-picker grid (spec shell_ux_v1.md): its own Launcher
        # instance so it keeps an independent selection/page, reusing the SAME tile
        # rendering. `ws.editor_picker` (the content Layer) draws it; ws.pick_selected
        # opens the chosen cart in the Editor.
        self.picker = Launcher(self._picker_items(self._all_carts),
                               self.layout, NAMES, _blit_glyph)
        self.picker.icon_for = self._icon_image_keyed
        self.picker.cover_for = self._cover_for
        # Screen states (#28): "launcher" is now the DESKTOP home (wallpaper + cart
        # icon grid + dock); "desktop" is a running cart; "menu" is the cards/code/
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
        # (Stage 4, docs/shell_ux_technical_plan_v1.md): the Editor now lends the top
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
        self._cover_cache = {}        # (path, w, h) -> shelf-card cover blittable (or None)
        self._cover_cache_order = []  # LRU keys (oldest first); bounds resize variants
        self._cover_cache_pixels = 0  # indexed pixels; device RGB bakes add 2B each
        self._cover_jobs = {}         # (path, w, h) -> in-flight _CoverJob (time-sliced)
        self._cover_built = False     # per-frame cover-build budget (one; see _cover_for)
        self._covers_deferred = False # a build was pushed past the budget -> stay dirty
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
        # Undo-journal idle-typing debounce (Stage 7 of docs/shell_ux_technical_plan_v1.md):
        # _edit_ms is the ticks of the last keystroke in the code editor (None = no
        # pending edit); frame() fires a durable, INVISIBLE autosave-commit once
        # _edit_debounce_ms of no keystroke elapse, so the SD write lands in a typing
        # GAP (never mid-burst, where it would stall the keystroke echo) -- the soft
        # trigger alongside the hard SAVE/PLAY/tab-leave commits. The ~1.5s default is
        # v1.1's pinned starting point, TO BE CONFIRMED BY THE STAGE-7 HARDWARE MEASUREMENT.
        self._edit_ms = None
        self._edit_debounce_ms = 1500
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
        # #68 follow-up (owner call 2026-07-08): the periodic diag->SD write is its
        # OWN gate -- PERF DIAG ON + DIAG SD OFF = serial-only measurement with no
        # 20s sdflush stutter. Crash/cart-exit flushes stay unconditional.
        self.diag_sd = False
        # Frameskip (#77): while a GAME plays, run the cart's _update + audio every
        # frame but its _draw + the composite + the flush only every SECOND frame --
        # logic/input stay at the full loop rate, pixels present at half. This
        # halves the whole render-side cost (which the #77 A/B measured to be
        # MicroPython per-draw-call dispatch -- the one tax left) at the price of
        # 30Hz motion. Settings -> FRAMESKIP toggles + persists it; default OFF
        # until the on-glass feel verdict. _fs_phase is the alternation bit.
        self.frameskip = False
        self._fs_phase = False

    def _init_overlays(self):
        """Achievements/eggs (#21), the system menu (#52), device hooks, and the
        #44 redraw gate."""
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
        # Full-canvas Paint is a system-domain app process: its 320x240/512x300
        # document stays indexed, while its chrome/view reflows to each window.
        self.artwork_app = PaintAppLayer(self, NAMES, _in)
        self.appearance_app = AppearanceAppLayer(self, NAMES, _in)
        # Writer (the kid notebook): notes list + a ruled text page over the shared
        # CodeEditor core; autosaves its notes.json through the cart store.
        self.writer_app = WriterAppLayer(self, NAMES, _in)
        # Storybook (#78): decks of art+words pages that COMPILE to story carts
        # (deck.json + a generated, readable main.py -- the blocks->code model).
        self.storybook_app = StorybookAppLayer(self, NAMES, _in)
        # Sheets (#78): the kid spreadsheet -- a workbook of grids with a hand-rolled
        # formula engine (runtime/formula.py); values reach a game via table().
        self.sheets_app = SheetsAppLayer(self, NAMES, _in)
        # Files (#108): the user-files gallery/manager over files/<kind>/ --
        # browse, rename, duplicate, trash/restore, and the copy-on-use reuse
        # verbs (wallpaper / game bg) on the kid's drawings.
        self.files_app = FilesAppLayer(self, NAMES, _in)
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
        self.register_app(self.artwork_app,
                          min_size=(PaintAppLayout.MIN_W, PaintAppLayout.MIN_H))
        self.register_app(self.appearance_app,
                          min_size=(AppearanceLayout.MIN_W, AppearanceLayout.MIN_H))
        self.register_app(self.writer_app, text_mode=True)
        self.register_app(self.storybook_app)
        self.register_app(self.sheets_app, text_mode=True)
        self.register_app(self.files_app, text_mode=True)   # rename typing
        self.register_app(CalcAppLayer(self, NAMES, _in))
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
        is open: in-window app bars suppress OS chrome, the dock is off, and
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
        """The wallpaper-type carts available as backdrops (discovery by type, Moybyte's
        equivalent of Picotron's wallpapers folder). Reads the FULL scanned list, not the
        launcher grid -- wallpapers are a backdrop category chosen in Settings, so they
        leave the launcher RUN-grid (spec shell_ux_v1.md) but stay discoverable here."""
        return [c for c in self._all_carts if c.get("type") == "wallpaper"]

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
        # Paint's shared document lives outside the re-seeded built-in cart. Restore
        # My Art's bg asset before compiling a persisted My Art wallpaper.
        self.artwork.sync_wallpaper()
        self.select_wallpaper(self.system.get("wallpaper"), persist=False)
        self.set_theme(self.system.get("theme", self.theme_name), persist=False)
        # #68: apply the persisted diagnostics gate (kid-mode default OFF).
        self.set_diag_live(self.system.get("diag_live", False), persist=False)
        self.set_diag_sd(self.system.get("diag_sd", False), persist=False)
        # #77: apply the persisted frameskip gate (default OFF).
        self.set_frameskip(self.system.get("frameskip", False), persist=False)

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

    def set_theme(self, name, persist=True):
        """Pick the panel THEME (Settings -> THEME): swap the chrome token set
        (chrome.THEMES) the panels/window chrome/selection accents read each draw,
        and persist the choice. An unknown name falls back to the default."""
        if not any(n == name for n, _t in THEMES):
            name = DEFAULT_THEME
        self.theme_name = name
        self.theme_colors = theme_colors(name)
        # The launcher grids read the accent for their selection ring/pill.
        self.launcher.theme = self.theme_colors
        if getattr(self, "picker", None) is not None:
            self.picker.theme = self.theme_colors
        # The cached bar strip now paints theme-colored pixels (the launcher zone's
        # PLAY/CHANGE chips), and its cache key doesn't fold the theme name -- bump
        # the explicit generation so a theme switch repaints it.
        if getattr(self, "bar_layer", None) is not None:
            self.bar_layer.invalidate()
        self._dirty = True
        if persist:
            self.system["theme"] = self.theme_name
            self._persist_system()

    def cycle_theme(self, d):
        """Step Settings -> THEME through chrome.THEMES (applies + persists)."""
        names = [n for n, _t in THEMES]
        cur = self.theme_name if self.theme_name in names else names[0]
        self.set_theme(names[(names.index(cur) + d) % len(names)], persist=True)

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

    def set_diag_sd(self, on, persist=True):
        """Flip the periodic diag->SD write gate (Settings -> DIAG SD LOG) and
        persist it. Separate from PERF DIAG so a measurement session can stream
        serial samples WITHOUT the ~115ms 20s sdflush stutter; the offline
        play-then-read-diag.log workflow flips this ON too. Crash/cart-exit
        flushes are unconditional either way (the safety net)."""
        self.diag_sd = bool(on)
        self._dirty = True
        if persist:
            self.system["diag_sd"] = self.diag_sd
            self._persist_system()

    def set_frameskip(self, on, persist=True):
        """Flip the #77 frameskip gate (Settings -> FRAMESKIP) and persist it.
        Takes effect on the next frame; the phase bit resets so the first frame
        after a toggle always renders (no one-frame blank on enable)."""
        self.frameskip = bool(on)
        self._fs_phase = False
        self._dirty = True
        if persist:
            self.system["frameskip"] = self.frameskip
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

    # -- favorites + recents (#105) -------------------------------------------
    #
    # Both ride the SAME system.json persistence Settings already uses for
    # theme/wallpaper/font/OTA channel (self.system + _persist_system) -- no new
    # store surface. `favorites` is a plain path list (order = the order a kid
    # starred them, oldest first); `desk_mru` (issue #105's own naming note) is a
    # capped most-recently-run path list, newest first. Cart identity is the
    # store PATH (stable across a rename/rescan, unlike an in-memory dict).

    _MRU_CAP = 8          # how many recents system.json remembers

    def is_favorite(self, cart):
        path = cart.get("path") if cart else None
        if not path:
            return False
        return path in self.system.get("favorites", [])

    def toggle_favorite(self, cart):
        """Star/unstar `cart` (the launcher card's corner badge tap) and persist.
        A no-op for a pseudo tile (no path)."""
        path = cart.get("path") if cart else None
        if not path:
            return
        favs = list(self.system.get("favorites", []))
        if path in favs:
            favs.remove(path)
        else:
            favs.append(path)
        self.system["favorites"] = favs
        self._dirty = True
        self._persist_system()

    def _note_recent(self, cart):
        """Record `cart` as most-recently-run: move its path to the front of
        system.json's `desk_mru` list (issue #105's naming), capped at
        _MRU_CAP. Called from every launcher-driven run/open (open/open_app) --
        a pseudo tile (no path) is never recorded."""
        path = cart.get("path") if cart else None
        if not path:
            return
        mru = [p for p in self.system.get("desk_mru", []) if p != path]
        mru.insert(0, path)
        self.system["desk_mru"] = mru[:self._MRU_CAP]
        self._persist_system()

    def recent_carts(self):
        """The desk_mru path list resolved back to live cart dicts (newest first),
        silently dropping any path that no longer scans (deleted/renamed since).
        Read-only convenience for a future recents surface; #105 only settled the
        system.json key, not where it renders."""
        by_path = {c.get("path"): c for c in self._all_carts if c.get("path")}
        out = []
        for path in self.system.get("desk_mru", []):
            c = by_path.get(path)
            if c is not None:
                out.append(c)
        return out

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
                # #66 live-set diet: a slimmed wallpaper cart rehydrates for the
                # compile (which bakes src/sheet into the wallpaper's own ns), then
                # re-slims -- unless it IS the open project's cart (stays fat).
                self._rehydrate_cart(cart)
                self.wallpaper.compile(cart)   # compile into the backdrop component (#28)
                if cart is not getattr(self, "_fat_cart", None):
                    self._reslim_cart(cart)
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
        if cart.get("path") is None:                # a pinned pseudo tile (Make/New):
            return None                             # no cart art -> draw its type glyph
        key = cart.get("path") or cart.get("title")
        cache = self._icon_cache
        if key in cache:
            return cache[key]
        sheet = self._build_sheet(cart)             # shared sprite-load + fallback
        img = sheet.tile_image(0, -1) if not sheet.is_blank() else None
        cache[key] = img
        return img

    def light_chrome(self):
        """True when the live theme's tool surface is LIGHT (visual identity v1
        Phase 3) -- THE gate every surface's light branch reads (ui.is_light over
        the live tokens). A method on ws so the layers inside the chrome import
        cycle need no ui import of their own."""
        return _ui_is_light(self.theme_colors)

    def _cover_for(self, cart, w, h):
        """The cart's COVER ART (visual identity v1 Section 11.4) as a blittable
        sized exactly (w, h) -- images/cover.moyimg cover-cropped (fill + center
        crop, nearest sample) -- or None when the cart carries none (the shelf
        card falls back to sprite/glyph, the deterministic pre-cover look) OR
        while its build is still in flight. Cached per (path, w, h); read
        through the store so a slimmed cart (#66) never rehydrates, and cleared
        with the icon cache on a store re-scan.

        Builds are TIME-SLICED and BUDGETED (#66, hardware-measured): decoding
        one 320x240 RLE cover in interpreted code costs 0.5-1.7s on the T-Deck,
        so a miss starts a resumable _CoverJob and each frame advances at most
        ONE job by ~_COVER_SLICE_MS. Cards draw their sprite/glyph fallback
        until their cover lands (covers pop in over frames, no frozen frames);
        frame() re-arms the redraw gate while any build is pending."""
        path = cart.get("path")
        if path is None or self.carts_store is None or w <= 0 or h <= 0:
            return None
        key = (path, w, h)
        cache = self._cover_cache
        if key in cache:
            order = self._cover_cache_order
            try:
                order.remove(key)
            except ValueError:
                pass
            order.append(key)
            return cache[key]
        if self._cover_built:              # this frame's slice is already spent
            self._covers_deferred = True
            return None
        self._cover_built = True
        t0 = _ticks_ms()
        jobs = self._cover_jobs
        job = jobs.get(key)
        if job is None:
            loader = getattr(self.carts_store, "load_image", None)
            cover_name = getattr(self.carts_store, "COVER_IMAGE", "cover")
            blob = loader(path, cover_name) if loader is not None else None
            runs = None
            if blob:
                parse = getattr(self.carts_store, "moyimg_runs", None)
                runs = parse(blob) if parse is not None else None
            if runs is None:                # no cover / not RLE -> cache the miss
                return self._cover_finish(key, None)
            # Persistent thumb sidecar (#66 "decode covers ONCE"): a crop this
            # size finished in ANY earlier session loads in one small read here
            # instead of re-running the 0.5-1.7s time-sliced decode -- so a
            # boot / re-scan / LRU-evicted shelf refills in a frame per card,
            # not seconds. Stamped against the blob, so an edited cover
            # rebuilds; store fns are optional so a bare store still works.
            sig_fn = getattr(self.carts_store, "cover_sig", None)
            sig = sig_fn(blob) if sig_fn is not None else None
            if sig is not None:
                load_thumb = getattr(self.carts_store, "load_cover_thumb", None)
                pix = (load_thumb(path, w, h, sig)
                       if load_thumb is not None else None)
                if pix is not None:
                    return self._cover_finish(key, _CoverImage(w, h, pix))
            job = _CoverJob(runs, w, h)
            job.sig = sig                   # stamps the sidecar when it lands
            jobs[key] = job
            # Bound the half-built set: a card scrolled out of view stops
            # being stepped -- drop some OTHER job (it just rebuilds if it
            # ever scrolls back into view).
            while len(jobs) > 8:
                for old in jobs:
                    if old != key:
                        jobs.pop(old)
                        break
                else:
                    break
        if not job.done:
            job.step(t0)
        if not job.done:
            self._covers_deferred = True    # keep frames coming until it lands
            return None
        jobs.pop(key, None)
        # Persist the finished crop (#66): the decode this size never runs
        # again for this cover version -- next session's shelf reads the
        # sidecar. Best-effort (save_cover_thumb never raises); _with_sd so
        # the device write rides the resident SD session like every store write.
        if (job.img is not None and getattr(job, "sig", None) is not None
                and self.can_manage):
            save_thumb = getattr(self.carts_store, "save_cover_thumb", None)
            if save_thumb is not None:
                try:
                    self._with_sd(lambda: save_thumb(path, w, h,
                                                     job.sig, job.img.pix))
                except Exception:  # noqa: BLE001 -- regenerable cache
                    pass
        return self._cover_finish(key, job.img)

    def _cover_finish(self, key, img):
        """Insert a finished cover (or a definitive miss) into the bounded
        LRU cache and return it."""
        cache = self._cover_cache
        cache[key] = img
        order = self._cover_cache_order
        order.append(key)
        if img is not None:
            self._cover_cache_pixels += len(img.pix)
        while (len(order) > _COVER_CACHE_MAX_ENTRIES
               or self._cover_cache_pixels > _COVER_CACHE_MAX_PIXELS):
            old_key = order.pop(0)
            old_img = cache.pop(old_key, None)
            if old_img is not None:
                self._cover_cache_pixels -= len(old_img.pix)
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
        fs = self._effective_font_scale()
        self.sysmenu.fs = fs          # rows hold fs-scaled text -> fs-scaled geometry
        pw = _POPUP_W * fs
        ax = bx + bw - pw
        self.sysmenu.anchor_x = max(0, min(ax, self.sys_canvas.w - pw))
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

    def run(self, project, caller):
        """Show `project`'s running cart on the desktop, recording `caller` so the exit
        gesture knows where to return (spec Section 2's run/return -- a stack discipline,
        not a blocking call, since the frame loop can't block). The cart itself is started
        by the explicit _start() at each call site (open/apply/run_code/_leave_menu);
        run() makes the desktop layer active + records the caller. The launcher home root
        is one caller (pop == go_home); the Editor is the second (Stage 3), so PLAY
        returns to the same tab -- proving the Player is caller-agnostic."""
        self._run_caller = caller
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
        _tb = _ticks_ms() if _perf else 0
        self.bar_layer._draw_status_strip("desktop")   # unified top bar (tool switcher)
        if _perf:
            self._pf_bar = _ticks_diff(_ticks_ms(), _tb)   # CHROMEBRK: the bar's share

    def _cart_bar_tap(self, px, py):
        """Route a CRASH-frame tap to the top-bar tool switcher (bar-owned), returning
        True iff a tool icon consumed it. Same isolation reason as _draw_cart_bar: the
        Player calls this instead of reaching the bar surface."""
        return self.bar_layer.handle_cart_tap(px, py)

    def frame_cap_fps(self):
        """The frame-loop cap for THIS moment (#63 frame pacing, the SNES rule:
        a LOCKED cadence feels smoother than a swing). A running GAME locks to a
        steady 30fps by default -- most carts land in the 29-45 band, and holding
        the fast frames to the slow ones' pace turns "38-55 and jittery" into
        "30 and rock solid", with the freed headroom absorbing GC/SD hitches. A
        cart that sustains more declares `"fps": 60` in its manifest (Hop Quest,
        Sky Run). Tools/apps and every console screen keep 60 -- the pointer must
        stay responsive. The device loop re-reads this every iteration; the host
        simulator paces via its own --fps flag."""
        if (FPS_GOVERNOR and self.wm.top_is_player()
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
        _tb = _ticks_ms() if _perf else 0
        self.bar_layer._draw_status_strip("tool")   # minimal bar: title + status + X
        if _perf:
            self._pf_bar = _ticks_diff(_ticks_ms(), _tb)   # CHROMEBRK: the bar's share

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

    def slim_carts(self):
        """The #66 live-set diet: after the backend wires the cart store, drop every
        SD-backed cart's heavy payloads (source/sprites/sounds/map/images/blocks)
        from the scanned list -- the launcher only needs metadata + the icon, which
        is baked into the icon cache here first. Cuts the permanently-live heap by
        ~300-500KB, which is most of a GC collect's mark cost (~0.2ms/KB on device).
        Embedded carts (no path / no store) stay fat -- they cannot be reloaded."""
        if self.carts_store is None:
            return
        for cart in self._all_carts:
            if not cart.get("path") or cart.get("lazy"):
                continue
            try:
                self._icon_sheet_for(cart)     # bake the grid icon while the art is here
            except Exception:  # noqa: BLE001 -- a bad sheet just gets the type glyph
                pass
            for k in _HEAVY_CART_KEYS:
                if k in cart:
                    del cart[k]
            cart["lazy"] = True
        try:
            import gc
            gc.collect()                       # reclaim the dropped payloads NOW
        except Exception:  # noqa: BLE001
            pass

    def _rehydrate_cart(self, cart):
        """Load a slimmed cart's full payloads back from the store IN PLACE (the
        launcher/picker hold the same dict, so every reference fattens at once).
        No-op for fat/embedded carts; a failed load leaves the cart slim and the
        caller's error handling surfaces it (missing src -> the crash panel)."""
        if not cart.get("lazy") or self.carts_store is None or not cart.get("path"):
            return cart
        try:
            full = self._with_sd(lambda: self.carts_store.load(cart["path"]))
        except Exception:  # noqa: BLE001 -- SD hiccup: stay slim, surface downstream
            full = None
        if full:
            cart.update(full)
            cart["lazy"] = False
        return cart

    def _reslim_cart(self, cart):
        # Re-slim a previously-opened cart when the workspace moves on (keeps at
        # most ~one fat cart live). Only SD-backed carts that slim_carts managed.
        if cart is None or not cart.get("path") or cart.get("lazy") is not False:
            return
        for k in _HEAVY_CART_KEYS:
            if k in cart:
                del cart[k]
        cart["lazy"] = True

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
            self._reslim_cart(prev)            # at most ~one fat cart stays live (#66)
        self._rehydrate_cart(cart)
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

    def app_min_size(self, kind):
        """The registered windowed resize minimum for app `kind`, or None."""
        return self._app_min_sizes.get(kind)

    def app_title(self, kind):
        """The registered app's requested window/taskbar title, or None."""
        return self._app_titles.get(kind)

    def open(self):
        # RUN landing (spec shell_ux_v1.md Section 2): build the workspace + run the
        # cart on the desktop, recording the launcher home as the caller so QUIT pops
        # home. Open to the desktop even if the cart failed to start: frame() shows the
        # error panel there so the kid isn't stranded (a silent stay-on-launcher would
        # be a dead end on the device). Authoring is a separate app (the Editor), reached
        # via the launcher's Make tile -> project-picker, not a tap-mode on the launcher.
        selected = self.launcher.selected()
        self._note_recent(selected)    # #105 desk_mru: every launcher-tap run counts
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
            for c in self._all_carts:
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
        """The picker's "+ New": create a fresh GAME cart (NEW_TEMPLATE), re-sync both
        grids, then open the new cart in the Editor. A no-op on a read-only store (a
        device without SD writes), leaving the picker up rather than crashing."""
        if not self.carts_root or not self.can_manage:
            return
        try:
            new, items = self._with_sd(lambda: (
                self.carts_store.new_from_template(self.carts_root),
                self.carts_store.scan(self.carts_root)))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte new cart failed:", exc)
            return
        self._apply_items(items)
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
    # runner + _icon_sheet_for call ws._build_sheet(cart), _start calls _build_audio,
    # and open() calls all four -- all through self.project now).
    def _build_sheet(self, cart=None):
        return self.project._build_sheet(cart)

    def _build_pmem(self):
        return self.project._build_pmem()

    def _build_tilemap(self, cart=None):
        return self.project._build_tilemap(cart)

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

    # -- durable undo/redo (Stage 7 of docs/shell_ux_technical_plan_v1.md) ------
    #
    # The kid-facing verbs over the moy_carts journal walk. UI TRIGGER: resolved by
    # owner decision (#88, 2026-07-18) -- shared UNDO/REDO icons in the Editor's lent
    # top-bar zone (EditorApp.draw_zone/_ZONE_TABS), reachable from every tab
    # (Code/Blocks/Sprites/Map/Scene/Music), on top of the code editor's existing
    # Ctrl+Z / Ctrl+Y host shortcut (code_layer.py) -- both drive the SAME
    # ws.undo()/ws.redo() mechanism, so neither affordance can drift from the other.

    def undo(self):
        """Undo the last durable commit: restore the previous snapshot over the live
        file, then rebuild the affected editor so the revert is visible. Returns True
        iff a step was taken (False at the floor -- finer, in-session undo is the
        editor's RAM). A DURABLE step = one commit, not one keystroke (spec Section 7)."""
        return self._journal_walk(False)

    def redo(self):
        """Re-apply the next durable commit (the inverse of undo). Returns True iff a
        step was taken (False at the top)."""
        return self._journal_walk(True)

    def can_undo(self):
        """Read-only: True iff undo() would actually restore something (#88, the bar
        icon's dimmed state). Cheap -- a journal.jsonl parse, no live-file write --
        but still an SD read, so callers should only ask when they're about to
        REPAINT (the zoned-bar cache key), never on a per-frame hot path."""
        return self._journal_check(False)

    def can_redo(self):
        """Read-only counterpart to can_undo() for redo()."""
        return self._journal_check(True)

    def _journal_check(self, redo):
        store = self.carts_store
        if store is None or not self.cart:
            return False
        path = self.cart.get("path")
        name = "journal_can_redo" if redo else "journal_can_undo"
        if not (path and self.can_manage and hasattr(store, name)):
            return False
        fn = getattr(store, name)
        try:
            return bool(self._with_sd(lambda: fn(path)))
        except Exception as exc:  # noqa: BLE001 -- a check failure must never crash the shell
            print("Moybyte journal check failed:", _err_text(exc))
            return False

    def _journal_walk(self, redo):
        store = self.carts_store
        if store is None or not self.cart:
            return False
        path = self.cart.get("path")
        if not (path and self.can_manage and hasattr(store, "journal_undo")):
            return False
        fn = store.journal_redo if redo else store.journal_undo
        try:
            changed = self._with_sd(lambda: fn(path))
        except Exception as exc:  # noqa: BLE001 -- a walk failure must never crash the shell
            print("Moybyte journal walk failed:", _err_text(exc))
            return False
        if not changed:
            return False           # at a floor/ceiling -- nothing to restore
        self._reload_after_walk(changed)
        self._dirty = True
        # The walk just moved the journal cursor, flipping can_undo()/can_redo() --
        # invalidate so the bar's UNDO/REDO icons re-check + repaint their dimmed
        # state on the NEXT frame (#88) instead of showing a stale enabled/disabled
        # look until some unrelated zone_gen bump happens to force a re-render.
        self.bar_layer.invalidate()
        return True

    def _reload_after_walk(self, file):
        """After the journal rewrote a live cart file on SD (undo/redo), re-adopt the
        fresh data into the OPEN workspace and rebuild the affected editor so the kid
        SEES the revert. Reloads the whole cart (uniform across file types) but keeps
        the current tab; re-_start()s so a running preview reflects the restored code.
        `file` is which live file the walk touched (informational -- the reload is
        wholesale)."""
        store = self.carts_store
        path = self.cart["path"]
        try:
            fresh = self._with_sd(lambda: store.load(path))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte reload after undo failed:", _err_text(exc))
            return
        if not fresh:
            return
        self.cart = fresh
        self.config = dict(fresh.get("cfg", {}))
        self.sheet = self._build_sheet()
        self.tilemap = self._build_tilemap()
        self.images = fresh.get("images") or {}
        self.tables = fresh.get("tables") or {}
        self.texts = fresh.get("texts") or {}
        self.scenes = self._build_scenes()   # a scene undo must reach the live rows (#85)
        self.cart_error = None
        self.crash_line = None
        # Drop the editor cores + rebuild the ACTIVE tab's over the fresh data, then
        # re-run so a running cart / a subsequent PLAY uses the restored source/art.
        self.editor = None
        self.paint = None
        self.map_ui.reset()
        self.scene_ui.reset()
        self.music_ui.reset()
        self.block_ui.reset()
        view = self.menu_view
        if self.wm.top_is("menu") and view in ("code", "paint", "map", "scene",
                                               "blocks", "music"):
            self.set_menu_view(view)     # rebuild the active editor from fresh data
        self._start()

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
            self.paint_status = "FILE FAILED"
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
        _writer = getattr(self, "writer_app", None)
        if _writer is not None:
            _writer.flush()            # never lose typed notes on ANY home pop
        _story = getattr(self, "storybook_app", None)
        if _story is not None:
            _story._commit_deck()      # same rule for an open story (clean = no-op)
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
            self._reslim_cart(_fat)
            self._fat_cart = None
        self.project = Project(self)
        self.cart_error = None
        self.save_status = None
        self.show_achievements = False
        self.ach_ui._konami_pos = 0          # fresh Konami run on the home desktop (#21)
        self.ach_ui._clock_taps = 0

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
        out = [make_tile()]
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
        place a rescan (_apply_items) and a query edit (set_search_query) both
        call, so the two can never desync on which list is "current"."""
        base = self._launcher_items(self._all_carts)
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
        game + open it), then EVERY editable cart (all types, wallpapers + built-ins
        included -- everything is editable)."""
        return [new_tile()] + list(carts)

    def _apply_items(self, items):
        # Re-sync BOTH display grids from a fresh scan (the single source `_all_carts`),
        # re-deriving the pinned pseudo tiles so a create/dup/delete lands in both.
        if items:
            self._all_carts = list(items)
            self.slim_carts()              # #66: a rescan reloads FULL carts -- re-slim
            self._cover_cache = {}         # a re-scan may carry new/changed cover art
            self._cover_cache_order = []
            self._cover_cache_pixels = 0
            self._cover_jobs = {}          # in-flight builds may read stale blobs
            self.launcher.set_items(self._launcher_view_items())   # #105: keep an active filter
            self.picker.set_items(self._picker_items(items))

    def _real_selected(self, grid):
        """The selected cart on `grid`, or None if it's a pinned pseudo tile (Make/New)
        -- so cart management (dup/del) never acts on a non-cart."""
        sel = grid.selected()
        return sel if (sel and sel.get("path")) else None

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
        # DUP now fires from the Editor picker's zone (docs/shell_ux_v1.md: the picker
        # manages projects, the launcher only plays) -- so it acts on the PICKER's
        # selection, not the launcher's.
        sel = self._real_selected(self.picker)
        if not self.carts_root or not self.can_manage or sel is None:
            return
        self._rehydrate_cart(sel)   # #66: duplicate() copies src/cfg FROM the dict
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.duplicate(sel, self.carts_root),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte duplicate failed:", exc)

    def del_cart(self):
        # Delete the OPEN cart when one is open (the sysmenu DELETE CART -- a cart opened
        # from the picker is NOT necessarily the picker's current selection), else the
        # picker's selection (the picker-zone DEL, docs/shell_ux_v1.md -- moved off the
        # launcher, which no longer has a management cluster at all). Keep at least one
        # real cart on the device.
        target = self.cart if (self.cart is not None and self.cart.get("path")) \
            else self._real_selected(self.picker)
        if not self.carts_root or not self.can_manage or target is None \
                or len(self._all_carts) <= 1:
            return
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.delete(target),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte delete failed:", exc)

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
            elif f["type"] == "choice":
                ch = f["choices"]
                idx = ch.index(cur) if cur in ch else 0
                self.config[key] = ch[(idx + d) % len(ch)]
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
        # Router (docs/shell_layers_refactor_v1.md §3): walk the z-ordered layer stack
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
            self._edit_ms = _ticks_ms()
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
        # Windowed WM (#73): while a window OTHER than the playtest holds input
        # focus, the game-space pointer publishes with click/down stripped, so a
        # background running cart never eats the taps meant for the editor beside
        # it. The fullscreen-stack WM has no hook -> unchanged.
        _pp = getattr(self.wm, "player_has_pointer", None)
        _live = _pp() if _pp is not None else True
        self.input.game_pointer = (gx, gy, click and _live, p.down and _live)
        # Memoized, pre-reversed visible stack (Stage 6c) -- no per-frame allocation.
        for layer in self.wm.visible_stack_rev():
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

    def _game_xy(self, px, py):
        return self.wm.game_xy(px, py)

    def _composite_game(self):
        return self.wm.composite_game()

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
        if kind == "appearance" and self.wallpaper.is_animating(dt):
            return True
        # A firmware install (#53) advances a chunk per frame; "done" runs a short
        # reboot countdown; "checking"/"downloading" (Phase 3) step the online flow.
        # All must keep redrawing so progress animates and the work proceeds without input.
        if kind == "update" and self.update_ui._upd_phase in (
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

    # (_draw_menu_backdrop -- the frozen-cart backdrop under the cards/paint/map
    # panels -- was removed by the #39 step-3 conversions: every Editor tab is
    # system-domain now and always fully covered the backdrop anyway, so the tabs
    # just reset the game canvas's draw state and paint their own opaque body.)

    def _autosave_code(self):
        """The idle-debounce autosave-COMMIT (Stage 7): persist + journal the code
        editor's buffer once the kid has stopped typing, WITHOUT the SAVE UI (save is
        invisible, spec Section 7). Only commits parseable source -- a mid-edit syntax
        error just waits (no nag) -- and only a real, writable edit. commit_code does
        the persist + the durable journal append + clears editor.dirty."""
        ed = self.editor
        if ed is None or not getattr(ed, "dirty", False):
            return
        if (self.carts_store is None or not self.cart
                or not self.cart.get("path") or not self.can_manage):
            ed.dirty = False              # nothing persistable (embedded/non-SD) -> disarm
            return
        src = ed.text()
        ok, _msg = self.carts_store.compile_check(src)
        if not ok:
            return                        # don't autosave/journal un-parseable source
        prev_status = self.save_status
        self.project.commit_code(src, quiet=True)   # persists + journals; clears ed.dirty
        self.save_status = prev_status    # keep the autosave invisible (spec Section 7):
                                          # save_status restored + quiet=True suppresses the
                                          # "Code Wizard" achievement toast (Stage 7 F2)

    def _journal_idle_tick(self):
        """Fire the idle-typing autosave-commit once the code editor has sat quiet for
        _edit_debounce_ms (Stage 7 soft trigger). Called every frame BEFORE the redraw
        gate so it runs even while a static editor screen is skipping its redraw -- the
        exact idle moment the between-frames SD write should land. Cheap: one early-out
        on the common no-pending-edit path."""
        if self._edit_ms is None:
            return
        ed = self.editor
        if ed is None or not getattr(ed, "dirty", False):
            self._edit_ms = None          # the edit was saved/cleared elsewhere -> disarm
            return
        if _ticks_diff(_ticks_ms(), self._edit_ms) < self._edit_debounce_ms:
            return                        # not idle long enough -- the kid is still typing
        self._edit_ms = None
        self._autosave_code()

    def frame(self, dt):
        if dt > 0:
            # EMA so the readout reflects sustained rate, not single-frame jitter.
            self._fps = _ema(self._fps, 1.0 / dt)
        self._cover_built = False     # reset the per-frame cover-build budget
        self._pf_home = None          # home-frame split (launcher_layer, perf_capture)
        # Undo journal (Stage 7): the idle-typing autosave debounce runs BEFORE the
        # redraw gate below, so it fires even on a static (redraw-skipped) editor frame.
        self._journal_idle_tick()
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
        if (self.frameskip and self.wm.top_is_player()
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
            return
        # Perf HUD (#43/#44): mark the start of this frame's draw work. Cheap (one
        # ticks call); only meaningful for a frame we actually paint, so it's after
        # the redraw gate. _flush_ms is filled around comp.flush() below; _draw_ms
        # is the rest (total span - flush). Both EMA-smoothed at frame end. Also
        # fires when perf_capture is set (device diag sampling) -- not just the HUD.
        _perf = self.perf_hud or self.perf_capture
        _frame_t0 = _ticks_ms() if _perf else 0
        _cmp = 0            # CHROMEBRK: _composite_game ms
        _cur = 0            # CHROMEBRK: _draw_cursor ms
        if _perf:
            _bc = getattr(self.canvas, "batch_reset", None)
            if _bc is not None:
                _bc()                  # #63: zero this frame's auto-batch profiling counters
            # Per-frame perf scratch (the running-cart content Layer fills self._pf_*).
            # #75: zeroed ONLY under _perf -- the writers (Player.tick / the bar draws)
            # only fill them under _perf too, and the reads below are _perf-gated, so a
            # kid-mode play frame skips the four attribute stores entirely.
            self._pf_upd = 0    # cart _update(dt) ms (game LOGIC); 0 off the cart path
            self._pf_cart = 0   # cart _draw() ms (RENDERING)
            self._pf_audio = 0  # audio.tick(dt) ms (mixer feed) -- split out from render
            self._pf_bar = 0    # CHROMEBRK: _draw_status_strip ms (cart path only)
        # Compositor / router (docs/shell_layers_refactor_v1.md §3): draw the z-ordered
        # visible stack bottom -> top. The active content draws first (game-domain
        # content on the fixed 320x240 game canvas); at the game->system domain boundary
        # the router composites that viewport into the system canvas ONCE (#39; the
        # launcher/settings + responsive code/blocks content are system-domain and skip
        # it); the chrome/overlays + cursor then draw on top on the system canvas. The
        # cursor is always the top system layer, so a game-domain content is always
        # composited before it -- reproducing the pre-refactor single composite step.
        _prev_domain = None
        # WM-surface mark (Stage 9, docs/shell_ux_technical_plan_v1.md): when a RECORDING system
        # canvas is installed (the opt-in web view), tag each WM-stack surface so the recorder
        # slices the frame into ONE stream per surface (bar / app-content / player-viewport) --
        # the browser then composites them (a second WM backend). `begin_surface` exists only on
        # the recording canvas, so on the RAW canvas (the default) `_surf` is None: no call, no
        # allocation, byte-identical pixels -- the golden set can't move. Probed ONCE per frame.
        _surf = getattr(self.sys_canvas, "begin_surface", None)
        for layer in self.wm.draw_stack():          # memoized (Stage 6c) -- no per-frame alloc
            if _prev_domain == "game" and layer.domain == "system":
                _tc = _ticks_ms() if _perf else 0
                self._composite_game()
                if _perf:
                    _cmp = _ticks_diff(_ticks_ms(), _tc)   # CHROMEBRK: viewport composite
            if _surf is not None:
                _surf(layer.id, layer.domain)       # start this surface's command stream
            if layer.id == "cursor":
                _tk = _ticks_ms() if _perf else 0
                layer.draw(dt)
                if _perf:
                    _cur = _ticks_diff(_ticks_ms(), _tk)   # CHROMEBRK: cursor
            else:
                layer.draw(dt)
            _prev_domain = layer.domain
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
        if self._covers_deferred:
            # A shelf cover build was pushed past this frame's budget -- stay
            # dirty so the remaining covers land on the following frames (the
            # flag is set during the draw, AFTER the gate consumed _dirty).
            self._covers_deferred = False
            self._dirty = True
        self._last_ptr = self._ptr_state()
        self._frames_drawn += 1

    def _frame_perf_end(self, frame_t0, cmp_ms, cur_ms):
        """The #43/#44 perf-capture frame tail (extracted from frame() so the hot
        router stays readable): time the panel DMA flush in isolation, back out
        the draw span, and EMA the DRAWBRK/CHROMEBRK splits. Only called when
        perf_hud/perf_capture is on -- the kid-mode path flushes directly, so the
        render path itself is unchanged. The timing fields stay on the
        Workstation (the device diag contract -- perf_sample/perf_breakdown/
        perf_chrome read them)."""
        _upd = self._pf_upd
        _cart = self._pf_cart
        _audio = self._pf_audio
        _bar = self._pf_bar
        _flush_t0 = _ticks_ms()
        self.comp.flush()
        _flush = _ticks_diff(_ticks_ms(), _flush_t0)
        _total = _ticks_diff(_ticks_ms(), frame_t0)
        _draw = _total - _flush
        if _draw < 0:
            _draw = 0
        self._flush_ms = _ema(self._flush_ms, _flush)
        self._draw_ms = _ema(self._draw_ms, _draw)
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
        self._upd_ms = _ema(self._upd_ms, _upd)
        self._cart_ms = _ema(self._cart_ms, _cart)
        self._audio_ms = _ema(self._audio_ms, _audio)
        self._chrome_ms = _ema(self._chrome_ms, _chrome)
        # CHROMEBRK sub-split (#66 lever 5): bar / composite / cursor EMAs, so
        # a chrome trim targets the real cost instead of guessing.
        self._bar_ms = _ema(self._bar_ms, _bar)
        self._cmp_ms = _ema(self._cmp_ms, cmp_ms)
        self._cur_ms = _ema(self._cur_ms, cur_ms)

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

    # (The #71 pause chrome -- _draw_pause_dim / _draw_pause_buttons -- was retired in
    # Stage 5; the Player now exits on hold-BACKSPACE, with a transient
    # hold-progress toast in its place. See player.py.)

    def _mini_btn(self, label, rect, fill, cv=None):
        # Shared draw toolkit -- the implementation moved to ui.mini_btn (the
        # v0.5 kernel-shrink direction); this stays the tested ws entry point.
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
            self.sys_canvas.spr(CURSOR, self.pointer.x, self.pointer.y, self.font_scale)

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

    def _icon_image_keyed(self, kind):
        """Like _bar_image but with palette index 0 keyed TRANSPARENT -- for drawing
        a bar icon over a non-black surface (the Library shelf's pseudo cards). The
        sheet's untouched pixels are 0: invisible on the black bar, a black plate
        anywhere else. tile_image memoises per (slot, transparent), so this shares
        the sheet's own cache."""
        if self.icon_sheet is None:
            return None
        slot = _ICON.get(kind)
        if slot is None:
            return None
        return self.icon_sheet.tile_image(slot, transparent=0)

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


def wire_workstation_core(ws, store, carts_root, make_api, wifi,
                          make_audio=None, lua_runtime=None, can_manage=None,
                          before_slim=None, pointer=None, inp=None,
                          keyboard=None):
    """The board-agnostic Workstation service wiring, in the ONE canonical order
    every backend used to hand-copy (host build_workstation + both boards'
    run_desktop -- the P4 literally carried a "same order as host" comment).
    The caller builds its board-specific backends (api/audio/wifi/lua/SD/OTA)
    and hands them in; board glue that must land between the store hookup and
    the #66 slim_carts diet (the T-Deck's _with_sd + OTA updater) goes through
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
    ws.slim_carts()   # #66 live-set diet: heavy payloads reload from the store
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
    ws.load_icon_sheet()            # Stage 1: the 16x16 bar IconSheet (theme or baked)
    ws.load_achievements()          # #21: unlocked badges survive reboots
