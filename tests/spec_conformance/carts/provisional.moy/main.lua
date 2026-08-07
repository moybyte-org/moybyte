-- provisional -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/provisional.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- SPEC.md 6.1 verbs. NOT part of conformance -- SPEC.md 11
-- excludes 6.1 until its promotion gates clear. Kept so the
-- golden already exists when they do.

function _draw()
  cls(1)
  tri(20, 20, 100, 40, 60, 100, 8)
  trib(20, 20, 100, 40, 60, 100, 7)
  tri(120, 20, 200, 20, 160, 90, 11)
  tri(120, 100, 200, 100, 160, 30, 12)
  tri(220, 20, 300, 20, 260, 20, 14)
  sspr(0, 8, 8, 8, 20, 130, 40, 40, -1, 0)
  sspr(16, 8, 8, 8, 70, 130, 60, 30, -1, 0)
  sspr(24, 8, 8, 8, 140, 130, 40, 40, -1, 1)
  sspr(24, 8, 8, 8, 190, 130, 40, 40, -1, 2)
  sspr(8, 8, 16, 8, 240, 130, 8, 40, -1, 0)
  sspr(0, 8, 8, 8, 20, 190, 0, 40, -1, 0)
end
