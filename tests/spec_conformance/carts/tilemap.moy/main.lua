-- tilemap -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/tilemap.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- map() regions, screen offsets, scale, colorkey, camera, clip,
-- and a region starting outside the map.

function _draw()
  cls(1)
  map(0, 0, 10, 6, 0, 0, -1, 1)
  map(2, 1, 4, 3, 100, 8, -1, 1)
  map(0, 0, 10, 6, 8, 60, -1, 2)
  map(0, 0, 4, 4, 200, 60, 11, 1)
  camera(-10, -160)
  map(0, 0, 6, 4, 0, 0, -1, 1)
  camera()
  clip(200, 160, 60, 60)
  map(0, 0, 10, 6, 190, 150, -1, 2)
  clip()
  map(-2, -2, 6, 6, 260, 200, -1, 1)
end
