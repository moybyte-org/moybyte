"""#42 Thread 3 -- the unified input model.

Two concrete deliverables, per the issue's Thread 3 (docs/issues/open/0042-...):

  (a) A cart manifest hint ("input": ["buttons"|"touch"|"keyboard", ...]) so a
      surface can show only the controls a cart actually reads (e.g. the web
      view's virtual gamepad only when the cart uses buttons). Optional,
      absent/invalid -> None, and every consumer treats None as "show
      everything" -- zero regression for a cart that never declares it.
      Threaded end-to-end: manifest.json -> moy_carts.load()/seed_builtins() ->
      tools/gen_device_carts.py (the device seed generator) ->
      web_input.effective_input_kinds() -> the runner page's applyInputHint().

  (b) ONE shared source<->cart-API mapping every input consumer reads
      (runtime.web_input.BUTTON_NAMES + apply_events). The two streaming
      transports that used to consult it (tools/web_console.py + the device
      web view) died in the 2026-08 streaming sunset; the surviving consumer
      is the wasm head's worker input pump (web_boot), and the table/decoder
      pins below keep it honest.
"""

import json
import os

from runtime import moy_carts
from runtime import web_input


SYSTEM_CARTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "system_carts")


# ---------------------------------------------------------------------------
# (a) the manifest input hint: normalize / load / seed_builtins / codegen
# ---------------------------------------------------------------------------


def test_normalize_input_kinds_default_and_filtering():
    assert moy_carts._normalize_input_kinds(None) is None
    assert moy_carts._normalize_input_kinds("touch") is None          # not a list
    assert moy_carts._normalize_input_kinds([]) is None                # empty -> None
    assert moy_carts._normalize_input_kinds(["bogus"]) is None         # nothing valid
    assert moy_carts._normalize_input_kinds(["touch", "bogus"]) == ("touch",)
    assert moy_carts._normalize_input_kinds(["buttons", "keyboard"]) == ("buttons", "keyboard")


def test_cart_without_hint_defaults_to_none_zero_regression(tmp_path):
    """A cart with no "input" key in its manifest -- the overwhelming majority,
    every pre-#42 cart -- loads with input=None, which every consumer (below)
    treats as "show every control" (today's behaviour, unchanged)."""
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    cart = moy_carts.create("Plain", carts_dir, src="def _draw():\n    cls(0)\n")
    assert cart["input"] is None


def test_manifest_input_hint_round_trips_through_load(tmp_path):
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    cart = moy_carts.create("Hinted", carts_dir, src="def _draw():\n    cls(0)\n")
    man_path = cart["path"] + "/manifest.json"
    man = json.loads(open(man_path, encoding="utf-8").read())
    man["input"] = ["touch", "buttons"]
    open(man_path, "w", encoding="utf-8").write(json.dumps(man))
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["input"] == ("touch", "buttons")


