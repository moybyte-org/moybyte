"""Brick Siege and Harpoon Pop, played by two (#65).

Both carts were written with a multiplayer hook and this is it being used. They
are driven through the SHARED console -- the same path the boards run -- once
with nobody joined and once with a second controller, so the two claims that
matter are checked rather than asserted:

  1. a console nobody joined plays the single-player game VERBATIM. This is the
     regression that would matter most and be noticed least, because a kid
     playing alone never reads a release note.
  2. each tank / each harpooner answers to ITS OWN pad, and to no other.

Where the second player comes from is deliberately not modelled here: the carts
read `btn(name, i)` and a PlayerRouter slot is a PlayerRouter slot, whether it is
half a T-Deck keyboard or another console over the radio.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from ws_helpers import open_cart as _open_cart  # noqa: E402

TWO_PLAYER_CARTS = ("Brick Siege", "Harpoon Pop")


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _run(ws, frames, dt=1 / 30):
    for _ in range(frames):
        ws.input.begin_frame()
        ws.frame(dt)


def _join(ws, index=1):
    """A second controller appears. This is all it takes -- no transport, no
    session, no netcode: a router slot IS a player."""
    return ws.input.players.add_player(index)


def _hold(slot, *names):
    for n in ("left", "right", "up", "down", "a", "b", "run"):
        slot.set_held(n, n in names)


# -- nobody joined: the single-player game, unchanged -----------------------

def test_one_player_is_still_one_player(tmp_path):
    ws = _ws(tmp_path)
    for title in TWO_PLAYER_CARTS:
        _open_cart(ws, title)
        _run(ws, 4)
        assert ws.cart_error is None, "%s: %s" % (title, ws.cart_error)
        assert ws.ns["players"]() == 1


def test_a_cart_roster_must_not_shadow_the_players_verb(tmp_path):
    """Brick Siege used to keep its tank list in a global called `players`,
    which is the NAME OF THE API VERB that reports how many there are -- so the
    moment the cart asked, it called a list and the run died. Caught here, and
    worth keeping: it is the kind of collision a kid will hit too."""
    ws = _ws(tmp_path)
    for title in TWO_PLAYER_CARTS:
        _open_cart(ws, title)
        _run(ws, 2)
        assert callable(ws.ns["players"]), "%s shadowed players()" % title


def test_brick_siege_alone_fields_one_tank(tmp_path):
    ws = _ws(tmp_path)
    _open_cart(ws, "Brick Siege")
    _run(ws, 4)
    assert ws.cart_error is None
    assert len(ws.ns["tanks"]) == 1
    assert ws.ns["tanks"][0][6] == 0, "the one tank is player one's"


def test_harpoon_pop_alone_fields_one_hunter(tmp_path):
    ws = _ws(tmp_path)
    _open_cart(ws, "Harpoon Pop")
    _run(ws, 4)
    assert ws.cart_error is None
    assert len(ws.ns["hunters"]) == 1
    hu = ws.ns["hunters"][0]
    assert hu[ws.ns["H_I"]] == 0
    # Alone, you start in the middle -- exactly where you always did.
    assert abs(hu[ws.ns["H_X"]] - (320 / 2 - ws.ns["PW"] / 2)) < 0.01


# -- a second controller joins ---------------------------------------------

def test_brick_siege_fields_a_second_tank_when_somebody_joins(tmp_path):
    ws = _ws(tmp_path)
    _join(ws)
    _open_cart(ws, "Brick Siege")
    _run(ws, 4)
    assert ws.cart_error is None
    tanks = ws.ns["tanks"]
    assert len(tanks) == 2
    assert [t[6] for t in tanks] == [0, 1]
    assert tanks[0][0] != tanks[1][0], "two kids must not start on one square"
    # Player two is drawn in the blue tank set, which had to be generated --
    # the sheet only ever carried the one blue tile.
    assert ws.ns["TANK_SET"] == (ws.ns["P_TANK"], ws.ns["P2_TANK"])
    assert ws.ns["P2_TANK"] == (15, 16, 17, 18)


def test_each_brick_siege_tank_answers_only_its_own_pad(tmp_path):
    ws = _ws(tmp_path)
    slot = _join(ws)
    _open_cart(ws, "Brick Siege")
    _run(ws, 2)
    tanks = ws.ns["tanks"]
    p1x, p2x = tanks[0][0], tanks[1][0]

    # Player two drives LEFT; player one is not touched.
    _hold(slot, "left")
    _run(ws, 6)
    assert tanks[1][0] < p2x, "player two moved"
    assert tanks[0][0] == p1x, "and player one did NOT"

    # Now player one drives RIGHT off the local console's own controls.
    _hold(slot)
    ws.input.set_held("right", True)
    _run(ws, 6)
    assert tanks[0][0] > p1x, "player one moved"


def test_brick_siege_gives_each_kid_their_own_bullet(tmp_path):
    """Sharing one shot between two players makes each of them feel like their
    own trigger is broken, because the other one's bullet eats it."""
    ws = _ws(tmp_path)
    slot = _join(ws)
    _open_cart(ws, "Brick Siege")
    _run(ws, 2)
    bullets = ws.ns["bullets"]
    del bullets[:]
    tanks = ws.ns["tanks"]
    ws.ns["_fire"](tanks[0], 0, 0)
    ws.ns["_fire"](tanks[1], 0, 1)
    mine = [b for b in bullets if b[3] == 0]
    assert len(mine) == 2, "both players got their shot off"
    assert sorted(b[4] for b in mine) == [0, 1]
    assert slot is not None


