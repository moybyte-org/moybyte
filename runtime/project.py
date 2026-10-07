# Map (grep -n a name to jump there):
#   _ConfigOps               the CONFIG tab's undo op codec
#   Project                  the open cart (ProjectStore) plus the Editor's op histories
#   Project.history_for      the op history for a tab
"""The open cart's live WORKSPACE (Stage 1 of docs/history/shell_ux_technical_plan_v1.md).

`Project` is the one object a tab edits and the one object the Player runs; it
is NOT a copy -- the editors and a re-run share the same live sheet/tilemap/bank
(edits reach a running cart via `gen` bumps). Its data, builders and commit_*
verbs are `ProjectStore` (runtime/project_store.py), the store's half, which
crosses with the store (docs/native_kernel_2026-09.md, sprint 1b). What this
file adds is the Editor's half: the per-tab op-history registry (`history_for`,
`_HISTORY_TABS`), the CONFIG tab's undo codec (`_ConfigOps`) and its History --
the hooks the commits drain into their journal lines.

The six live-data fields are exposed back on Workstation as forwarding
properties, so every surface file + every test reaches them unmodified.
Canonical home is runtime/; every board stages it.
"""

try:
    from project_store import ProjectStore
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.project_store import ProjectStore
# The #111 op-history core: CONFIG's fine-grained undo lives directly on Project
# (there is no separate ConfigEditor class the way paint/map/scene/music have one --
# see _ConfigOps below).
try:
    from op_history import History, OpCodec
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.op_history import History, OpCodec


class _ConfigOps(OpCodec):
    """OpCodec for the CONFIG tab (#111 phase 4): an op is `{"k":key,"o":old,
    "n":new}` -- one field's old/new value, a single-cell codec shape
    (invert is O(1): write `o`/`n` straight back). The doc is the Project
    itself (config lives directly in `doc.config`, a plain dict -- there is no
    separate ConfigEditor instance the way paint/map/scene/music each have
    one, so no editor-identity guard is needed; `Project.reset_config_history`
    re-baselines whenever the live dict is replaced wholesale)."""

    def apply(self, doc, op):
        doc.config[op["k"]] = op["n"]

    def invert(self, doc, op):
        doc.config[op["k"]] = op["o"]