def test_seed_builtins_writes_input_hint_when_seed_declares_it(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    seed = [{"title": "Seed", "type": "game", "version": 1,
            "src": "def _draw():\n    cls(0)\n", "cfg": {}, "edit": [],
            "input": ["touch"]}]
    moy_carts.seed_builtins(seed, root)
    [cart] = moy_carts.scan(root)
    assert cart["input"] == ("touch",)


def test_seed_builtins_omits_input_when_seed_has_none(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    seed = [{"title": "Seed", "type": "game", "version": 1,
            "src": "def _draw():\n    cls(0)\n", "cfg": {}, "edit": []}]
    moy_carts.seed_builtins(seed, root)
    [cart] = moy_carts.scan(root)
    assert cart["input"] is None


def test_gen_device_carts_carries_the_input_hint_from_manifest():
    """The device build's codegen (tools/gen_device_carts.py, which turns
    system_carts/*.moy into the embedded CARTS list moy_runtime.seed_builtins()
    writes to SD) must thread "input" through exactly like "permissions"."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(SYSTEM_CARTS), "tools"))
    import gen_device_carts
    carts = {c["title"]: c for c in gen_device_carts.build_carts(SYSTEM_CARTS)}
    assert carts["Tap Only Red"]["input"] == ["touch"]
    assert carts["Brick Siege"]["input"] == ["buttons"]
    assert carts["Letter Blitz"]["input"] == ["keyboard", "touch"]
    # A cart that never declares the hint carries no "input" key at all (matches
    # the "permissions" precedent: absent, not an empty list).
    assert "input" not in carts["Sakura"]


def test_seed_system_carts_have_a_bumped_version_and_declared_kinds_match_their_api():
    """The three carts chosen to MODEL the hint (#42 Thread 3): a touch-only game,
    a buttons-only game, and a textmode()+touch game -- each manifest version is
    bumped (#47) so an already-seeded device/host picks up the new hint."""
    for folder, title, kinds, min_version in (
        ("tap_red", "Tap Only Red", ["touch"], 5),
        ("brick_siege", "Brick Siege", ["buttons"], 7),
        ("letter_blitz", "Letter Blitz", ["keyboard", "touch"], 13),
    ):
        man = json.loads(open(os.path.join(SYSTEM_CARTS, folder + ".moy", "manifest.json"),
                              encoding="utf-8").read())
        assert man["title"] == title
        assert man["input"] == kinds
        assert man["version"] >= min_version


# ---------------------------------------------------------------------------
# (a) the browser page gates its virtual controls on the hint
# ---------------------------------------------------------------------------


def test_page_gates_virtual_gamepad_and_soft_keyboard_on_the_input_hint():
    # The page is a plain file in the build that ships it since moycore stage 4
    # (it was generated from a Python string back when three transports shared
    # it, and the last two of those are gone).
    text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "firmware", "web_runner", "page_core.html"),
                encoding="utf-8").read()
    assert "INPUT=a.input||null" in text
    assert "function applyInputHint()" in text
    assert 'INPUT.indexOf("buttons")>=0' in text
    assert 'INPUT.indexOf("keyboard")>=0' in text
    assert "function syncCtl()" in text
    # applyInputHint() must run on every /assets fetch (i.e. every cart change),
    # not just once at page load, so switching carts re-gates the controls.
    assert "INPUT=a.input||null;applyInputHint();" in text


# ---------------------------------------------------------------------------
# (b) ONE shared source<->cart-API mapping, not two hand-rolled tables
# ---------------------------------------------------------------------------


def test_apply_events_gates_unknown_button_names():
    """BUTTON_NAMES membership is enforced in the ONE shared decoder
    (web_view.apply_events -- the wasm head's input path), so a stray/unknown
    name can't wedge a held button forever."""
    held = {}

    class Input:
        def set_button(self, name, down):
            held[name] = down

    class Pointer:
        pass

    inp, ptr = Input(), Pointer()
    web_input.apply_events([{"type": "hold", "name": "nonsense", "down": True}],
                          inp, ptr)
    assert "nonsense" not in held
    web_input.apply_events([{"type": "hold", "name": "left", "down": True}],
                          inp, ptr)
    assert held.get("left") is True


def test_button_names_pinned_to_the_shared_table():
    """Pin the ONE table every input consumer reads (runtime.web_input
    .BUTTON_NAMES) -- the launcher-nav + gameplay logical names."""
    assert set(web_input.BUTTON_NAMES) == {"left", "right", "up", "down", "a", "b",
                                          "run", "home"}


def test_button_names_is_the_host_input_state_table_itself():
    """Not a second copy of it. A ninth OS button added to InputState.BUTTONS
    used to be dropped silently by the browser: apply_events gates `hold` events
    on BUTTON_NAMES membership, so an unlisted name is discarded as junk with no
    error anywhere."""
    from runtime.input import InputState
    assert web_input.BUTTON_NAMES is InputState.BUTTONS


def test_a_new_host_button_reaches_the_browser_decoder(monkeypatch):
    from runtime.input import InputState
    monkeypatch.setattr(InputState, "BUTTONS", InputState.BUTTONS + ("menu",))
    import importlib
    importlib.reload(web_input)
    try:
        held = {}

        class Input:
            def set_button(self, name, down):
                held[name] = down

        web_input.apply_events([{"type": "hold", "name": "menu", "down": True}],
                               Input(), object())
        assert held.get("menu") is True
    finally:
        monkeypatch.undo()             # before the reload, or the 9th name sticks
        importlib.reload(web_input)


def test_the_board_input_state_is_deliberately_not_this_table():
    """The device tier's BUTTONS is 15 names in libmoy's enum order and asserting
    the two tiers equal would be WRONG -- tests/test_moy_button_order.py is the
    file that explains why. This pins that web_input took the HOST one."""
    assert len(web_input.BUTTON_NAMES) == 8
    assert web_input.BUTTON_NAMES[:4] == ("left", "right", "up", "down")
