-- layer_small -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/layer_small.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- draw_layer of a layer narrower and shorter than the screen,
-- asked for past its far corner: the camera is 0 on both axes,
-- the rest of the screen is not written, and the screen's camera,
-- clip and pal do not apply.

local L1

function _init()
  L1 = make_layer(200, 120)
end

function _draw()
  cls(1)
  rect(0, 0, 10, 240, 2)
  rect(20, 0, 10, 240, 3)
  rect(40, 0, 10, 240, 4)
  rect(60, 0, 10, 240, 2)
  rect(80, 0, 10, 240, 3)
  rect(100, 0, 10, 240, 4)
  rect(120, 0, 10, 240, 2)
  rect(140, 0, 10, 240, 3)
  rect(160, 0, 10, 240, 4)
  rect(180, 0, 10, 240, 2)
  rect(200, 0, 10, 240, 3)
  rect(220, 0, 10, 240, 4)
  rect(240, 0, 10, 240, 2)
  rect(260, 0, 10, 240, 3)
  rect(280, 0, 10, 240, 4)
  rect(300, 0, 10, 240, 2)
  rect(0, 5, 320, 4, 5)
  rect(0, 25, 320, 4, 6)
  rect(0, 45, 320, 4, 5)
  rect(0, 65, 320, 4, 6)
  rect(0, 85, 320, 4, 5)
  rect(0, 105, 320, 4, 6)
  rect(0, 125, 320, 4, 5)
  rect(0, 145, 320, 4, 6)
  rect(0, 165, 320, 4, 5)
  rect(0, 185, 320, 4, 6)
  rect(0, 205, 320, 4, 5)
  rect(0, 225, 320, 4, 6)
  L1:cls(0)
  L1:rect(0, 0, 8, 120, 16)
  L1:rect(16, 0, 8, 120, 17)
  L1:rect(32, 0, 8, 120, 18)
  L1:rect(48, 0, 8, 120, 19)
  L1:rect(64, 0, 8, 120, 20)
  L1:rect(80, 0, 8, 120, 21)
  L1:rect(96, 0, 8, 120, 22)
  L1:rect(112, 0, 8, 120, 23)
  L1:rect(128, 0, 8, 120, 24)
  L1:rect(144, 0, 8, 120, 25)
  L1:rect(160, 0, 8, 120, 26)
  L1:rect(176, 0, 8, 120, 27)
  L1:rect(0, 12, 200, 2, 48)
  L1:rect(0, 28, 200, 2, 49)
  L1:rect(0, 44, 200, 2, 50)
  L1:rect(0, 60, 200, 2, 51)
  L1:rect(0, 76, 200, 2, 52)
  L1:rect(0, 92, 200, 2, 53)
  L1:rect(0, 108, 200, 2, 54)
  L1:rectb(0, 0, 200, 120, 8)
  L1:line(0, 0, 199, 119, 7)
  L1:print("200X120", 4, 4, 7)
  camera(11, 7)
  clip(40, 30, 100, 60)
  pal(16, 8)
  draw_layer(L1, 500, 300)
  pal()
  clip()
  camera()
end
