-- primitives -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/primitives.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- Every core drawing verb (SPEC.md 6) at a size where the
-- rasterization is visible, plus the degenerate cases: a 1x1
-- rect, r=0 and r=1 circles, zero-size rects.

function _draw()
  cls(1)
  rect(8, 8, 60, 40, 3)
  rectb(8, 8, 60, 40, 11)
  circ(120, 30, 20, 8)
  circb(120, 30, 20, 10)
  line(160, 8, 260, 48, 7)
  line(160, 48, 260, 8, 12)
  line(280, 8, 280, 48, 14)
  line(160, 60, 300, 60, 15)
  pix(300, 70, 7)
  pix(301, 70, 7)
  pix(302, 70, 7)
  pix(303, 70, 7)
  pix(300, 71, 7)
  pix(301, 71, 7)
  pix(302, 71, 7)
  pix(303, 71, 7)
  rect(8, 70, 1, 1, 7)
  circ(30, 80, 0, 8)
  circb(60, 80, 1, 8)
  rect(100, 70, 0, 10, 8)
  rect(110, 70, 10, 0, 8)
end
