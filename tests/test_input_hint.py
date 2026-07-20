"""#42 Thread 3 -- the unified input model.

Two concrete deliverables, per the issue's Thread 3 (docs/issues/open/0042-...):

  (a) A cart manifest hint ("input": ["buttons"|"touch"|"keyboard", ...]) so a
      surface can show only the controls a cart actually reads (e.g. the web
      view's virtual gamepad only when the cart uses buttons). Optional,
      absent/invalid -> None, and every consumer treats None as "show
      everything" -- zero regression for a cart that never declares it.
      Threaded end-to-end: manifest.json -> moy_carts.load()/seed_builtins() ->
      tools/gen_device_carts.py (the device seed generator) -> the shared
      web_view.assets_payload() -> the browser page's applyInputHint().

  (b) ONE shared source<->cart-API mapping the host web console and the device
      web view both consult (runtime.web_view.BUTTON_NAMES + apply_events)
      instead of two hand-rolled tables -- tools/web_console.py used to carry
      its own duplicate BUTTON_NAMES + a second hand-written event decoder;
      it now calls the SAME runtime.web_view.apply_events the device's
      moy_webserver re-exports.

Also locks in the mirrored device-side fix for the browser-typed-key collapse
device_webview.feed_input had (last_key = _key_queue[-1] then wipe the whole
queue -- dropped every character but the last in a multi-char WS batch), which
the issue calls out as a known follow-up to the host ConsoleDriver.type_char
fix. Device-side is source-grepped, not executed (test_micropython_spike.py's
pattern): device_webview.py imports device-only modules (console/device_util/
device_wifi) not importable on the host, and NEEDS AN ON-GLASS VERIFICATION
PASS -- see the module's comment.
"""

import json
import os

from runtime import moy_carts
from runtime import web_view
from tools import web_console


SYSTEM_CARTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "system_carts")
FW_MODULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "firmware", "lilygo_t_deck_plus_micropython", "modules")


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
    assert carts["Battle City"]["input"] == ["buttons"]
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
        ("battle_city", "Battle City", ["buttons"], 7),
        ("letter_blitz", "Letter Blitz", ["keyboard", "touch"], 13),
    ):
        man = json.loads(open(os.path.join(SYSTEM_CARTS, folder + ".moy", "manifest.json"),
                              encoding="utf-8").read())
        assert man["title"] == title
        assert man["input"] == kinds
        assert man["version"] >= min_version


# ---------------------------------------------------------------------------
# (a) end-to-end: the shared assets_payload carries the hint to both surfaces
# ---------------------------------------------------------------------------


def test_assets_payload_input_field_defaults_to_none_and_round_trips():
    a = web_view.assets_payload(320, 240, [[0, 0, 0]] * 64, None, None, None)
    assert a["input"] is None
    b = web_view.assets_payload(320, 240, [[0, 0, 0]] * 64, None, None, "T",
                                input_kinds=("touch",))
    assert b["input"] == ["touch"]
    import json as _json
    _json.dumps(b)                              # must stay wire-serializable


def _open_by_title(console, title):
    for i, c in enumerate(console.ws.launcher.items):
        if c["title"] == title:
            console.ws.launcher.sel = i
            break
    console.ws.open()
    assert console.ws.screen == "desktop" and console.ws.cart_error is None, title


def test_web_console_assets_carries_the_open_carts_input_hint(tmp_path):
    """The HOST /assets path (tools/web_console.py): the launcher (no cart open)
    ships input=None; opening a cart that declares a hint ships that hint over
    the wire, so the browser can gate its virtual controls per cart."""
    launcher_console = web_console.WebConsole(str(tmp_path / "carts_launcher"), fps=30)
    assert launcher_console.assets()["input"] is None    # launcher: no open cart

    touch_console = web_console.WebConsole(str(tmp_path / "carts_touch"), fps=30)
    _open_by_title(touch_console, "Tap Only Red")
    assert touch_console.assets()["input"] == ["touch"]

    buttons_console = web_console.WebConsole(str(tmp_path / "carts_buttons"), fps=30)
    _open_by_title(buttons_console, "Battle City")
    assert buttons_console.assets()["input"] == ["buttons"]

    keyboard_console = web_console.WebConsole(str(tmp_path / "carts_keyboard"), fps=30)
    _open_by_title(keyboard_console, "Letter Blitz")
    assert keyboard_console.assets()["input"] == ["keyboard", "touch"]


def test_device_webview_assets_passes_the_input_hint_source_wired():
    """Device-side wiring is source-grepped (device_webview.py imports device-only
    modules): assets() must read the open cart's "input" and forward it as
    assets_payload's input_kinds= kwarg -- the SAME shared builder the host uses."""
    text = open(os.path.join(FW_MODULES, "device_webview.py"), encoding="utf-8").read()
    assert 'input_kinds = cart.get("input") if cart else None' in text
    assert "decoded or None, input_kinds)" in text


# ---------------------------------------------------------------------------
# (a) the browser page gates its virtual controls on the hint
# ---------------------------------------------------------------------------


def test_page_gates_virtual_gamepad_and_soft_keyboard_on_the_input_hint():
    text = web_view.PAGE_HTML
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


def test_host_web_console_no_longer_hand_rolls_its_own_button_table():
    """tools/web_console.py used to define its own BUTTON_NAMES frozenset +
    per-event decode loop, duplicating runtime/web_view.py's BUTTON_NAMES +
    apply_events (which the device's moy_webserver re-exports and drives).
    That duplicate must be gone; the host now calls the shared function."""
    assert not hasattr(web_console, "BUTTON_NAMES")
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "web_console.py"), encoding="utf-8").read()
    assert "web_view.apply_events(" in src


def test_web_console_apply_events_still_gates_unknown_button_names(tmp_path):
    """Behaviour parity after the refactor: BUTTON_NAMES membership is still
    enforced (via the shared table), so a stray/unknown name can't wedge a held
    button forever."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30)
    console.apply_events([{"type": "hold", "name": "nonsense", "down": True}])
    assert console.ws.input.held("nonsense") is False
    console.apply_events([{"type": "hold", "name": "left", "down": True}])
    assert console.ws.input.held("left") is True
    console.apply_events([{"type": "hold", "name": "left", "down": False}])


def test_web_console_button_names_identical_to_shared_table():
    """Pin the ONE table both transports read (runtime.web_view.BUTTON_NAMES) --
    the launcher-nav + gameplay logical names."""
    assert set(web_view.BUTTON_NAMES) == {"left", "right", "up", "down", "a", "b",
                                          "run", "home"}


# ---------------------------------------------------------------------------
# The device-side input-collapse fix (mirrors the host ConsoleDriver.type_char
# queue fix): device_webview.feed_input must pop ONE key per frame, not take
# last_key = _key_queue[-1] then wipe the whole queue.
# ---------------------------------------------------------------------------


def test_device_webview_key_queue_pops_one_per_frame_not_last_wins():
    text = open(os.path.join(FW_MODULES, "device_webview.py"), encoding="utf-8").read()
    assert "self._inp.last_key = self._key_queue.pop(0)" in text
    # The old collapse pattern (take the last char, then wipe the queue) must be gone.
    assert "self._key_queue[-1]" not in text
    assert "NEEDS AN ON-GLASS" in text            # the fix is flagged as unverified on hardware
