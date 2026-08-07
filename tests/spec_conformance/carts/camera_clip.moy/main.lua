-- camera_clip -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/camera_clip.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- camera and clip TOGETHER. clip is screen space, applied
-- after the camera offset -- an implementation that clips in
-- world space passes both features separately and fails this.

function _draw()
  cls(1)
  camera(20, 10)
  rect(0, 0, 40, 40, 8)
  camera()
  rect(0, 60, 40, 40, 3)
  clip(100, 20, 60, 60)
  rect(80, 0, 200, 200, 11)
  circ(130, 50, 40, 10)
  clip()
  rect(0, 120, 20, 20, 7)
  camera(-40, -100)
  clip(200, 120, 80, 80)
  rect(0, 0, 400, 400, 12)
  print("CLIPPED", 170, 40, 7)
  clip()
  camera()
  clip(10, 200, 0, 0)
  rect(0, 190, 300, 40, 14)
  clip()
  clip(-50, -50, 500, 500)
  rect(280, 200, 60, 60, 15)
  clip()
end
