-- text -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/text.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- The whole printable range (SPEC.md 6), then bytes outside it,
-- which must draw nothing and still advance 8px.

function _draw()
  cls(1)
  print(" !\"#$%&'()*+,-./0123456789:;<=>?", 8, 8, 7)
  print("@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_", 8, 18, 7)
  print("`abcdefghijklmnopqrstuvwxyz{|}~\127", 8, 28, 7)
  print("", 8, 72, 7)
  print("negative", -20, 84, 12)
  print("colour 0", 8, 96, 8)
  print("colour 1", 8, 105, 9)
  print("colour 2", 8, 114, 10)
  print("colour 3", 8, 123, 11)
  print("colour 4", 8, 132, 12)
  print("colour 5", 8, 141, 13)
  print("colour 6", 8, 150, 14)
  print("colour 7", 8, 159, 15)
end
