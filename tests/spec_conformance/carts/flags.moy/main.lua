-- flags -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/flags.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- Tile flags (SPEC.md 3.5, 7.1, 7.2): map(..., layers) draws only
-- the cells whose tile shares a flag bit with the mask; fset
-- changes that on the next map(); a mask against no flags draws
-- nothing. The scene restores what it edits first.

function _draw()
  fset(3, 0)
  fset(2, 1, true)
  fset(0, 0)
  cls(1)
  map(0, 0, 10, 6, 0, 0, -1, 1)
  map(0, 0, 10, 6, 100, 0, -1, 1, 1)
  map(0, 0, 10, 6, 200, 0, -1, 1, 2)
  map(0, 0, 10, 6, 0, 60, -1, 1, 128)
  map(0, 0, 10, 6, 100, 60, -1, 1, 64)
  map(0, 0, 10, 6, 200, 60, -1, 1, 129)
  fset(3, 2)
  map(0, 0, 10, 6, 0, 120, -1, 1, 2)
  fset(2, 1, false)
  map(0, 0, 10, 6, 100, 120, -1, 1, 2)
  fset(0, 1)
  map(0, 0, 10, 6, 200, 120, -1, 1, 1)
  map(0, 0, 10, 6, 0, 180, -1, 2, 1)
  camera(-200, -180)
  map(0, 0, 10, 6, 0, 0, -1, 1, 128)
  camera()
end
