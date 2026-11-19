-- A careless save: pmem written every frame, under a tone that never stops.
-- Cell 0 is the frame count, so it changes every frame; cell 1 counts the
-- runs. pmem_spam_py.moy is the same cart in Python.

local n = 0

function _init()
  pmem(1, pmem(1) + 1)
end

function _update()
  n = n + 1
  pmem(0, n)
  if n % 20 == 1 then beep(220, 1.0) end
end

function _draw()
  cls(1)
  print("pmem 0 = " .. pmem(0), 8, 8, 7)
  print("runs " .. pmem(1), 8, 20, 7)
end
