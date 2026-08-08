-- edges -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/edges.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- Clipping. Every shape hangs off an edge; a host that clamps
-- instead of clipping, or wraps a row, fails here and nowhere else.

function _draw()
  cls(0)
  rect(-20, -20, 60, 60, 8)
  rect(300, 220, 60, 60, 11)
  rect(-30, 100, 20, 20, 12)
  circ(0, 0, 30, 10)
  circ(320, 240, 30, 14)
  circb(160, -10, 40, 7)
  line(-50, 120, 370, 130, 15)
  line(160, -50, 170, 290, 6)
  print("EDGE", -12, 4, 7)
  print("EDGE", 300, 232, 7)
  pix(-1, -1, 8)
  pix(320, 240, 8)
end
