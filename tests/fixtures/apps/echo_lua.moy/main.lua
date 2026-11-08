-- A Lua service from docs/app_api_v1.md alone (sections 7 and 8): it sleeps
-- until a "ping" arrives and answers its sender with a "pong" carrying the
-- number plus one. Another app reaches it because its manifest lists "ping"
-- under receives. tests/test_app_examples.py drives it.

function moy_start()
  moybyte.on("ping", function(sender, n)
    moybyte.send(sender, "pong", n + 1)
  end)
end
