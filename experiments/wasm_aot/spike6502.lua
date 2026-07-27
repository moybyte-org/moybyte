-- #158 spike: a representative 6502 interpreter inner loop in Lua 5.4.
-- Measures the cost of the ONE thing an emulator does most: fetch -> decode ->
-- dispatch -> execute -> flags -> memory. Not a full core (7 opcodes), but the
-- per-instruction cost profile is the real one, so instr/sec extrapolates.
--
-- Memory model is the real NES one: 2KB RAM mirrored to $0000-$1FFF, PRG at
-- $8000+. Registers/flags are chunk locals -> upvalues in the op closures,
-- which is the FAST shape in Lua (GETUPVAL, not a table lookup).

local ram = {}
for i = 0, 2047 do ram[i] = 0 end
local rom = {}
for i = 0, 255 do rom[i] = 0 end

-- The test program at $8000 -- a store/increment/compare/branch loop, the
-- shape real 6502 game code spends most of its time in:
--   8000: A2 00        LDX #$00
--   8002: BD 00 03     LDA $0300,X     ; memory read, indexed
--   8005: 69 01        ADC #$01        ; ALU + carry/overflow flags
--   8007: 9D 00 02     STA $0200,X     ; memory write, indexed
--   800A: E8           INX
--   800B: E0 00        CPX #$00
--   800D: D0 F3        BNE $8002
--   800F: 4C 00 80     JMP $8000       ; outer loop, so the core never halts
-- 6 instructions / 18 cycles per inner iteration = 3.0 cycles/instr (the NES
-- average is ~3.5, so this slightly OVERSTATES instructions per emulated cycle,
-- i.e. it is the pessimistic direction for the fps figure).
local prog = {0xA2, 0x00, 0xBD, 0x00, 0x03, 0x69, 0x01, 0x9D, 0x00, 0x02,
              0xE8, 0xE0, 0x00, 0xD0, 0xF3, 0x4C, 0x00, 0x80}
for i = 1, #prog do rom[i - 1] = prog[i] end

local A, X, PC = 0, 0, 0x8000
local C, Z, N, V = 0, 1, 0, 0

local function rd(a)
  if a < 0x2000 then return ram[a & 0x07FF] end
  if a >= 0x8000 then return rom[a - 0x8000] end
  return 0
end

local function wr(a, v)
  if a < 0x2000 then ram[a & 0x07FF] = v end
end

local ops = {}

ops[0xA2] = function()                     -- LDX #imm
  local v = rd(PC); PC = PC + 1
  X = v
  Z = (v == 0) and 1 or 0
  N = (v >= 0x80) and 1 or 0
  return 2
end

ops[0xBD] = function()                     -- LDA abs,X
  local lo = rd(PC); local hi = rd(PC + 1); PC = PC + 2
  local v = rd((hi * 256 + lo + X) & 0xFFFF)
  A = v
  Z = (v == 0) and 1 or 0
  N = (v >= 0x80) and 1 or 0
  return 4
end

ops[0x69] = function()                     -- ADC #imm
  local v = rd(PC); PC = PC + 1
  local t = A + v + C
  C = (t > 0xFF) and 1 or 0
  local r = t & 0xFF
  V = (((A ~ r) & (v ~ r) & 0x80) ~= 0) and 1 or 0
  A = r
  Z = (r == 0) and 1 or 0
  N = (r >= 0x80) and 1 or 0
  return 2
end

ops[0x9D] = function()                     -- STA abs,X
  local lo = rd(PC); local hi = rd(PC + 1); PC = PC + 2
  wr((hi * 256 + lo + X) & 0xFFFF, A)
  return 5
end

ops[0xE8] = function()                     -- INX
  X = (X + 1) & 0xFF
  Z = (X == 0) and 1 or 0
  N = (X >= 0x80) and 1 or 0
  return 2
end

ops[0xE0] = function()                     -- CPX #imm
  local v = rd(PC); PC = PC + 1
  local t = X - v
  C = (t >= 0) and 1 or 0
  local r = t & 0xFF
  Z = (r == 0) and 1 or 0
  N = (r >= 0x80) and 1 or 0
  return 2
end

ops[0x4C] = function()                     -- JMP abs
  local lo = rd(PC); local hi = rd(PC + 1)
  PC = (hi * 256 + lo) & 0xFFFF
  return 3
end

ops[0xD0] = function()                     -- BNE rel
  local o = rd(PC); PC = PC + 1
  if Z == 0 then
    if o >= 0x80 then o = o - 256 end
    PC = (PC + o) & 0xFFFF
    return 3
  end
  return 2
end

-- Run n emulated instructions; returns emulated CYCLES (so the caller can
-- convert to emulated MHz as well as instr/sec).
function step(n)
  -- moy_lua.call pushes its argument as a Lua FLOAT, so normalise to an integer
  -- loop -- otherwise the device runs a float for-loop and the host an integer
  -- one, and the comparison measures the wrong thing.
  n = math.tointeger(n) or (n // 1)
  local cyc = 0
  for _ = 1, n do
    local op = rd(PC)
    PC = PC + 1
    cyc = cyc + ops[op]()
  end
  return cyc
end

-- Raw VM throughput reference: one add + one compare per iteration.
function spin(n)
  local s = 0
  for i = 1, n do s = s + i end
  return s
end
