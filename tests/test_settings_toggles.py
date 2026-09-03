"""The settings-toggle registry (#209, docs/history/console_architecture_2026-08.md 7).

Adding an ON/OFF setting used to walk FIVE hand-kept sites, and crisp_pixels
walked every one of them in 2026-08: the `ws.set_*` verb, the boot apply in
`load_system`, `_SETTINGS_ROWS` plus its capability splice, the tap-dispatch
branch, and the dev channel's serial word. Four of the five are now
`settings_layer.SETTINGS_TOGGLES`, and the fifth -- the verb -- is what the
registry deliberately does NOT absorb, because the phase reset, the canvas hook
and the keyboard hand-over are each toggle's own behaviour.

This file is the ratchet, and it is the `test_skin.py` shape: it pins the
registry as the single declaration point in BOTH directions -- a toggle cannot
exist without an entry, and the sites it replaced cannot re-grow. Three things
it refuses beyond that:

1. **The capability gates stay EXPRESSED.** A board that cannot serve a toggle
   hides the row and declines the serial word; it never silently keeps a flag
   nothing reads. `set_two_player` is the honest end of the same rule and
   reports OFF whatever it is told when no second keyboard exists.
2. **The flat mirrors stay FLAT.** `frame`'s pace check reads `self.frameskip`
   every loop iteration on all three boards, and both WMs read `ws.show_fps`
   per painted game frame. The registry owns declaration, persistence and the
   row -- never a read path.
3. **The row block keeps its place.** It sits directly after EDIT ICONS, in
   registry order, which is exactly where the hand-spliced rows were: the 87
   shell goldens and the ~300 Settings sub-surface hashes are the pixel proof,
   and this file pins the ORDER those hashes rest on.
"""

import ast
import inspect
from pathlib import Path

from runtime.settings_layer import SETTINGS_TOGGLES

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "runtime" / "console.py"
SETTINGS = ROOT / "runtime" / "settings_layer.py"
DEV = ROOT / "runtime" / "dev_channel.py"

KEYS = [t[0] for t in SETTINGS_TOGGLES]
SETTERS = [t[3] for t in SETTINGS_TOGGLES]

# The modules allowed to know the registry exists, and why. The other
# direction is checked too: a name here that has stopped importing it is dead
# wiring, which is the failure `test_skin` was written after finding.
OWNERS = {
    "settings_layer.py": "declares it, and draws the rows from it",
    "console.py": "the flat defaults, the boot apply, the persistence tail",
    "dev_channel.py": "the serial words",
}

# `dev_channel.py` still names ONE setter by hand, and that is the decision
# rather than an oversight: `diag` is not a plain toggle over there -- it drives
# perf_capture and the FPS chip along with the gate -- so its entry declares no
# serial word and the command stays written out.
DEV_SETTER_EXEMPT = {"set_diag_live"}


