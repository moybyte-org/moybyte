"""A held key repeats at the input layer, and the trackball's steps are counts
(docs/scroll_and_text_2026-10.md §2.2, §2.3).

The table (native/moy_input/moy_input.c, through the host's binding) repeats
a held button and a source's typing level on the clock `begin_frame` is
given: REPEAT_DELAY_MS of level, then one every REPEAT_RATE_MS, a slow frame
owing every period it spanned. The counts sit beside the frame (`repeats`,
`steps`, `rep_word`, `key_repeats`) and never in a press edge, a key event
or `last_key`, so a cart's btn/btnp/key/keyp read what they read without
them. Every head reaches the same table: the host's keyboard and the
browser's (one ConsoleDriver, the page forwarding no OS autorepeat), the
T-Deck's matrix (its typing level, executed here over a fake bus), a BLE
keyboard's held key.
"""

import re
from pathlib import Path

import pytest

from runtime import host_app, moy_carts, web_input
from runtime import moy_input as mi
from ws_helpers import select_on_home

ROOT = Path(__file__).resolve().parent.parent
DELAY, RATE = 400, 50


def _table():
    t = mi.InputTable()
    assert t.repeat == (DELAY, RATE), "the defaults moy_input.h names"
    return t, t.source("k")


def _run(t, frames, ms, now, name="down"):
    """`frames` merges `ms` apart from `now`: (clock, pressed, repeats) each."""
    out = []
    for _ in range(frames):
        now += ms
        t.begin_frame(now)
        out.append((now, t.pressed(name), t.repeats(name)))
    return out, now


# -- the table ---------------------------------------------------------------------

def test_a_held_button_repeats_after_the_delay_at_the_rate():
    t, s = _table()
    t.begin_frame(1000)
    s.set_button("down", True)
    got, now = _run(t, 100, 10, 1000)          # a second of 10 ms frames
    assert [p for _c, p, _r in got].count(True) == 1, "one press edge, held"
    first = next(c for c, _p, r in got if r)
    assert first == 1010 + DELAY, "the first repeat comes a delay after the press"
    reps = [c for c, _p, r in got if r]
    assert all(b - a == RATE for a, b in zip(reps, reps[1:])), "then one a period"
    assert sum(r for _c, _p, r in got) == (2000 - 1010 - DELAY) // RATE + 1
    s.set_button("down", False)
    got, now = _run(t, 50, 10, now)
    assert not any(r for _c, _p, r in got), "a release ends it"
    s.set_button("down", True)                 # pressed again: the delay again
    got, now = _run(t, 30, 10, now)
    assert sum(r for _c, _p, r in got) == 0 and got[0][1]


def test_a_slow_frame_owes_every_period_it_spanned():
    t, s = _table()
    t.begin_frame(1000)
    s.set_button("up", True)
    t.begin_frame(1010)                         # the press
    t.begin_frame(1010 + DELAY)                 # the first repeat, on time
    assert t.repeats("up") == 1
    t.begin_frame(1010 + DELAY + 4 * RATE + 10)     # a frame that took four
    assert t.repeats("up") == 4
    assert t.rep_word == 4 << 16, "up's count, packed in its byte"
    assert t.steps("up") == 4 and not t.pressed("up")


def test_a_second_key_repeats_on_its_own_delay_and_sources_do_not_add():
    t, s = _table()
    pad = t.source("pad")
    t.begin_frame(1000)
    s.set_button("down", True)
    t.begin_frame(1010)
    t.begin_frame(1200)
    s.set_button("left", True)                  # a second key, 190 ms later
    pad.set_button("down", True)                # and down held on a second source
    t.begin_frame(1210)
    t.begin_frame(1010 + DELAY)
    assert t.repeats("down") == 1, "the most a source owes, not the sum"
    assert t.repeats("left") == 0
    t.begin_frame(1210 + DELAY)
    assert t.repeats("left") == 1