class Project(ProjectStore):
    """The open cart's data and persistence verbs (`ProjectStore`) plus the
    Editor's op histories: one History per tab, resolved by `history_for`."""

    def __init__(self, ws):
        ProjectStore.__init__(self, ws)
        self.config_hist = History(self, _ConfigOps())  # #111: CONFIG tab op-history

    # -- CONFIG tab op-history (#111 phase 4) ---------------------------------

    def reset_config_history(self):
        """Fresh #111 op-history for `config`: called whenever the live dict is
        replaced WHOLESALE (Project.__init__ via a fresh workspace open, and a
        journal walk's HistoryRouter._reload_after_walk) so a stale field op from a
        superseded config can never be replayed against the new one -- the same
        "clean boundary" reset paint/map/scene/music get from dropping their
        whole editor instance on a reload."""
        self.config_hist = History(self, _ConfigOps())

    def record_config(self, key, old, new):
        """Record one field's old/new value (#111): called by every config
        mutation point (Workstation.adjust's left/right stepper, the CardsLayer
        choice-cell tap) AFTER the field is already written, mirroring the
        paint/map/scene record() contract. A same-value set records
        nothing."""
        if old != new:
            self.config_hist.record({"k": key, "o": old, "n": new})

    def _paint_history(self):
        """The op-history of the OPEN cart-sprite PaintEditor (#111), or None. Guarded
        so the theme/icon editor (whose PaintEditor rides a DIFFERENT sheet) can never
        have its ops folded into a cart's journal -- only the editor bound to THIS
        project's live sheet counts."""
        pe = getattr(self.ws, "paint", None)
        if pe is not None and getattr(pe, "sheet", None) is self.sheet:
            return getattr(pe, "_hist", None)
        return None

    def _map_history(self):
        """The op-history of the OPEN MapEditor (#111), or None -- guarded on the live
        tilemap identity like _paint_history."""
        me = getattr(self.ws.map_ui, "mapedit", None)
        if me is not None and getattr(me, "tilemap", None) is self.tilemap:
            return getattr(me, "_hist", None)
        return None

    def _code_history(self):
        """The op-history of the OPEN code editor (#111 phase 4), or None -- created
        lazily on the UNDO ROUTER over the live CodeEditor and rebound when a fresh
        editor is built (ws.history.code_op_history). The code burst + this History
        live there, with the routing that consumes them; Project just references it
        (and commit_code drains it, mirroring commit_sprites/commit_map)."""
        return self.ws.history.code_op_history()

    def _blocks_history(self):
        """The op-history of the OPEN BlockEditor (#111 phase 4), or None. A
        GRADUATED cart's Blocks tab is a FROZEN, read-only render (spec Section 8),
        so its History is deliberately ABSENT there -- the bar UNDO then falls
        straight to the durable journal walk, which un-graduates when it crosses the
        graduating commit (moy_carts). In-session only: blocks saves don't journal
        ops, so unlike paint/map this History never flushes into a commit."""
        ui = getattr(self.ws, "block_ui", None)
        if ui is None or getattr(ui, "blk_graduated", False):
            return None
        be = getattr(ui, "blocks_ed", None)
        return getattr(be, "_hist", None) if be is not None else None

    # #111 phase 4: the per-tab op-history REGISTRY -- the active Editor tab's
    # menu_view maps to the Project method returning that tab's live History (or
    # None for a tab with no in-RAM op stack). One entry per surface, ADDITIVE:
    # paint/map (#111 phase 2) + code/blocks (phase 4) here; scene/music/config
    # wire theirs in the same shape. HistoryRouter.active_history reads it -- keeping
    # this a data table (not a switch ladder in console) is why new tabs merge
    # cleanly (one line each) instead of colliding in one growing if/elif.
    _HISTORY_TABS = {
        "paint": "_paint_history",
        "map": "_map_history",
        "code": "_code_history",
        "blocks": "_blocks_history",
        "scene": "_scene_history",
        "music": "_music_history",
        "cards": "_config_history",
    }

    def history_for(self, view):
        """The live op-history for Editor tab `view` (a menu_view key), or None for
        a tab with no in-RAM op stack. Keyed via _HISTORY_TABS so a stale editor
        from another tab is never consulted -- the caller (the bar UNDO/REDO icons,
        only reachable inside the Editor) guarantees the Editor is focused."""
        name = self._HISTORY_TABS.get(view)
        if name is None:
            return None
        return getattr(self, name)()

    def _config_history(self):
        """The op-history of this project's config cards (#111 phase 4) -- lives
        directly on the Project (there is no separate ConfigEditor instance);
        reset_config_history() re-baselines it whenever self.config is replaced
        wholesale (fresh open, journal-walk reload)."""
        return getattr(self, "config_hist", None)

    def _scene_history(self):
        """The op-history of the OPEN SceneEditor (#111 phase 4), or None. Unlike
        paint/map there is only ever one live SceneEditor per open project (no
        theme-editor-style alias rides the same class on different data), so no
        identity guard beyond "an editor is actually open" is needed."""
        se = getattr(self.ws.scene_ui, "sceneedit", None)
        return getattr(se, "_hist", None) if se is not None else None

    def _music_history(self):
        """The op-history of the OPEN MusicEditor (#111 phase 4), or None --
        guarded on the live AudioBank identity like _paint_history (the bank a
        MusicEditor edits is `ws.audio.bank`; commit_sounds only wants
        the ops for THIS project's bank)."""
        me = getattr(self.ws.music_ui, "musicedit", None)
        au = getattr(self.ws, "audio", None)
        if me is not None and au is not None and getattr(me, "bank", None) is au.bank:
            return getattr(me, "_hist", None)
        return None
