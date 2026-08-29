"""The Zero's physical I/O endpoint (#9): POST /gpio, batched, allowlisted.

The re-based Zero exists to be what a browser is not. It already contributes
STORAGE -- the cart store the wasm console pulls and syncs back to -- and this
is the other half: PINS. A cart runs in the browser and still lights an LED,
because `pin_write` queues an op that the page POSTs to whichever board served
it (firmware/web_runner/gpio_link.py is the queue at the far end).

    GET  /gpio?pin=1234  -> {"v": 1, "pins": [...]}  the allowlist, for a human
    POST /gpio           -> {"v": 1, "ops": [...], "pin": "1234"}
                 -> {"ok": n, "reads": {"<pin>": 0|1}, "err": [[i, why], ...]}

Both are PIN-GATED (2026-08-25): on a board that has a pin, nothing here
answers without it.

An op is one of:

    {"p": 2, "mode": "out", "v": 1}     drive pin 2 high
    {"p": 2, "mode": "read"}            report pin 2's level in `reads`

Digital in and out, and nothing else. PWM, servos and the H-bridge motor pair
#9 is really about are deliberately not here yet -- see the README's absences.

THE ALLOWLIST IS THE SECURITY MODEL, and it is an allowlist for the same reason
moy_webhost's asset table is one: the set of pins a kid may drive is FIXED and
known here, so there is no reason to take a pin NUMBER off the network and then
reason about whether it happens to be safe. A pin outside it is REFUSED and
never touched -- not clamped to a neighbour, not coerced -- because the failure
modes are not survivable in the way a 400 is. Driving a flash or PSRAM line
kills the running system mid-instruction; driving the USB pair kills the only
cable this headless board has; driving UART0 garbles the console you would get
it back through. A refusal costs a kid a puzzled moment. The alternatives cost
a trip to the ROM loader.

A bad op SKIPS and is reported in `err`; it never aborts the batch. Same
reasoning as moy_webhost's sync apply: the client clears an answered batch
either way, so aborting would let one poison op eat its innocent neighbours
forever -- and here the neighbours are the write that turns a motor OFF.
"""

import json

from moy_webserver import http_response, query_param

# Wire version. Bumped only if the op shape changes; the browser sends it and a
# batch without it is refused, so an old page cannot half-speak to a new board.
PROTOCOL_V = 1

MODES = ("out", "read")

# The XIAO ESP32-S3's safe user GPIOs. The board's silkscreen names are D0..D10;
# what travels on the wire is the GPIO number, because that is what machine.Pin
# takes and what every ESP32 datasheet and schematic is written in.
#
#     D0=1   D1=2   D2=3*  D3=4   D4=5(SDA)  D5=6(SCL)
#     D6=43* D7=44* D8=7(SCK)  D9=8(MISO)  D10=9(MOSI)      * excluded, below
#
# 21 is not on a pad at all: it is the board's own user LED (active LOW -- the
# Seeed schematic ties the cathode to the pin). It is in the list because
# "blink the light that is already soldered on" is the one physical-I/O
# hello-world that needs no wiring, no driver board and nothing ordered.
#
# WHAT IS EXCLUDED AND WHY -- each of these is a pin some other list would have
# handed out:
#   3 (D2)        STRAPPING. It selects the JTAG signal source at reset. Its
#                 default is genuinely don't-care and driving it after boot is
#                 fine in practice, so this is the strict call rather than the
#                 forced one: it is the only exposed pad held back, and
#                 re-admitting it is a one-line decision with this sentence as
#                 the argument to answer.
#   43, 44 (D6/D7) UART0 TX/RX. Stock MicroPython keeps a REPL on them, so
#                 driving either garbles the serial console -- which on a
#                 headless board is the recovery path, not a convenience.
#   19, 20        The native USB D-/D+ pair. Driving them drops the USB device
#                 this board is reached through.
#   0, 45, 46     Boot-mode / VDD_SPI strapping. 0 held low across a reset
#                 lands in the ROM loader.
#   26..37        SPI flash and the octal PSRAM. Driving one of these does not
#                 fail, it stops the machine.
PINS = (1, 2, 4, 5, 6, 7, 8, 9, 21)

_FACTORY = []                      # the lazily-built default pin factory


def parse_batch(body):
    """The POST body -> (ops, pin), or (None, None) on anything malformed.

    Tolerant of bytes (the transport hands bytes), strict about everything
    else: this is the one function standing between the network and a pin.
    """
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:            # noqa: BLE001 -- not text: not a batch
            return None, None
    try:
        doc = json.loads(body)
    except Exception:                # noqa: BLE001
        return None, None
    if not isinstance(doc, dict) or doc.get("v") != PROTOCOL_V:
        return None, None
    ops = doc.get("ops")
    if not isinstance(ops, list):
        return None, None
    return ops, doc.get("pin")


