"""Cart GPIO across the wire (#9): the browser's queue and the Zero's pins.

Two modules, one wire shape, and no shared code between them on purpose -- the
browser end is a queue and a cache, the board end is validation and a Pin. What
holds them together is the last test in this file, which drives a real batch out
of one and into the other; sharing a module would have made them agree by
construction and proved nothing about the JSON that actually crosses.

The allowlist gets the most attention here, because it is the security model.
A pin number arrives from the network, and the pins it must never reach are the
ones the board is running ON -- its flash, its USB, its serial console. Refusing
is the whole mechanism, so the cases below are refusals.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))            # moy_webserver
sys.path.insert(0, str(ROOT / "firmware" / "seeed_xiao_esp32s3_zero"))
# APPENDED, not inserted: that directory also holds web_boot/web_canvas/serve,
# and this suite has no business changing which module a bare import of one of
# those resolves to elsewhere in the run.
sys.path.append(str(ROOT / "firmware" / "web_runner"))

import zero_gpio                                               # noqa: E402
from gpio_link import GpioLink                                 # noqa: E402


class _FakePin:
    def __init__(self, level=0):
        self.level = level
        self.writes = []

    def value(self, v=None):
        if v is None:
            return self.level
        self.level = v
        self.writes.append(v)


def _pins(levels=None):
    """A pin factory over fakes, and the dict it hands out, so a test can read
    what landed. `levels` seeds what a pin reads back."""
    levels = levels or {}
    made = {}

    def get_pin(n, mode):
        p = made.get(n)
        if p is None:
            p = made[n] = _FakePin(levels.get(n, 0))
        return p

    return get_pin, made


def _batch(ops, pin=None):
    doc = {"v": zero_gpio.PROTOCOL_V, "ops": ops}
    if pin:
        doc["pin"] = pin
    return json.dumps(doc).encode()


# -- the allowlist -----------------------------------------------------------


@pytest.mark.parametrize("n, why", [
    (26, "flash"), (30, "flash"), (37, "octal PSRAM"),   # the running system
    (19, "USB"), (20, "USB"),                            # the only cable
    (43, "UART0"), (44, "UART0"),                        # the serial console
    (0, "boot strapping"), (45, "strapping"), (46, "strapping"),
    (3, "the strict call"),                              # D2, held back
])
def test_a_pin_the_board_needs_is_not_in_the_allowlist(n, why):
    assert n not in zero_gpio.PINS, why


def test_the_allowlist_is_exactly_the_xiaos_spare_pads_plus_its_led():
    # D0=1 D1=2 D3=4 D4=5 D5=6 D8=7 D9=8 D10=9, and 21 = the on-board user LED.
    assert zero_gpio.PINS == (1, 2, 4, 5, 6, 7, 8, 9, 21)


@pytest.mark.parametrize("op, word", [
    ({"p": 43, "mode": "out", "v": 1}, "allowlist"),
    ({"p": 26, "mode": "read"}, "allowlist"),
    ({"p": -1, "mode": "read"}, "allowlist"),
    ({"p": "2", "mode": "out", "v": 1}, "pin number"),
    ({"p": None, "mode": "read"}, "pin number"),
    ({"p": True, "mode": "out", "v": 1}, "pin number"),   # True == 1 in Python
    ({"p": 2, "mode": "pwm", "v": 1}, "'out' or 'read'"),
    ({"p": 2}, "'out' or 'read'"),
    ({"p": 2, "mode": "out"}, "0 or 1"),
    ({"p": 2, "mode": "out", "v": 2}, "0 or 1"),
    ({"p": 2, "mode": "out", "v": "1"}, "0 or 1"),
    ({"p": 2, "mode": "out", "v": True}, "0 or 1"),
    ("not an op", "not an object"),
])
def test_a_bad_op_is_refused_and_never_reaches_a_pin(op, word):
    get_pin, made = _pins()
    applied, reads, errors = zero_gpio.apply_ops([op], get_pin)
    assert applied == 0 and reads == {}
    assert made == {}, "a refused op still constructed a Pin"
    assert len(errors) == 1 and word in errors[0][1]


def test_a_bad_op_skips_and_its_neighbours_still_run():
    """A batch is not a transaction. The client clears an answered batch either
    way, so aborting would let one poison op eat its neighbours forever -- and
    here a neighbour is the write that turns something OFF."""
    get_pin, made = _pins()
    applied, reads, errors = zero_gpio.apply_ops([
        {"p": 1, "mode": "out", "v": 1},
        {"p": 44, "mode": "out", "v": 1},        # refused
        {"p": 2, "mode": "out", "v": 0},
    ], get_pin)
    assert applied == 2
    assert [e[0] for e in errors] == [1]
    assert made[1].level == 1 and made[2].level == 0
    assert 44 not in made


def test_a_pin_that_throws_is_an_error_row_not_a_dead_request():
    def get_pin(n, mode):
        raise OSError("pin in use")

    applied, reads, errors = zero_gpio.apply_ops(
        [{"p": 1, "mode": "out", "v": 1}], get_pin)
    assert applied == 0 and "pin 1:" in errors[0][1]


# -- reads and writes --------------------------------------------------------


def test_a_batch_drives_outputs_and_reports_reads():
    get_pin, made = _pins({2: 1})
    applied, reads, errors = zero_gpio.apply_ops([
        {"p": 21, "mode": "out", "v": 0},
        {"p": 2, "mode": "read"},
    ], get_pin)
    assert (applied, errors) == (2, [])
    assert made[21].writes == [0]
    assert reads == {"2": 1}          # keys are strings: they cross as JSON


class _Machine:
    class Pin:
        OUT, IN, PULL_UP = 3, 1, 2

        def __init__(self, n, mode, pull=None):
            self.n, self.mode, self.pull = n, mode, pull
            _Machine.made.append((n, mode, pull))

    made = []


@pytest.fixture
def machine(monkeypatch):
    _Machine.made = []
    monkeypatch.setitem(sys.modules, "machine", _Machine)
    return _Machine


def test_reading_a_pin_never_reconfigures_it(machine):
    """The bug this exists to stop, found on glass: re-making a written pin as
    an input to answer a read drops its drive, so `pin_read(21)` on the LED you
    just lit turns the LED off. A read is a question."""
    get = zero_gpio.pin_factory()
    out = get(21, "out")
    assert get(21, "read") is out            # answered from the output latch
    assert machine.made == [(21, machine.Pin.OUT, None)]


def test_a_pin_is_what_its_first_touch_made_it_and_a_write_can_promote_it(
        machine):
    get = zero_gpio.pin_factory()
    inp = get(2, "read")
    assert inp.mode == machine.Pin.IN
    assert get(2, "read") is inp             # cached: ONE object per pin
    out = get(2, "out")                      # ...promoted by a write
    assert out is not inp and out.mode == machine.Pin.OUT
    assert get(2, "read") is out             # and it stays an output
    assert machine.made == [(2, machine.Pin.IN, machine.Pin.PULL_UP),
                            (2, machine.Pin.OUT, None)]


def test_an_input_is_pulled_up_so_an_unwired_pin_is_not_noise(machine):
    """A button between the pin and ground is the wiring a kid actually does,
    and it means nothing without a pull-up. Unwired reads 1, pressed reads 0."""
    zero_gpio.pin_factory()(4, "read")
    assert machine.made == [(4, machine.Pin.IN, machine.Pin.PULL_UP)]


# -- the endpoint ------------------------------------------------------------


def test_get_answers_the_allowlist_for_a_human():
    body = zero_gpio.handle("GET", b"")
    assert b"200 OK" in body
    doc = json.loads(body.split(b"\r\n\r\n", 1)[1])
    assert doc == {"v": 1, "pins": list(zero_gpio.PINS)}


def test_an_empty_batch_is_the_probe_and_comes_back_with_the_pins():
    get_pin, _ = _pins()
    body = zero_gpio.handle("POST", _batch([]), get_pin=get_pin)
    doc = json.loads(body.split(b"\r\n\r\n", 1)[1])
    assert doc["pins"] == list(zero_gpio.PINS) and doc["ok"] == 0


def test_a_real_batch_does_not_repeat_the_allowlist_every_pump():
    get_pin, _ = _pins()
    body = zero_gpio.handle("POST", _batch([{"p": 1, "mode": "read"}]),
                            get_pin=get_pin)
    assert "pins" not in json.loads(body.split(b"\r\n\r\n", 1)[1])


@pytest.mark.parametrize("body, status", [
    (b"not json", b"400 "),
    (b'{"ops": []}', b"400 "),                       # no version
    (b'{"v": 99, "ops": []}', b"400 "),              # a version we do not speak
    (b'{"v": 1, "ops": "nope"}', b"400 "),
])
def test_a_malformed_batch_is_a_400(body, status):
    get_pin, made = _pins()
    assert status in zero_gpio.handle("POST", body, get_pin=get_pin)
    assert made == {}


def test_the_pin_gate_refuses_a_page_that_does_not_carry_it():
    get_pin, made = _pins()
    ops = [{"p": 1, "mode": "out", "v": 1}]
    assert b"403" in zero_gpio.handle("POST", _batch(ops), pin="4242",
                                      get_pin=get_pin)
    assert b"403" in zero_gpio.handle("POST", _batch(ops, pin="0000"),
                                      pin="4242", get_pin=get_pin)
    assert made == {}, "a refused batch still drove a pin"
    assert b"200 OK" in zero_gpio.handle("POST", _batch(ops, pin="4242"),
                                         pin="4242", get_pin=get_pin)
    assert made[1].level == 1


def test_the_probe_goes_through_the_pin_gate_too():
    """So a page opened without the board's ?pin= learns it has no pins BEFORE
    the verbs exist, rather than after every write it makes comes back 403."""
    assert b"403" in zero_gpio.handle("POST", _batch([]), pin="4242")


def test_a_method_that_is_neither_is_a_405():
    assert b"405" in zero_gpio.handle("PUT", _batch([]))


def test_a_board_with_no_pin_set_accepts_a_page_that_sends_one():
    """The gate is the BOARD's, not the page's: a stray ?pin= must not lock a
    kid out of a board that was never configured with one."""
    get_pin, made = _pins()
    assert b"200 OK" in zero_gpio.handle(
        "POST", _batch([{"p": 1, "mode": "out", "v": 1}], pin="1111"),
        pin=None, get_pin=get_pin)
    assert made[1].level == 1


# -- the browser's queue -----------------------------------------------------


def _link(pins=(1, 2, 21)):
    said = []
    return GpioLink(pins, log=said.append), said


def test_a_write_queues_and_ships_in_the_next_batch():
    link, _ = _link()
    assert link.write(21, 1) is True
    doc = json.loads(link.take_json())
    assert doc == {"v": 1, "ops": [{"p": 21, "mode": "out", "v": 1}]}


def test_writes_to_one_pin_coalesce_because_a_pin_has_one_state():
    link, _ = _link()
    for v in (1, 0, 1, 1, 0):
        link.write(21, v)
    doc = json.loads(link.take_json())
    assert doc["ops"] == [{"p": 21, "mode": "out", "v": 0}]


def test_nothing_queued_is_an_empty_body_not_an_empty_batch():
    link, _ = _link()
    assert link.take_json() == ""


def test_only_one_batch_is_ever_in_flight():
    link, _ = _link()
    link.write(1, 1)
    assert link.take_json() != ""
    link.write(2, 1)
    assert link.take_json() == "", "a second batch went out unanswered"
    link.ack(True, '{"ok": 1}')
    assert json.loads(link.take_json())["ops"] == [
        {"p": 2, "mode": "out", "v": 1}]


def test_reading_a_pin_subscribes_it_and_answers_from_the_last_batch():
    link, _ = _link()
    assert link.read(2) is None                # nothing has been answered yet
    doc = json.loads(link.take_json())
    assert doc["ops"] == [{"p": 2, "mode": "read"}]
    link.ack(True, '{"ok": 1, "reads": {"2": 1}}')
    assert link.read(2) == 1
    # ...and it stays subscribed, so the value keeps refreshing.
    assert json.loads(link.take_json())["ops"] == [{"p": 2, "mode": "read"}]


def test_the_page_pin_rides_every_batch():
    link, _ = _link()
    link.write(1, 1)
    assert json.loads(link.take_json("4242"))["pin"] == "4242"


def test_a_failed_post_requeues_its_writes():
    link, _ = _link()
    link.write(1, 1)
    link.take_json()
    link.ack(False)
    assert json.loads(link.take_json())["ops"] == [
        {"p": 1, "mode": "out", "v": 1}]


def test_a_requeue_never_overwrites_a_newer_write():
    """The value the cart set while the batch was away is the current truth. A
    failed POST replaying the stale one on top would turn a pin back."""
    link, _ = _link()
    link.write(1, 1)
    link.take_json()
    link.write(1, 0)              # the cart moved on while it was in flight
    link.ack(False)
    assert json.loads(link.take_json())["ops"] == [
        {"p": 1, "mode": "out", "v": 0}]


def test_a_board_that_stops_answering_makes_the_verbs_inert():
    link, said = _link()
    for _ in range(10):
        link.write(1, 1)
        link.take_json()
        link.ack(False)
    assert link.dead is True
    assert link.write(1, 1) is False and link.read(1) is None
    assert link.take_json() == ""
    assert any("stopped answering" in s for s in said)


def test_a_pin_outside_the_boards_list_is_refused_and_said_once():
    link, said = _link()
    for _ in range(60):                        # a whole second of _update
        assert link.write(44, 1) is False
    assert link.read(44) is None
    assert link.take_json() == "", "a refused pin still made a batch"
    assert len(said) == 1 and "44" in said[0]


def test_a_junk_answer_does_not_take_the_link_down():
    link, _ = _link()
    link.write(1, 1)
    link.take_json()
    assert link.ack(True, "<html>proxy error</html>") is True
    assert link.dead is False


def test_the_boards_error_rows_reach_the_console():
    link, said = _link()
    link.write(1, 1)
    link.take_json()
    link.ack(True, '{"ok": 0, "err": [[0, "pin 1: in use"]]}')
    assert any("in use" in s for s in said)


# -- the two ends, driven against each other ---------------------------------


def test_a_batch_the_browser_builds_is_one_the_board_understands():
    """The shape check with teeth: no shared module, so this is the only thing
    that would notice the two ends drifting apart -- a renamed key, a version
    bump on one side, a read answered under an int key the other cannot find."""
    get_pin, made = _pins({2: 1})
    link, said = _link(pins=zero_gpio.PINS)

    link.write(21, 0)
    link.read(2)
    body = link.take_json("4242")

    answer = zero_gpio.handle("POST", body.encode(), pin="4242",
                              get_pin=get_pin)
    assert b"200 OK" in answer
    link.ack(True, answer.split(b"\r\n\r\n", 1)[1].decode())

    assert made[21].writes == [0]          # the board drove the LED
    assert link.read(2) == 1               # ...and the level came back
    assert said == []


def test_the_probe_the_worker_sends_is_the_one_the_board_answers():
    """worker.js probes with an empty batch and reads `pins` back; web_boot then
    builds the link from that list. This is that handshake, minus the fetch."""
    probe = json.dumps({"v": 1, "ops": [], "pin": "4242"}).encode()
    answer = zero_gpio.handle("POST", probe, pin="4242")
    doc = json.loads(answer.split(b"\r\n\r\n", 1)[1])
    link = GpioLink(doc["pins"])
    assert link.write(21, 1) is True
    assert link.write(44, 1) is False
