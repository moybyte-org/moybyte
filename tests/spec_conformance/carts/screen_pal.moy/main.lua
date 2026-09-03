-- screen_pal -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/screen_pal.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- The screen palette (SPEC.md 6, 12.1): pal(c0, c1, 1) composes
-- after the draw palette for pixels drawn from then on, and a
-- pixel already on the canvas does not move; pal() resets both.

function _draw()
  cls(1)
  rect(8, 8, 40, 40, 8)
  rect(56, 8, 40, 40, 9)
  pal(4, 15, 1)
  pal(2, 8)
  pal()
  rect(104, 8, 40, 40, 4)
  rect(152, 8, 40, 40, 2)
  pal(8, 11, 1)
  rect(200, 8, 40, 40, 8)
  pal(9, 10)
  pal(10, 12, 1)
  rect(248, 8, 40, 40, 9)
  print("SHOWN", 8, 56, 8)
  spr(1, 248, 60, -1, 4, 0)
  rect(8, 100, 40, 40, 7)
  pal(7, 15, 1)
  pal(15, 7, 1)
  rect(56, 100, 40, 40, 15)
  rect(104, 100, 40, 40, 7)
  pal(11, 3)
  pal(3, 5, 1)
  rect(152, 100, 40, 40, 11)
  rect(200, 100, 40, 40, 3)
  pal(12, 12, 1)
  rect(248, 100, 40, 40, 12)
  pal(1, 2, 1)
  rect(8, 160, 300, 30, 1)
end