def _op(op, pins):
    """One op -> (pin, mode, value). Raises ValueError with a sentence a kid's
    grown-up can act on -- these come back in `err` and end up on a console."""
    if not isinstance(op, dict):
        raise ValueError("op is not an object")
    n = op.get("p")
    # bool BEFORE int: True is an int in Python and `True in PINS` is a pin.
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("p must be a pin number")
    if n not in pins:
        raise ValueError("pin %d is not on this board's allowlist" % n)
    mode = op.get("mode")
    if mode not in MODES:
        raise ValueError("mode must be 'out' or 'read'")
    if mode == "read":
        return n, mode, None
    v = op.get("v")
    if isinstance(v, bool) or v not in (0, 1):
        raise ValueError("v must be 0 or 1")
    return n, mode, v


def apply_ops(ops, get_pin, pins=PINS):
    """Run a batch. Returns (applied, reads, errors).

    `get_pin(n, mode)` is injected: on the board it is `pin_factory()`, in a
    host test it is a fake, and that is the whole reason the validation above
    can be tested without a machine module underneath it.
    """
    applied = 0
    reads = {}
    errors = []
    for i, op in enumerate(ops):
        try:
            n, mode, v = _op(op, pins)
        except ValueError as exc:
            errors.append((i, str(exc)))
            continue
        try:
            p = get_pin(n, mode)
            if mode == "out":
                p.value(v)
            else:
                reads[str(n)] = 1 if p.value() else 0
        except Exception as exc:     # noqa: BLE001 -- a dead pin is not a 500
            errors.append((i, "pin %d: %s" % (n, exc)))
            continue
        applied += 1
    return applied, reads, errors


def pin_factory():
    """A `machine.Pin` factory that remembers ONE object per pin.

    Keyed on the pin and NOT on (pin, mode), because MicroPython hands out one
    object per physical pin: constructing Pin(n, IN) reconfigures the very
    object an (n, "out") entry is still holding, so caching both would leave a
    stale entry silently driving an input.

    THE RULE IS THAT A READ NEVER RECONFIGURES ANYTHING. A pin's first touch
    decides what it is -- an output if it was written, an input if it was read
    -- and only a write can promote an input to an output. Reading is then a
    question, which is what a kid means by it: `pin_write(21, 0)` followed by
    `pin_read(21)` has to answer 0 and leave the LED on. Re-making the pin as
    an input to answer would drop the drive, i.e. reading a light would turn it
    off. (The cost is that a written pin stays an output until the board
    reboots. That is the trade, and it is the right way round: nothing a cart
    can do this way makes the board stop working.)

    INPUTS GET A PULL-UP. A floating input reads noise, and the wiring a kid
    actually does -- a button between the pin and ground -- needs a pull-up to
    mean anything at all. So an unwired pin reads 1 and a pressed button reads
    0, which is deterministic and is what docs/moy_cart_api.md describes.
    """
    import machine
    cache = {}

    def get_pin(n, mode):
        ent = cache.get(n)
        if ent is None:
            if mode == "out":
                p = machine.Pin(n, machine.Pin.OUT)
            else:
                p = machine.Pin(n, machine.Pin.IN, machine.Pin.PULL_UP)
            cache[n] = (mode, p)
            return p
        if mode == "out" and ent[0] != "out":
            p = machine.Pin(n, machine.Pin.OUT)
            cache[n] = ("out", p)
            return p
        return ent[1]

    return get_pin


def _default_factory():
    if not _FACTORY:
        _FACTORY.append(pin_factory())
    return _FACTORY[0]


def handle(method, body, pin=None, get_pin=None, pins=PINS, query=""):
    """One /gpio request -> complete http_response bytes.

    `pin` is this board's PIN (zero.json) when one is configured, and since
    2026-08-25 it gates BOTH methods -- the owner call that made the pin gate
    everything but the boot assets. The GET used to be open on the reasoning
    that it changes nothing; what it hands over is this board's wiring, which
    is a fact about somebody's house, and the page never needed it (it probes
    with an EMPTY POST, which was always gated). The GET carries its pin the
    only place a GET can, `?pin=` -- `query` is the request target the caller
    read it from; the POST keeps carrying it in the batch body.
    """
    if method == "GET":
        if pin and query_param(query, "pin") != pin:
            return http_response(403, '{"error":"pin"}')
        return http_response(200, json.dumps({"v": PROTOCOL_V,
                                              "pins": list(pins)}))
    if method != "POST":
        return http_response(405, '{"error":"GET or POST"}')
    ops, sent = parse_batch(body)
    if ops is None:
        return http_response(400, '{"error":"bad batch"}')
    if pin and sent != pin:
        return http_response(403, '{"error":"pin"}')
    if not ops:
        # AN EMPTY BATCH IS THE PROBE, and the allowlist is what it asks for.
        # Answering it here rather than on every batch is the difference
        # between one list and one per pump; answering it on the POST rather
        # than the GET is what puts the probe behind the pin gate, so a page
        # that cannot write finds out before it has any verbs to write with.
        #
        # It also touches no pin, which is why the factory is resolved below
        # and not here: a page merely asking whether this board has pins must
        # not be what makes it claim one.
        return http_response(200, json.dumps({
            "ok": 0, "reads": {}, "err": [], "pins": list(pins)}))
    applied, reads, errors = apply_ops(ops, get_pin or _default_factory(), pins)
    return http_response(200, json.dumps({
        "ok": applied, "reads": reads,
        "err": [list(e) for e in errors[:8]],
    }))
