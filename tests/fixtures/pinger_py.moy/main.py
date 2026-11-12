# Pinger: a Python service that sends a message every PERIOD_MS, the wake a
# service woken by messages is measured under (tests/test_wasi_services.py,
# #246). Each tick sends "ping" to the WASI service fixture
# (tests/fixtures/wasm/wasi_svc.moy), which answers it with a "pong" and a
# log line, and "award" for an id no badge has to the achievements process
# (tests/fixtures/achievements_lua.moy), which finds nothing to do. A
# receiver takes one only where its manifest lists the name under
# `receives` (native/moy_kernel/moy_evq.h, THE RULE).

import moybyte

PERIOD_MS = 100
SENT = [0, 0]                   # ticks, messages taken


def _tick():
    SENT[0] += 1
    SENT[1] += (moybyte.send("wasi.svc", "ping", SENT[0])
                + moybyte.send("moybyte.achievements", "award", "none", True))


def moy_start():
    moybyte.every(PERIOD_MS, _tick)
