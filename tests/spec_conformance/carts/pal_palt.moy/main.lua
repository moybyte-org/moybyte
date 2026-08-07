-- pal_palt -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/pal_palt.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- Draw-time remap and sprite transparency, including the case
-- where both are active. pal must not touch pixels already drawn.

function _draw()
  cls(0)
  rect(8, 8, 40, 40, 8)
  pal(8, 11)
  rect(56, 8, 40, 40, 8)
  print("REMAP", 8, 52, 8)
  circ(120, 28, 20, 8)
  spr(1, 150, 8, -1, 1, 0)
  pal()
  rect(180, 8, 40, 40, 8)
  pal(11, 14)
  rect(230, 8, 40, 40, 11)
  pal()
  rect(0, 70, 320, 60, 3)
  spr(4, 16, 80, -1, 4, 0)
  palt(0, true)
  spr(4, 80, 80, -1, 4, 0)
  palt()
  spr(4, 144, 80, 0, 4, 0)
  palt(11, true)
  spr(4, 208, 80, -1, 4, 0)
  palt()
  pal(11, 12)
  palt(0, true)
  spr(4, 272, 80, -1, 4, 0)
  pal()
  palt()
end