def test_no_clock_repeats_nothing_and_clear_edges_starts_the_delay_over():
    t, s = _table()
    s.set_button("right", True)
    for _ in range(100):
        t.begin_frame()                         # no clock
        assert t.repeats("right") == 0
    t.begin_frame(1000)
    t.begin_frame(1000 + DELAY)
    assert t.repeats("right") == 1
    t.clear_edges()                             # a modal opened on the key
    assert t.repeats("right") == 0 and t.rep_word == 0
    t.begin_frame(1000 + DELAY + RATE)
    assert t.repeats("right") == 0, "the delay starts over under the modal"
    t.begin_frame(1000 + 2 * DELAY)
    assert t.repeats("right") == 1


def test_a_spent_or_cleared_edge_never_presses_a_held_button_again():
    """A surface that takes a frame's keys spends them: the held button stays
    held, so the next merge sees no new press, and with `spend_edges` its
    repeat keeps its schedule. A caret that took every frame's step through
    `clear_edges` stepped once a frame, the button re-pressing at each merge."""
    for spend in (True, False):
        t, s = _table()
        t.begin_frame(1000)
        s.set_button("down", True)
        now, steps = 1000, []
        for _ in range(60):
            now += 16
            t.begin_frame(now)
            steps.append((now - 1016, t.steps("down")))
            (t.spend_edges if spend else t.clear_edges)()
            assert t.steps("down") == 0 and not t.pressed("down") and t.held("down")
        assert steps[0] == (0, 1), "the press is a step"
        later = [at for at, n in steps[1:] if n]
        if spend:
            assert later and later[0] >= DELAY, "nothing before the delay"
            assert all(b - a <= RATE + 16 for a, b in zip(later, later[1:])), later
        else:
            assert not later, "each clear starts the delay over"


def test_repeats_enter_no_edge_no_key_and_no_mask():
    """Two tables fed the same holds, one repeating and one not: every
    read a cart makes -- the masks, the press edges, the key -- agrees."""
    on, s_on = _table()
    off, s_off = _table()
    off.set_repeat(DELAY, 0)
    now = 1000
    for f in range(120):
        for s in (s_on, s_off):
            s.set_button("down", 10 <= f < 90)
            s.set_button("a", 20 <= f < 70)
            s.last_key = ord("x") if 30 <= f < 100 else 0
            s.typing = ord("x") if 30 <= f < 100 else 0
        now += 16
        on.begin_frame(now)
        off.begin_frame(now)
        names = mi.NAMES
        assert on.button_masks(names) == off.button_masks(names), f
        assert on.last_key == off.last_key and on.key_event == off.key_event, f
        assert off.rep_word == 0 and off.key_repeats == 0
    assert on.repeat == (DELAY, RATE)


def test_the_typing_level_repeats_and_a_chord_does_not():
    t, s = _table()
    t.begin_frame(1000)
    s.typing = ord("q")
    got = []
    now = 1000
    for _ in range(60):
        now += 10
        t.begin_frame(now)
        got.append((t.key_repeats, t.repeat_key if t.key_repeats else 0, t.last_key))
    assert sum(n for n, _k, _l in got) == (600 - 10 - DELAY) // RATE + 1
    assert {k for n, k, _l in got if n} == {ord("q")}
    assert all(lk == 0 for _n, _k, lk in got), "the typing level is no key event"
    s.typing = mi.event(ord("z"), mi.CTRL)      # Ctrl+Z held: a shortcut
    for _ in range(80):
        now += 10
        t.begin_frame(now)
        assert t.key_repeats == 0
    s.typing = mi.KEY_DOWN                      # types no byte
    for _ in range(80):
        now += 10
        t.begin_frame(now)
        assert t.key_repeats == 0
    s.typing = mi.event(ord("Q"), mi.SHIFT)     # Shift is typing
    seen = set()
    for _ in range(60):
        now += 10
        t.begin_frame(now)
        if t.key_repeats:
            seen.add(t.repeat_key)
    assert seen == {ord("Q")}


