-- text_bytes -- a moy conformance cart. GENERATED; do not edit.
-- Regenerate with: python3 conformance/build.py
--
-- One static frame replaying a recorded verb trace. Compare the frame
-- your host renders against conformance/golden/text_bytes.png -- SPEC.md 11
-- calls conformance pixel-identical, so any difference is a bug in one
-- of the two implementations and the point is to find out which.
--
-- Bytes outside 0x20-0x7F, which draw nothing and still advance
-- 8px. SPEC.md 6: print walks BYTES, so a two-byte UTF-8
-- character takes two cells, not one.

function _draw()
  cls(1)
  print("A\0B", 8, 8, 10)
  print("C\31D", 8, 20, 10)
  print("E\127F", 8, 32, 10)
  print("G\255H", 8, 44, 10)
  print("\0\0\0\0TAIL", 8, 56, 7)
  print("caf\195\169", 8, 68, 11)
end
