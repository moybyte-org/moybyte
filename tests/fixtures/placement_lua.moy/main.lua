-- The on-glass check for #214: the placement API called from Lua, on a board.
--
-- Not a shipped cart. Push it with `tools/push_cart.py
-- tests/fixtures/placement_lua.moy --board <tdeck|p4|guition_s3>` and open it.
-- Every line it prints is a value the glue had to carry:
--
--   SCENE 4      scene() reached Lua as a table of rows. It used to arrive nil,
--                and `ipairs(scene())` was "value expected" -- the crash panel,
--                not a wrong number
--   TAGS 1/3     the rows carry the .tag the kid branches on
--   AT 40,40     .x/.y arrived, and the d-pad moves the LIVE actor
--   LIVE 4->1    remove_actor reached the Python actor draw_scene draws
--   GOT 3        touching() found the coins the player walked onto
--
-- The outlined boxes are drawn from the scene ROWS (authored, never changes);
-- the filled ones are the LIVE actors. Walk the white player onto a coin: its
-- box goes, GOT rises, LIVE falls.

score = 0

function _init()
  N = #scene()
  PLAYERS, COINS = #actors("player"), #actors("coin")
end

function _update(dt)
  for _, p in ipairs(actors("player")) do
    if btn("left")  then move_actor(p, -2, 0) end
    if btn("right") then move_actor(p, 2, 0) end
    if btn("up")    then move_actor(p, 0, -2) end
    if btn("down")  then move_actor(p, 0, 2) end
  end
  for _, c in ipairs(actors("coin")) do
    if touching(c, "player") then
      remove_actor(c)
      score = score + 1
    end
  end
end

function _draw()
  cls(1)
  for _, a in ipairs(scene()) do             -- the AUTHORED placement
    rectb(a.x - 1, a.y - 1, 10, 10, 6)
  end
  for _, a in ipairs(actors()) do            -- the LIVE world
    rect(a.x, a.y, 8, 8, a.tag == "player" and 7 or 10)
  end
  draw_scene()
  local p = actors("player")[1]
  print("SCENE " .. N, 4, 4, 7)
  print("TAGS " .. PLAYERS .. "/" .. COINS, 4, 14, 7)
  print("AT " .. p.x .. "," .. p.y, 4, 24, 7)
  print("LIVE " .. N .. "->" .. #actors(), 4, 34, 7)
  print("GOT " .. score, 4, 44, 10)
end