def test_added_steps_are_this_frames_counts():
    t, _s = _table()
    t.begin_frame(1000)
    t.add_steps("down", 3)
    t.add_steps("left", 1)
    assert t.steps("down") == 3 and t.rep_word == 1 | 3 << 24
    assert not t.pressed("down")
    t.begin_frame(1016)
    assert t.rep_word == 0, "a frame's steps are that frame's"
    t.add_steps("up", 300)
    assert t.repeats("up") == 255, "saturated in its byte"


def test_board_toml_input_overrides_become_compile_definitions(tmp_path):
    from tools import board_config
    d = tmp_path / "b"
    d.mkdir()
    (d / "board.toml").write_text('[board]\nota = "x"\n[input]\nrepeat_rate_ms = 40\n'
                                  'ball_coast = 3\n')
    assert board_config.input_defines(d) == [("MOY_INPUT_REPEAT_RATE_MS", 40),
                                             ("MOY_INPUT_BALL_COAST", 3)]
    (d / "board.toml").write_text('[board]\nota = "x"\n[input]\nrepeat = 40\n')
    with pytest.raises(ValueError):
        board_config.input_defines(d)
    tdeck = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
    assert ("MOY_INPUT_BALL_COAST", 0) in board_config.input_defines(tdeck), \
        "the T-Deck's coast ships at 0 (scroll_and_text §17)"
    assert mi.ball_coast() == (0, 20)


# -- the T-Deck: its typing level, the driver executed ---------------------------

def test_tdeck_text_mode_types_a_held_key_at_the_rate():
    """The matrix types each key's press edge once (`last_key`, what a cart
    in text mode reads) and holds the key typed last as its source's typing
    level while it stays down, which the table repeats; a game's held key
    is no typing level."""
    from test_tdeck_input import _Kbd, _matrix
    fake = _Kbd([b"\x00"])
    table = mi.InputTable()
    drv = mi.KbdDriver(table, fake)
    drv.raw_mode = True
    table.text_mode = True
    now = 1000
    lk, reps = [], []
    for f in range(80):                         # 'h' held for ~1.2 s of 16 ms frames
        fake.frames = [_matrix("h") if 2 <= f < 77 else bytes(5)]
        drv.pass_()
        now += 16
        table.begin_frame(now)
        lk.append(table.last_key)
        reps.append(table.key_repeats)
        if table.key_repeats:
            assert table.repeat_key == ord("h")
    assert lk.count(ord("h")) == 1, "a text-mode cart sees the key once"
    held_ms = 75 * 16
    assert sum(reps) == (held_ms - 16 - DELAY) // RATE + 1, reps
    assert not any(reps[78:])
    table.text_mode = False                     # a game: the level is its key
    for f in range(60):
        fake.frames = [_matrix("h")]
        drv.pass_()
        now += 16
        table.begin_frame(now)
        assert table.key_repeats == 0


# -- the host and the browser: one driver, one repeat ----------------------------

