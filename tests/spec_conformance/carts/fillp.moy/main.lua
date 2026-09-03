-- fillp -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/fillp.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- The fill pattern (SPEC.md 6): a set bit is a hole, a hole takes
-- the second colour or nothing, the pattern is anchored to the
-- SCREEN through camera and clip, every shape verb honours it and
-- pix, print, sprites and the map do not.

function _draw()
  cls(1)
  rect(8, 8, 40, 24, 8)
  fillp(42405, -1)
  rect(56, 8, 40, 24, 8)
  fillp(42405, 12)
  rect(104, 8, 40, 24, 8)
  fillp(61680, -1)
  rect(152, 8, 40, 24, 8)
  fillp(32768, 0)
  rect(200, 8, 40, 24, 8)
  fillp(65535, -1)
  rect(248, 8, 40, 24, 8)
  fillp(65535, 11)
  rect(248, 36, 40, 24, 8)
  fillp(23130, 5)
  circ(28, 70, 20, 10)
  circb(76, 70, 20, 10)
  tri(100, 50, 140, 60, 110, 90, 10)
  trib(150, 50, 190, 60, 160, 90, 10)
  line(200, 50, 240, 90, 10)
  line(200, 90, 240, 50, 10)
  rectb(250, 50, 40, 40, 10)
  oval(8, 100, 50, 30, 10)
  ovalb(64, 100, 50, 30, 10)
  fillp(3855, -1)
  pix(130, 100, 7)
  pix(131, 101, 7)
  print("SOLID", 136, 100, 7)
  spr(1, 190, 100, -1, 3, 0)
  map(0, 0, 3, 2, 230, 100, -1, 1)
  fillp(42405, 12)
  camera(1, 1)
  rect(8, 140, 40, 24, 8)
  camera()
  rect(56, 140, 40, 24, 8)
  clip(110, 145, 30, 14)
  rect(104, 140, 40, 24, 8)
  clip()
  pal(12, 14)
  rect(152, 140, 40, 24, 8)
  pal()
  fillp()
  rect(200, 140, 40, 24, 8)
  fillp(42405, -1)
  rect(248, 140, 40, 24, 8)
  fillp(107941, -1)
  rect(8, 180, 40, 24, 8)
  fillp()
end
