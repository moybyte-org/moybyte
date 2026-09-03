-- sheet -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/sheet.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- sset (SPEC.md 7.1): a sheet write is what spr, sspr and map draw
-- next; the index is masked to 0-15; writes off the sheet are
-- dropped.

function _draw()
  cls(1)
  sset(16, 0, 7)
  sset(8, 8, 0)
  sset(17, 1, 7)
  sset(9, 9, 0)
  sset(18, 2, 7)
  sset(10, 10, 0)
  sset(19, 3, 7)
  sset(11, 11, 0)
  sset(20, 4, 7)
  sset(12, 12, 0)
  sset(21, 5, 7)
  sset(13, 13, 0)
  sset(22, 6, 7)
  sset(14, 14, 0)
  sset(23, 7, 7)
  sset(15, 15, 0)
  sset(16, 7, 12)
  sset(0, 0, 0)
  sset(8, 0, 8)
  spr(2, 8, 8, -1, 4, 0)
  sset(16, 0, 8)
  sset(17, 1, 8)
  sset(18, 2, 8)
  sset(19, 3, 8)
  sset(20, 4, 8)
  sset(21, 5, 8)
  sset(22, 6, 8)
  sset(23, 7, 8)
  spr(2, 48, 8, -1, 4, 0)
  sset(16, 7, 24)
  sset(-1, 0, 8)
  sset(0, 999, 8)
  sset(128, 0, 8)
  spr(2, 88, 8, -1, 4, 0)
  sset(8, 8, 9)
  sset(9, 9, 9)
  sset(10, 10, 9)
  sset(11, 11, 9)
  sset(12, 12, 9)
  sset(13, 13, 9)
  sset(14, 14, 9)
  sset(15, 15, 9)
  spr(17, 128, 8, -1, 4, 0)
  sset(0, 0, 11)
  spr(0, 168, 8, -1, 4, 0)
  sspr(16, 0, 8, 8, 208, 8, 32, 32, -1, 0)
  map(3, 0, 2, 1, 8, 60, -1, 1)
  sset(8, 0, 12)
  map(3, 0, 2, 1, 8, 72, -1, 1)
end
