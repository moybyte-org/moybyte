-- sprites -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/sprites.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- Flips, integer scales, colorkeys, out-of-range tile ids, and
-- sprites under camera and clip.

function _draw()
  cls(1)
  spr(3, 8, 8, -1, 1, 0)
  spr(3, 32, 8, -1, 1, 1)
  spr(3, 56, 8, -1, 1, 2)
  spr(3, 80, 8, -1, 1, 3)
  spr(3, 8, 32, -1, 3, 0)
  spr(3, 48, 32, -1, 3, 1)
  spr(3, 88, 32, -1, 3, 2)
  spr(3, 128, 32, -1, 3, 3)
  spr(2, 180, 8, -1, 1, 0)
  spr(2, 210, 8, -1, 2, 0)
  spr(2, 240, 8, -1, 3, 0)
  spr(2, 270, 8, -1, 4, 0)
  spr(5, 8, 140, -1, 4, 0)
  spr(5, 48, 140, -1, 4, 2)
  spr(4, 96, 140, 11, 4, 0)
  spr(0, 140, 140, -1, 4, 0)
  spr(511, 180, 140, -1, 4, 0)
  spr(512, 220, 140, -1, 4, 0)
  spr(-1, 260, 140, -1, 4, 0)
  spr(3, -6, 200, -1, 2, 0)
  spr(3, 306, 200, -1, 2, 0)
  camera(-100, -8)
  spr(3, 0, 0, -1, 2, 0)
  camera()
  clip(140, 190, 24, 24)
  spr(2, 136, 186, -1, 4, 0)
  clip()
end
