"""Backend-agnostic editor cores shared by the host and device consoles.

The UMBRELLA module: the cores live in per-editor modules (split 2026-07-19 --
each was already independent, sharing only the editors_base history discipline)
and are re-imported + re-exported here under their original names, so every
consumer and test keeps `from editors import X` / `editors.X`:

  editors_base      -- KeyEdge (the input leaf; its pre-#111 UndoStack/
                       UndoRedoMixin pair was deleted 2026-08-18, unused)
  editors_code      -- CodeEditor (editable text buffer + cursor, #3)
  editors_sheet     -- _SheetSprite / SpriteSheet / IconSheet / TileMap
  editors_paint_map -- PaintEditor (#4) + MapEditor (#32)
  editors_block     -- BlockRow / BlockEditor (+ _clone_tree, #29 Part 2)
  editors_music     -- MusicEditor (#50/#92)
  editors_scene     -- SceneEditor (placed-actor placement, #85 Stage 2)

All of them are pure logic -- no canvas, framebuf, input, or I/O -- so the
*same* files back both the host reference (`runtime/`) and the MicroPython
device port (the build stages each into the firmware `modules/` tree; keep
every module dependency-free so the set freezes cleanly on both)."""

try:
    from editors_base import KeyEdge
    from editors_code import CodeEditor
    from editors_sheet import _SheetSprite, SpriteSheet, IconSheet, TileMap
    from editors_paint_map import PaintEditor, MapEditor
    from editors_block import _BLK_UNDO_MAX, _clone_tree, BlockRow, BlockEditor
    from editors_music import (_ME_REST, _ME_PITCH_MIN, _ME_PITCH_MAX,
                               _ME_WAVE_MIN, _ME_WAVE_MAX, _ME_VOL_MIN,
                               _ME_VOL_MAX, _ME_SPEED_MIN, _ME_SPEED_MAX,
                               _ME_STEPS_MAX, _ME_PATTERN_MAX, _ME_BANK_MAX,
                               _ME_UNDO_MAX, _me_clamp, MusicEditor)
    from editors_scene import SceneEditor
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_base import KeyEdge
    from runtime.editors_code import CodeEditor
    from runtime.editors_sheet import _SheetSprite, SpriteSheet, IconSheet, TileMap
    from runtime.editors_paint_map import PaintEditor, MapEditor
    from runtime.editors_block import (_BLK_UNDO_MAX, _clone_tree, BlockRow,
                                       BlockEditor)
    from runtime.editors_music import (_ME_REST, _ME_PITCH_MIN, _ME_PITCH_MAX,
                                       _ME_WAVE_MIN, _ME_WAVE_MAX, _ME_VOL_MIN,
                                       _ME_VOL_MAX, _ME_SPEED_MIN, _ME_SPEED_MAX,
                                       _ME_STEPS_MAX, _ME_PATTERN_MAX,
                                       _ME_BANK_MAX, _ME_UNDO_MAX, _me_clamp,
                                       MusicEditor)
    from runtime.editors_scene import SceneEditor