def _ws(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(1 / 30)
    return ws, drv


def _trace(ws, drv, down, up, frames=60):
    """Each frame's (steps of down, key_repeats, repeat_key) while `down()`
    holds an arrow and a letter, then `up()` lets go."""
    out = []
    down()
    for f in range(frames):
        if f == frames - 10:
            up()
        drv.frame(1 / 30)
        i = ws.input
        out.append((i.steps("down"), i.key_repeats, i.repeat_key if i.key_repeats else 0))
    return out


def test_the_browsers_keyboard_repeats_as_the_hosts(tmp_path):
    host_ws, host = _ws(tmp_path / "h")
    web_ws, web = _ws(tmp_path / "w")

    def kd(code):
        return lambda: (web_input.apply_events(
            [{"type": "kd", "code": code, "mods": 0}], web.input, web.pointer,
            on_key_down=web.key_down, on_key_up=web.key_up))

    def ku(code):
        return lambda: web_input.apply_events(
            [{"type": "ku", "code": code, "mods": 0}], web.input, web.pointer,
            on_key_down=web.key_down, on_key_up=web.key_up)

    a = _trace(host_ws, host, lambda: host.key_down(mi.KEY_DOWN),
               lambda: host.key_up(mi.KEY_DOWN))
    b = _trace(web_ws, web, kd(mi.KEY_DOWN), ku(mi.KEY_DOWN))
    assert a == b
    steps = [s for s, _n, _k in a]
    assert steps[0] == 1 and sum(steps) > 6, "an arrow held steps at the table's rate"
    c = _trace(host_ws, host, lambda: host.key_down(ord("j")), lambda: host.key_up(ord("j")))
    d = _trace(web_ws, web, kd(ord("j")), ku(ord("j")))
    assert c == d
    assert sum(n for _s, n, _k in c) > 6 and {k for _s, n, k in c if n} == {ord("j")}
    assert not any(n for _s, n, _k in c[-8:]), "the key's release ends its repeat"


@pytest.mark.parametrize("code", [mi.KEY_DOWN, mi.KEY_RIGHT])
def test_a_held_arrow_steps_the_code_caret_at_the_tables_repeat(tmp_path, code):
    """The browser's arrow, held over the Code tab's caret: one step at the
    key's down, none until REPEAT_DELAY_MS, then one every REPEAT_RATE_MS --
    the decode path the page's `kd` takes, on a 60 fps clock."""
    ws, drv = _ws(tmp_path)
    for c in ws._runnable_items():
        if "star_catcher" in c.get("path", ""):
            ws.select_cart(c)
            break
    ws.open_in_editor()
    ws.set_menu_view("code")
    for _ in range(5):
        drv.frame(1 / 60)
    ed = ws.editor
    ed.row, ed.col = 0, 0
    send = lambda t: web_input.apply_events(  # noqa: E731
        [{"type": t, "code": code, "mods": 0}], drv.input, drv.pointer,
        on_key_down=drv.key_down, on_key_up=drv.key_up)
    send("kd")
    moved, last = [], (ed.row, ed.col)
    for f in range(60):                         # one second
        drv.frame(1 / 60)
        if (ed.row, ed.col) != last:
            moved.append(round(f * 1000 / 60))
            last = (ed.row, ed.col)
    send("ku")
    assert moved[0] == 0, moved
    assert DELAY - 17 <= moved[1] <= DELAY + 17, moved
    gaps = [b - a for a, b in zip(moved[1:], moved[2:])]
    assert gaps and all(RATE - 17 <= g <= RATE + 17 for g in gaps), moved


def test_the_page_forwards_no_os_autorepeat():
    """The table repeats on every head alike, so the page sends a key's
    down once, whatever the OS repeats."""
    page = (ROOT / "firmware" / "web_runner" / "page_core.html").read_text()
    sends = re.findall(r'[^;]*send\(\{type:"kd"[^;]*', page)
    assert sends and all("if(!e.repeat)send" in s for s in sends), sends


# -- carts: what they read is what they read -------------------------------------

_PY = ('n = [0, 0]\n'
       'def _update(dt):\n'
       '    if btnp("down"):\n'
       '        n[0] += 1\n'
       '    if keyp(109):\n'
       '        n[1] += 1\n'
       '    pmem(0, n[0])\n'
       '    pmem(1, n[1])\n'
       'def _draw():\n'
       '    cls(0)\n')
_LUA = ('local bp, kp = 0, 0\n'
        'function _update(dt)\n'
        '  if btnp("down") then bp = bp + 1 end\n'
        '  if keyp(109) then kp = kp + 1 end\n'
        '  pmem(0, bp)\n'
        '  pmem(1, kp)\n'
        'end\n'
        'function _draw() cls(0) end\n')


def _cart_reads(tmp_path, runtime, repeat, text):
    carts = str(tmp_path / ("%s%d%d" % (runtime, repeat, text)))
    ws = host_app.build_workstation(carts)
    src, main = (_PY, "main.py") if runtime == "python" else (_LUA, "main.lua")
    if text:
        src += ("def _init():\n    textmode(True)\n" if runtime == "python"
                else "function _init() textmode(true) end\n")
    moy_carts.create("Held", carts, type="game", edit=[], src=src, runtime=runtime,
                     main=main)
    ws.launcher.set_items(moy_carts.scan(carts))
    drv = host_app.ConsoleDriver(ws)
    if not repeat:
        ws.input.set_repeat(DELAY, 0)
    select_on_home(ws, "Held")
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    for _ in range(3):
        drv.frame(1 / 30)
    drv.key_down(mi.KEY_DOWN)
    drv.key_down(ord("m"))
    for _ in range(60):                         # two seconds held
        drv.frame(1 / 30)
    drv.key_up(ord("m"))
    drv.key_up(mi.KEY_DOWN)
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.player.cart_error is None, ws.player.cart_error
    if runtime == "lua":                        # the Lua tier's pmem is C-side
        from array import array
        from runtime import moycore
        img = array("i", [0] * 256)
        moycore.pmem_image(img)
        return img[0], img[1]
    return ws.pmem.cell(0), ws.pmem.cell(1)


@pytest.mark.parametrize("runtime", ["python", "lua"])
@pytest.mark.parametrize("text", [False, True])
def test_a_carts_btnp_and_keyp_never_see_a_repeat(tmp_path, runtime, text):
    on = _cart_reads(tmp_path, runtime, True, text)
    off = _cart_reads(tmp_path, runtime, False, text)
    assert on == off
    if text and runtime == "python":
        assert on[1] == 1, "a held key is one keyp in text mode"
    else:
        assert on[0] == 1, "a held button is one btnp"


# -- the verbs engine: a roll's steps, all of them, within one glide ---------------

@pytest.fixture
def th(tmp_path):
    from ws_helpers import build_ws
    return build_ws(Path(tmp_path)).theme_colors


@pytest.mark.parametrize("kind", ("page", "table"))
def test_a_fast_roll_delivers_every_step_within_one_glide(th, kind):
    """A roll of 30 steps at two a frame (the REP word, `add_steps`) moves
    the focus 30 stops, as 30 presses do, and the view rests on it one glide
    after the roll's last frame."""
    from test_verbs_motion import _DOWN, _ready, Page
    from runtime import ui_draw
    a = _ready(Page(th, kind, dt=33))
    b = _ready(Page(th, kind, dt=0))
    a.frame(_DOWN)
    b.frame(_DOWN)
    for _ in range(15):
        a.host.inp[ui_draw._IN_REP] = 2 << 24
        a.frame()
    a.host.inp[ui_draw._IN_REP] = 0
    for _ in range(30):
        b.frame(_DOWN)
    frames = a.settle()
    assert frames <= 12, "the view rests one glide after the roll: %d frames" % frames
    focus = ui_draw._C.api_focus
    assert focus(a.host.ctx, None) == focus(b.host.ctx, None)
    if kind == "table":
        assert a.cur == b.cur and a.cur >= 30
    a.frame()
    b.frame()
    assert a.offset() == b.offset() > 0
    assert a.pixels() == b.pixels(), "30 steps at rest are 30 presses at rest"


def test_a_held_keys_repeats_type_into_a_field(th):
    """A field takes the typing repeats (the KEYREP word) as more of the
    key; Enter's repeats commit nothing."""
    from runtime import ui, ui_draw
    from runtime.host_canvas import make_system_canvas
    cv = make_system_canvas(320, 240)
    h = ui_draw.AppHost()
    state = {"text": "", "done": 0}

    def frame(key=0, rep=0):
        ui_draw.frame_dt(33)
        h.inp[5] = key
        h.inp[ui_draw._IN_KEYREP] = rep
        h.begin(cv, th, (0, 0, 320, 240))
        ui.page("Field")
        state["text"], done = ui.field("name", state["text"])
        state["done"] += done
        ui.end()

    for _ in range(3):
        frame()
    ui_draw._C.api_focus(h.ctx, "name")
    frame()
    frame(key=ord("a"))
    frame(rep=ord("a") | 3 << 16)
    assert state["text"] == "aaaa"
    frame(rep=8 | 2 << 16)
    assert state["text"] == "aa"
    frame(rep=13 | 5 << 16)
    assert state["done"] == 0, "a held Enter commits nothing on its repeats"


def test_ball_coast_ships_off_and_carries_a_fast_roll_on_when_set(tmp_path, monkeypatch):
    """BALL_COAST 0 (the shipped value) delivers every step as a step; set,
    the steps a roll makes over BALL_COAST_RATE a second become a coast that
    keeps stepping after the roll and decays to rest."""
    from runtime import console
    from test_trackball_keys import _home
    ws, _drv = _home(tmp_path)
    t = [100000]
    monkeypatch.setattr(console, "_ticks_ms", lambda: t[0])
    got = []
    ws.input.add_steps = lambda name, k: got.append((name, k))
    assert ws._coast == 0
    ws._ball_rate_at = t[0] - 100
    ws._ball_steps("right", 6)                  # 60 a second
    assert got == [("right", 6)] and not ws._coast_r.flinging
    ws._coast, ws._coast_rate = 100, 20
    got.clear()
    ws._ball_rate_at = t[0]
    t[0] += 100
    ws._ball_steps("right", 6)                  # 2 a 100 ms at 20/s; 4 over
    assert got == [("right", 2)]
    assert ws._coast_r._vel / ws.COAST_UNIT == pytest.approx(0.4)    # steps a ms
    per = []
    ws._frame_dt_ms = 33.0                      # the loop's dt, which the coast takes
    for _ in range(400):
        t[0] += 33
        got.clear()
        ws.ball(0, 0)
        per.append(sum(k for _n, k in got))
        if not ws.ball_owed:
            break
    assert not ws.ball_owed, "the coast comes to rest"
    assert per[0] == 0, "the frame a roll feeds it, the coast does not move"
    assert per[1] > 0 and sum(per) > 10, per
    assert per[1] > max(per[len(per) // 2:]), "it decays"


def test_a_ble_keyboards_held_key_repeats_as_typing_and_its_arrow_as_steps():
    """The BLE central's held key is its source's typing level too
    (moy_hid.c, executed over the fake stack), and its held arrow a held
    button: both repeat at the table's rate, neither in a key event."""
    from test_ble_keyboard import _machine, _ready
    table, _stack, m = _machine(cart=False)
    _ready(m)
    table.text_mode = True
    now = 1000
    reps = []
    m.notify(3, 3, b"\x00\x00\x1a\x00\x00\x00\x00\x00")      # W held
    for _ in range(60):
        m.frame()
        now += 16
        table.begin_frame(now)
        reps.append((table.last_key, table.key_repeats, table.repeat_key))
    assert sum(n for _l, n, _k in reps) == (59 * 16 - DELAY) // RATE + 1
    assert {k for _l, n, k in reps if n} == {ord("w")}
    m.notify(3, 3, b"\x00\x00\x51\x00\x00\x00\x00\x00")      # Down arrow held
    steps = []
    for _ in range(60):
        m.frame()
        now += 16
        table.begin_frame(now)
        steps.append(table.steps("down"))
        assert table.key_repeats == 0, "an arrow types no byte"
    assert steps[0] == 1 and sum(steps) == 1 + (59 * 16 - DELAY) // RATE + 1
    m.notify(3, 3, bytes(8))
    m.frame()
    table.begin_frame(now + 16)
    assert table.key_repeats == 0 and table.rep_word == 0
