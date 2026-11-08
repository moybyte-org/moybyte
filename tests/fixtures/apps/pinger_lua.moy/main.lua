-- A Lua app from docs/app_api_v1.md alone (sections 8 and 9): it pings the
-- Echo service and hears its answer, posts a notification, sets an alarm
-- and writes the OS log. tests/test_app_examples.py drives it.

ANSWER, NOTE, RANG = nil, nil, 0

function _init()
  moybyte.on("pong", function(sender, n)
    ANSWER = sender .. " says " .. n
  end)
  moybyte.send("local.echo", "ping", 41)
  NOTE = moybyte.notify("Pinger", "Sent a ping")
  moybyte.at(os.time(), function() RANG = RANG + 1 end)
  moybyte.log(moybyte.INFO, "pinged the echo")
end

function _draw()
  cls(0)
  print(ANSWER or "waiting", 4, 4, 7)
end