def test_harpoon_pop_fields_a_second_hunter_who_starts_apart(tmp_path):
    ws = _ws(tmp_path)
    _join(ws)
    _open_cart(ws, "Harpoon Pop")
    _run(ws, 4)
    assert ws.cart_error is None
    hunters = ws.ns["hunters"]
    assert len(hunters) == 2
    hx = ws.ns["H_X"]
    assert hunters[0][hx] < hunters[1][hx], "one third in from each wall"
    assert ws.ns["BODY"][0] != ws.ns["BODY"][1], "and told apart by colour"


def test_each_harpoon_pop_hunter_answers_only_its_own_pad(tmp_path):
    ws = _ws(tmp_path)
    slot = _join(ws)
    _open_cart(ws, "Harpoon Pop")
    _run(ws, 2)
    hunters = ws.ns["hunters"]
    hx = ws.ns["H_X"]
    a0, b0 = hunters[0][hx], hunters[1][hx]

    _hold(slot, "left")
    _run(ws, 6)
    assert hunters[1][hx] < b0
    assert hunters[0][hx] == a0

    _hold(slot)
    ws.input.set_held("right", True)
    _run(ws, 6)
    assert hunters[0][hx] > a0


def test_each_harpoon_pop_hunter_has_their_own_rope(tmp_path):
    ws = _ws(tmp_path)
    _join(ws)
    _open_cart(ws, "Harpoon Pop")
    _run(ws, 2)
    hunters = ws.ns["hunters"]
    rope = ws.ns["H_ROPE"]
    ws.ns["_fire"](hunters[0])
    assert hunters[0][rope] is not None
    assert hunters[1][rope] is None, "one kid firing must not spend the other's shot"
    ws.ns["_fire"](hunters[1])
    assert hunters[1][rope] is not None


def test_harpoon_pop_is_co_op_and_ends_only_when_everybody_is_out(tmp_path):
    ws = _ws(tmp_path)
    _join(ws)
    _open_cart(ws, "Harpoon Pop")
    _run(ws, 2)
    hunters = ws.ns["hunters"]
    lives, dead = ws.ns["H_LIVES"], ws.ns["H_DEAD"]

    hunters[0][lives] = 1
    ws.ns["_lose_life"](hunters[0])
    assert hunters[0][lives] == 0
    assert ws.ns["over"] == 0.0, "one player down is not game over"
    assert hunters[0][dead] == ws.ns["OUT"]

    hunters[1][lives] = 1
    ws.ns["_lose_life"](hunters[1])
    _run(ws, 1)
    assert ws.ns["over"] > 0.0, "both down IS"


def test_the_carts_run_a_two_player_frame_without_error(tmp_path):
    """The whole point, end to end: open each cart with a second controller
    joined and drive real frames, drawing included."""
    ws = _ws(tmp_path)
    slot = _join(ws)
    for title in TWO_PLAYER_CARTS:
        _open_cart(ws, title)
        assert ws.ns["players"]() == 2, title
        _hold(slot, "right", "a")
        _run(ws, 20)
        assert ws.cart_error is None, "%s: %s" % (title, ws.cart_error)
        _hold(slot)


def test_both_carts_declare_the_multiplayer_permission(tmp_path):
    """Two-console play is gated on it, and a cart that forgot it would link
    with nobody while looking perfectly fine on one console."""
    import json
    for folder in ("brick_siege", "harpoon_pop"):
        man = json.loads(
            (ROOT / "system_carts" / (folder + ".moy") / "manifest.json")
            .read_text(encoding="utf-8"))
        assert "multiplayer" in man["permissions"], folder