def _ws(tmp_path, **kw):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def _workstation_ast():
    tree = ast.parse(CONSOLE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Workstation":
            return node
    raise AssertionError("Workstation is not a class in runtime/console.py")


def _method(name):
    for fn in _workstation_ast().body:
        if isinstance(fn, ast.FunctionDef) and fn.name == name:
            return fn
    raise AssertionError("Workstation has no %s" % name)


def _string_constants(node):
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


# -- the table itself ---------------------------------------------------------

def test_the_registry_shape_is_uniform():
    """Six fields, one meaning each. A malformed entry would fail somewhere
    far away (a row with no label, a boot apply calling a string), so it fails
    here instead."""
    assert SETTINGS_TOGGLES, "the registry is empty"
    seen_keys = set()
    seen_dev = set()
    for entry in SETTINGS_TOGGLES:
        assert len(entry) == 6, entry
        key, label, default, setter, gate, dev = entry
        assert isinstance(key, str) and key
        assert isinstance(label, str) and label == label.upper()
        assert default is True or default is False, key
        assert isinstance(setter, str) and setter.startswith("set_")
        assert gate is None or callable(gate), key
        assert dev is None or (isinstance(dev, str) and dev), key
        assert key not in seen_keys, key
        seen_keys.add(key)
        if dev is not None:
            assert dev not in seen_dev, dev
            seen_dev.add(dev)


def test_every_entry_resolves_on_a_real_console(tmp_path):
    """key IS the flat attribute IS the system.json key, and setter IS a verb.
    All three were the same string six times over before the registry; this is
    what turns that coincidence into a contract."""
    ws = _ws(tmp_path)
    for key, _label, default, setter, _gate, _dev in SETTINGS_TOGGLES:
        assert hasattr(ws, key), key
        assert isinstance(getattr(ws, key), bool), key
        assert isinstance(default, bool)
        verb = getattr(ws, setter, None)
        assert callable(verb), setter
        sig = inspect.signature(verb)
        assert list(sig.parameters) == ["on", "persist"], setter
        assert sig.parameters["persist"].default is True, setter


def test_the_registry_is_the_only_place_a_toggle_can_be_declared():
    """The completeness ratchet, structurally rather than by name list: a
    Workstation method taking exactly `(self, on, persist=True)` IS a settings
    toggle, and every one of them must be in the table. Writing a seventh
    setter and forgetting the entry turns this red."""
    shaped = set()
    for fn in _workstation_ast().body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        names = [a.arg for a in fn.args.args]
        if names == ["self", "on", "persist"] and len(fn.args.defaults) == 1:
            shaped.add(fn.name)
    assert shaped == set(SETTERS), sorted(shaped ^ set(SETTERS))


def test_the_wiring_is_exactly_three_modules():
    """Both directions, the `test_skin` shape. An unlisted importer is a
    seventh site growing back; a listed one that stopped importing is dead
    wiring in the exemption list."""
    importers = {}
    for top in ("runtime", "device", "tools"):
        for path in sorted((ROOT / top).rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            if "SETTINGS_TOGGLES" in src:
                importers[path.name] = str(path.relative_to(ROOT))
    assert set(importers) == set(OWNERS), sorted(importers)
    for name, why in OWNERS.items():
        assert why, name


def test_no_module_outside_the_owners_names_a_toggle_verb():
    """A setter called from a fourth place is a hand-wired site by another
    name. The device tier reaches these through the Settings row and the
    serial word, never by name."""
    for top in ("runtime", "device", "tools"):
        for path in sorted((ROOT / top).rglob("*.py")):
            if path.name in OWNERS:
                continue
            src = path.read_text(encoding="utf-8")
            for setter in SETTERS:
                assert setter not in src, "%s names %s" % (path.name, setter)


# -- the five sites, each pinned shut -----------------------------------------

def test_the_boot_apply_names_no_setter():
    """Site 2. `load_system` reads the registry and calls the verb it names;
    six hand-kept `self.set_*(self.system.get(...))` lines used to sit there
    and a seventh was the price of a seventh toggle."""
    fn = _method("load_system")
    called = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"):
            called.add(node.func.attr)
    assert not called & set(SETTERS), sorted(called & set(SETTERS))
    assert not _string_constants(fn) & set(KEYS)
    src = ast.dump(fn)
    assert "SETTINGS_TOGGLES" in src


def test_the_static_row_list_carries_no_gate_row():
    """Site 3. `_SETTINGS_ROWS` is the non-toggle rows now; every "diag"-kind
    row comes from the registry, so a new one needs no splice and no fourth
    memo attribute."""
    from runtime.settings_layer import SettingsLayer
    kinds = {row[2] for row in SettingsLayer._SETTINGS_ROWS}
    assert "diag" not in kinds
    keys = {row[0] for row in SettingsLayer._SETTINGS_ROWS}
    assert not keys & set(KEYS)


def test_the_tap_dispatch_names_no_toggle():
    """Site 4. `_toggle_diag_row` was a six-branch `if key == ...` chain; it
    reads the registry now, so the branch a seventh toggle needed is gone."""
    tree = ast.parse(SETTINGS.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_toggle_diag_row")
    assert not _string_constants(fn) & set(KEYS)
    assert not _string_constants(fn) & set(SETTERS)


def test_the_dev_channel_writes_no_toggle_branch():
    """Site 5. The serial words are registry data. `set_diag_live` survives by
    name because `diag` is not a plain toggle -- see DEV_SETTER_EXEMPT."""
    src = DEV.read_text(encoding="utf-8")
    for _key, _label, _d, setter, _gate, dev in SETTINGS_TOGGLES:
        if setter not in DEV_SETTER_EXEMPT:
            assert setter not in src, setter
        if dev is not None:
            assert 'cmd == "%s"' % dev not in src, dev


# -- the capability gates are EXPRESSED ---------------------------------------

def test_a_board_that_cannot_serve_a_toggle_shows_no_row(tmp_path):
    """The host console has neither the crisp canvas hook nor a second
    keyboard, so neither row exists -- which is what keeps the other tiers'
    frozen Settings pixels."""
    ws = _ws(tmp_path)
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    for key, _l, _d, _s, gate, _dev in SETTINGS_TOGGLES:
        if gate is None:
            assert key in keys, key
        else:
            assert bool(gate(ws)) is False, key
            assert key not in keys, key


def test_a_granted_gate_adds_its_row_in_registry_order(tmp_path):
    """Grant both capabilities and the block is the registry, in order,
    directly after EDIT ICONS. This is the order the Settings goldens rest on:
    2 PLAYERS above FRAMESKIP, CRISP PIXELS below it."""
    ws = _ws(tmp_path)
    ws.sys_canvas.set_crisp_scale = lambda on: None
    ws.ble_keyboard = _FakeSecondKeyboard()
    rows = ws.settings_layer._settings_rows()
    keys = [r[0] for r in rows]
    start = keys.index("icons") + 1
    assert keys[start:start + len(KEYS)] == KEYS
    for i, key in enumerate(KEYS):
        assert rows[start + i] == (key, SETTINGS_TOGGLES[i][1], "diag")


def test_the_row_block_follows_the_gate_without_a_manual_bust(tmp_path):
    """The memo re-asks the gates every call, so a capability that appears mid
    session appears on the next frame. A memo keyed on a stale flag would show
    the OLD rows until something else invalidated the cache."""
    ws = _ws(tmp_path)
    before = ws.settings_layer._settings_rows()
    assert "two_player" not in [r[0] for r in before]
    ws.ble_keyboard = _FakeSecondKeyboard()
    after = ws.settings_layer._settings_rows()
    assert "two_player" in [r[0] for r in after]
    # ...and it settles: an unchanged gate returns the SAME tuple, which is
    # what the ~15-calls-per-frame memo exists for.
    assert ws.settings_layer._settings_rows() is after


def test_a_gated_toggle_declines_its_serial_word(tmp_path, capsys):
    """The dev channel expresses the gate too. A board with no crisp scaler
    says so instead of setting a flag nothing reads -- the declined-verb
    doctrine the channel's docstring states."""
    from runtime.dev_channel import DevChannel
    from tests.test_dev_channel import FakePointer
    ws = _ws(tmp_path)
    ch = DevChannel(ws, FakePointer())
    capsys.readouterr()                 # the channel's own no-fileno notice
    ch.run(ws, "crisp 1")
    assert "not available" in capsys.readouterr().out
    assert ws.crisp_pixels is False

    ws.sys_canvas.set_crisp_scale = lambda on: None
    ch.run(ws, "crisp 1")
    assert capsys.readouterr().out.strip() == "REMOTE crisp on"
    assert ws.crisp_pixels is True
    ch.run(ws, "crisp 0")
    assert capsys.readouterr().out.strip() == "REMOTE crisp off"
    assert ws.crisp_pixels is False


def test_the_serial_word_reports_what_the_console_reached(tmp_path, capsys):
    """`skip` keeps its wire behaviour verbatim -- the on-glass suites and the
    bench tools speak it -- and it does not persist, so a measurement session
    cannot leave a kid's system.json off-default."""
    from runtime.dev_channel import DevChannel
    from tests.test_dev_channel import FakePointer
    ws = _ws(tmp_path)
    ws.system.pop("frameskip", None)
    ch = DevChannel(ws, FakePointer())
    capsys.readouterr()                 # the channel's own no-fileno notice
    ch.run(ws, "skip 1")
    assert capsys.readouterr().out.strip() == "REMOTE skip on"
    assert ws.frameskip is True and "frameskip" not in ws.system
    ch.run(ws, "skip 0")
    assert capsys.readouterr().out.strip() == "REMOTE skip off"
    assert ws.frameskip is False


def test_a_setter_that_cannot_work_still_reports_honestly(tmp_path):
    """The pinned behaviour the gate must not tidy away: LOCAL 2P reports OFF
    whatever it is told when there is no second keyboard. The gate hides the
    row; the SETTER is what keeps a stale system.json key from claiming two
    players nothing can drive."""
    ws = _ws(tmp_path)
    assert ws.second_keyboard() is None
    ws.set_two_player(True, persist=False)
    assert ws.two_player is False


# -- persistence and the boot round trip --------------------------------------

def test_a_fresh_console_boots_at_the_declared_defaults(tmp_path):
    ws = _ws(tmp_path)
    for key, _l, default, _s, gate, _dev in SETTINGS_TOGGLES:
        if gate is None:
            assert getattr(ws, key) is default, key


def test_every_toggle_persists_under_its_own_key_and_comes_back(tmp_path):
    """The whole persistence tail is one body now, so this is the check that
    it writes the RIGHT key for each entry rather than one key six times."""
    from runtime import host_app
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    for key, _l, default, setter, gate, _dev in SETTINGS_TOGGLES:
        if gate is not None:
            continue
        getattr(ws, setter)(not default)
        assert ws.system[key] is (not default), key

    ws2 = host_app.build_workstation(carts)
    for key, _l, default, _s, gate, _dev in SETTINGS_TOGGLES:
        if gate is None:
            assert getattr(ws2, key) is (not default), key


def test_the_boot_apply_writes_nothing_back(tmp_path):
    """`persist=False` on every entry: loading a store must never re-write what
    it just read (and must not mint keys the kid never chose)."""
    ws = _ws(tmp_path)
    for key in KEYS:
        ws.system.pop(key, None)
    ws.load_system()
    assert not (set(ws.system) & set(KEYS)), sorted(set(ws.system) & set(KEYS))


# -- the mirrors stay flat ----------------------------------------------------

def test_no_toggle_is_a_property(tmp_path):
    """A property here would put a call on `pace`'s per-iteration read. Checked
    on the CLASS (a descriptor would not show up on the instance) and on the
    instance dict (a plain attribute, which is what makes the read a slot
    lookup)."""
    from runtime.console import Workstation
    ws = _ws(tmp_path)
    for key in KEYS:
        assert not isinstance(getattr(Workstation, key, None), property), key
        assert key in ws.__dict__, key


def test_the_hot_readers_still_read_the_flat_attribute():
    """The named per-frame sites, by source. `frame`'s frameskip gate and its
    pace check run every loop iteration on all three boards; both WMs read
    show_fps per painted game frame. None of them may go through the registry
    or the system dict."""
    console_src = CONSOLE.read_text(encoding="utf-8")
    assert "self.frameskip and self.wm.top_is_player()" in console_src
    assert "FPS_GOVERNOR or self.frameskip" in console_src
    assert 'ws.show_fps and self._stack[-1] == "desktop"' in (
        ROOT / "runtime" / "wm.py").read_text(encoding="utf-8")
    assert "self.ws.show_fps and bool(self._stack)" in (
        ROOT / "runtime" / "wm_windowed.py").read_text(encoding="utf-8")
    for src in (console_src,
                (ROOT / "runtime" / "wm.py").read_text(encoding="utf-8"),
                (ROOT / "runtime" / "wm_windowed.py").read_text("utf-8")):
        for key in KEYS:
            assert 'system["%s"]' % key not in src, key


def test_the_frame_loop_never_touches_the_registry():
    """The registry is declaration, persistence and the row -- never a read
    path. `frame` is where that would first cost something."""
    for name in ("frame", "handle_input", "handle_pointer"):
        fn = _method(name)
        for node in ast.walk(fn):
            assert not (isinstance(node, ast.Name)
                        and node.id == "SETTINGS_TOGGLES"), name


class _FakeSecondKeyboard:
    """A BLE keyboard alongside the board's own one (the T-Deck's shape), which
    is the whole capability `second_keyboard` looks for."""

    def __init__(self):
        self.player = 0

    def set_player(self, n):
        self.player = n
