"""Every service a target attaches to the Workstation, declared per target.

THE BUG THIS EXISTS TO MAKE IMPOSSIBLE (#161 Phase 0b, fixed in 6084b2d).

The web console shipped on the P4 with every SHARED piece already in place --
`moy_webhost.py`, the Settings row, the console's three webhost verbs, all
staged from one source -- and the T-Deck still did not have the feature,
because the single per-board injection line in its `run_desktop` was never
written. Nothing failed. `settings_layer._settings_rows` gates the row on
`getattr(ws, "webhost", None) is not None`, so the board quietly did not offer
it, and no test in the tree could tell "not wired" from "not supported".

That is the shape of the whole class: the shared console defaults every
attachment point to None (or to a neutral stub) in `Workstation.__init__`, and
every consumer is capability-gated -- so a missing injection is a MISSING
FEATURE, never a crash. Nothing goes red, and the only witness is somebody
picking up the other board.

So the claim is moved out of the code and into a TABLE. `WIRING` below states,
for each target and each service, either "this target injects it" or a one-line
reason why it deliberately does not. Adding a service to one target then fails
here until every other target's row says what it does about it -- which is the
conversation that never happened for the web console.

WHAT COUNTS AS AN INJECTION. Two sites, because the wiring lives in two:

  * `ws.<name> = ...` on the BOOT PATH of the target's wiring function.
    Assignments inside a loop are excluded: on the P4 the whole frame loop is
    inside `run_desktop`, and its serial dev commands mutate `ws.perf_capture`,
    `ws.show_fps`, `ws._psave_ms` and `ws._dirty` per keystroke. Those are
    per-frame STATE, not services -- a board that offers `diag 0|1` over serial
    is not thereby a board that has an FPS preference the others lack.

  * an argument to `console.wire_workstation_core`, which is where `make_api`,
    `wifi`, `make_audio`, `lua_runtime`, `keyboard`, `pointer`, `can_manage`
    and the store actually land on `ws`. That mapping is DERIVED from
    `wire_workstation_core`'s own body rather than restated here, so a new
    parameter that assigns to `ws` is picked up without editing this file --
    and then fails, correctly, as an undeclared service.

WHAT IT CANNOT SEE, said out loud: this is static analysis, so it asserts the
LINE EXISTS, not that the object it builds is non-None at runtime. Every
device injection here is inside a guarded `try`, and a build without the native
module still ends up with the attribute unset. That is fine and deliberate --
the defect being pinned is a line nobody wrote, and a line nobody wrote is
exactly what a source-level check catches. The runtime half belongs to the
board.

Same house pattern as `tests/test_staging_closure.py`: parse the source, never
import it (these device modules cannot import on CPython), and keep a ratchet so
a declared absence cannot outlive the thing it excuses.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# (wiring source, the function that wires it). Each target boots the SAME shared
# console; these are the four places that decide what it can do.
TARGETS = {
    "tdeck": ("firmware/lilygo_t_deck_plus_mainline/modules/moy_runtime.py",
              "run_desktop"),
    "p4": ("firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py",
           "run_desktop"),
    "guition": ("firmware/guition_jc3248w535/modules/moy_runtime.py",
                "run_desktop"),
    "host": ("runtime/host_app.py", "build_workstation"),
    # The wasm head is the third tier and wires the same console (moycore stage
    # 4), so it belongs in the table: it is the target most likely to be handed
    # a new service last. Only `boot()` is read -- web_boot also carries
    # page-facing entry points (`kiosk`, `reload_cart`) that poke `ws` from
    # JavaScript long after boot, and those are commands, not wiring.
    "web": ("firmware/web_runner/web_boot.py", "boot"),
}

INJECTED = True     # the target wires this service; anything else must be a
                    # non-empty reason string (see test_every_absence_...).

# What each service IS, one line each -- so a row's reason can be judged against
# what is actually being given up.
SERVICES = {
    "make_api": "the cart API factory: what a cart's globals are built from",
    "make_audio": "the audio backend the AudioBank model is played through",
    "lua_runtime": "the .moy `runtime: lua` cart engine (#67 -> moycore)",
    "make_game_canvas": "per-run canvas factory for a cart with a small raster",
    "carts_store": "the moy_carts module the shell reads/writes the store with",
    "carts_root": "where that store lives on this target",
    "can_manage": "are writes to the store enabled (Make tile, editors)",
    "wifi": "the #38 WiFi service behind Settings -> WIFI",
    "pointer": "the cursor model, sized to the SYSTEM canvas",
    "keyboard": "the physical keyboard, for the code editor's text/raw flip",
    "ble_keyboard": "an OPTIONAL SECOND keyboard over BLE HID (#26), on a board that already has a physical one",
    "_with_sd": "the storage gate every store op is wrapped in",
    "updater": "the #53 OTA updater behind Settings -> UPDATE FW",
    "webhost": "the #192 board-served web console (Settings -> WEB CONSOLE)",
    "reboot_hook": "the sysmenu Reboot row's real reset",
    "net": "the #65 multiplayer transport behind net.* in a cart",
    "wm": "the presentation tier: windowed desktop vs the fullscreen stack",
    "perf_capture": "per-frame timing measured without drawing the HUD",
}

# THE TABLE. One row per target, one entry per service, no gaps allowed.
#
# A reason is not a comment: it is the argument for the asymmetry, and the
# ratchet below deletes it the day it stops being true. Write what would have
# to change for the row to become INJECTED.
WIRING = {
    "tdeck": {
        "make_api": INJECTED,
        "make_audio": INJECTED,
        "lua_runtime": INJECTED,
        "make_game_canvas": INJECTED,
        "carts_store": INJECTED,
        "carts_root": INJECTED,
        "can_manage": "derived, not passed -- wire_workstation_core defaults it "
                      "to `carts_root is not None`, and on this board a store "
                      "root means a mounted SD card, which is exactly the "
                      "condition writes need",
        "wifi": INJECTED,
        "pointer": INJECTED,
        "keyboard": INJECTED,
        "ble_keyboard": INJECTED,
        "_with_sd": INJECTED,
        "updater": INJECTED,
        "webhost": INJECTED,
        "reboot_hook": INJECTED,
        "net": "no #65 transport on this board yet -- the host injects a "
               "loopback fake for the sim; a real one needs a radio pairing "
               "story, not a wiring line",
        "wm": "the fullscreen tier (#73). Workstation.__init__ already installs "
              "FullscreenStackWM, and wm_windowed.py is deliberately NOT staged "
              "into this build -- a 320x240 panel has no desktop to window",
        "perf_capture": INJECTED,
    },
    "p4": {
        "make_api": INJECTED,
        "make_audio": "no ES8311 bring-up on this board yet (#82). The codec is "
                      "on the hardware and unwired; until it is, injecting a "
                      "backend would give the console an audio path that plays "
                      "into nothing",
        "lua_runtime": INJECTED,
        "make_game_canvas": INJECTED,
        "carts_store": INJECTED,
        "carts_root": INJECTED,
        "can_manage": "derived, not passed -- the store is on internal flash and "
                      "is always writable, so the carts_root default is already "
                      "the right answer",
        "wifi": INJECTED,
        "pointer": INJECTED,
        "keyboard": INJECTED,
        "ble_keyboard": "a paired BLE keyboard IS this board's only keyboard, so it\n                         is attached as `keyboard` above rather than beside it --\n                         one driver, one slot. Settings finds it either way: \n                         settings_layer._bt_service() checks ble_keyboard first,\n                         then keyboard, and gates on settings_capable.",
        "_with_sd": "no SD card on this console -- the store is internal flash "
                    "and races nothing, so the Workstation's own call-through "
                    "default IS the correct gate. A wrapper here would be "
                    "ceremony around `fn()`",
        "updater": INJECTED,
        "webhost": INJECTED,
        "reboot_hook": INJECTED,
        "net": "same as the T-Deck: no #65 transport on a board yet",
        "wm": INJECTED,
        "perf_capture": INJECTED,
    },
    "guition": {
        "make_api": INJECTED,
        "make_audio": "audio is stage 5 of this board's bring-up and OPEN: "
                      "which amp (if any) is populated and on which I2S pins "
                      "is unverified, and its board.toml denies the moy_audio "
                      "usermod for the same reason. Wire both together at "
                      "stage 5",
        "lua_runtime": INJECTED,
        "make_game_canvas": INJECTED,
        "carts_store": INJECTED,
        "carts_root": INJECTED,
        "can_manage": "derived, not passed -- the store is on internal flash "
                      "and always writable, so the carts_root default is "
                      "already the right answer (the P4's row, same hardware "
                      "story)",
        "wifi": INJECTED,
        "pointer": INJECTED,
        "keyboard": INJECTED,
        "ble_keyboard": "a paired BLE keyboard IS this board's only keyboard, so it\n                         is attached as `keyboard` above rather than beside it --\n                         one driver, one slot. Settings finds it either way: \n                         settings_layer._bt_service() checks ble_keyboard first,\n                         then keyboard, and gates on settings_capable.",
        "_with_sd": "no SD in play (stage 4 open) -- the store is internal "
                    "flash and races nothing, so the Workstation's own "
                    "call-through default IS the correct gate",
        "updater": INJECTED,
        "webhost": INJECTED,
        "reboot_hook": INJECTED,
        "net": "same as the other boards: no #65 transport on a board yet",
        "wm": "the fullscreen tier, same as the T-Deck: Workstation.__init__ "
              "already installs FullscreenStackWM, and wm_windowed.py is "
              "deliberately not staged into this build",
        "perf_capture": INJECTED,
    },
    "host": {
        "make_api": INJECTED,
        "make_audio": INJECTED,
        "lua_runtime": INJECTED,
        "make_game_canvas": INJECTED,
        "carts_store": INJECTED,
        "carts_root": INJECTED,
        "can_manage": INJECTED,
        "wifi": INJECTED,
        "pointer": INJECTED,
        "keyboard": "the sim reads the host keyboard through ConsoleDriver/"
                    "pygame, not through a device keyboard object -- ws.keyboard "
                    "exists only so the code editor can flip a T-Deck keyboard "
                    "between ASCII and raw-matrix mode, which has no host analogue",
        "ble_keyboard": "the sim has no BLE radio; the host reads a real\n                         keyboard through ConsoleDriver/pygame, and a\n                         pairing UI over nothing would be a dead row.",
        "_with_sd": "no shared bus and no card: the Workstation's call-through "
                    "default is already right on a filesystem",
        "updater": "OTA writes an ESP32 app partition (esp32.Partition). There "
                   "is nothing on a host to flash, and Settings hides the row "
                   "when no updater is injected",
        "webhost": "the host IS the machine the browser runs on -- serving the "
                   "wasm console to itself has no user. `tools/simulate_desktop"
                   ".py` and firmware/web_runner cover that ground",
        "reboot_hook": "machine.reset() has no host meaning; the shared console "
                       "falls back to go_home() for the sysmenu Reboot row",
        "net": INJECTED,
        "wm": INJECTED,
        "perf_capture": "no serial PERF sampler here. On the host the timing "
                        "meters are driven by the perf HUD and the Settings "
                        "PERF DIAG toggle, which set the flag at runtime",
    },
    "web": {
        "make_api": INJECTED,
        "make_audio": INJECTED,
        "lua_runtime": INJECTED,
        "make_game_canvas": INJECTED,
        "carts_store": INJECTED,
        "carts_root": INJECTED,
        "can_manage": INJECTED,
        "wifi": INJECTED,
        "pointer": INJECTED,
        "keyboard": "browser key events arrive through web_input as InputState, "
                    "not as a device keyboard object with a mode command",
        "ble_keyboard": "a browser cannot pair HID devices for the page --\n                         keys arrive as DOM events through web_input.",
        "_with_sd": "the VFS is in-memory; nothing to gate",
        "updater": "the page IS the update -- a reload fetches the current "
                   "build, so there is no image to flash",
        "webhost": "this build is what a webhost SERVES. A page hosting itself "
                   "is the same circle the host row describes",
        "reboot_hook": "a reload is the browser's reset, and the page owns it",
        "net": "no #65 transport in the browser yet -- the host's LoopbackNet is "
               "a sim fake for a solo desktop and would mean nothing here",
        "wm": INJECTED,
        "perf_capture": "no serial sampler; the page's own harness times whole "
                        "frames from outside (pageshot/browsershot)",
    },
}

# `ws.<name> = ...` on a boot path that is NOT a service. Explicit and with a
# reason each, the same way test_staging_closure spells out MICROPYTHON_BUILTINS
# -- a name here is a claim someone can check, where a widening regex would be a
# hole. Nothing may be added without saying why it is not an attachment point.
NOT_A_SERVICE = {
    "show_fps": "a user PREFERENCE with a default in Workstation.__init__, a "
                "Settings toggle and a persisted system.json key. web_boot sets "
                "it from its `hud` argument so a conformance capture has no FPS "
                "chip in the golden frame; the P4 flips it from `diag 0|1`. "
                "Neither is a capability the other target lacks",
    "perf_hud": "the same knob's other half -- whether the chip DRAWS. Turned "
                "off with show_fps for the same clean-frame reason",
    "_achievement_unlocked": "a de-branding stub: the web build silences the "
                             "console's achievement toasts (gamification for "
                             "the kid console, not for a cart player). An "
                             "override of shared behaviour, not a backend",
    "_psave_ms": "per-board state, not a capability: the idle-blank timeout "
                 "mirrored onto ws so the dev channel's `state` reports the "
                 "LIVE value (dev_channel._remote_state's psave field). Both "
                 "boards set it beside their IdleBlank; the host and web have "
                 "no panel to blank",
}


# -- extraction ---------------------------------------------------------------


def _wire_param_map():
    """{ws attribute: wire_workstation_core parameter} -- read from the function.

    Derived rather than restated so that adding a parameter that lands on `ws`
    is seen here immediately. `ws.can_manage` mentions two parameters (it
    defaults from carts_root), so a same-name match wins over a sole mention.
    """
    fn = _func(ROOT / "runtime" / "console.py", "wire_workstation_core")
    params = {a.arg for a in fn.args.args}
    out = {}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if not _is_ws_attr(t):
                continue
            used = {x.id for x in ast.walk(n.value)
                    if isinstance(x, ast.Name)} & params
            if t.attr in used:
                out[t.attr] = t.attr
            elif len(used) == 1:
                out[t.attr] = used.pop()
    return out


def _func(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("%s has no %s()" % (path, name))


# The workstation reaches a closure under whatever name that closure's
# parameter has. The T-Deck's `_before_slim` calls it `_ws`, so matching only
# "ws" read its `_with_sd`/`updater` injections as MISSING -- a service the
# board demonstrably attaches, reported as absent.
_WS_NAMES = ("ws", "_ws")


def _is_ws_attr(node):
    return (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in _WS_NAMES)


def _boot_assignments(fn):
    """{attr: lineno} for `ws.<attr> = ...` reachable on the boot path.

    Loop bodies are skipped. On the P4 the entire frame loop lives inside
    run_desktop, and its serial dev commands assign to `ws` on every keystroke;
    counting those would make "this board has a `power` command" read as "this
    board injects a service the others do not".

    Nested functions are NOT skipped: the T-Deck's `_with_sd` injection lives in
    `_before_slim`, a closure handed to wire_workstation_core precisely because
    it must run between the store hookup and slim_carts. That is boot wiring in
    every sense that matters.
    """
    out = {}

    def walk(node, in_loop):
        for child in ast.iter_child_nodes(node):
            loop = in_loop or isinstance(node, (ast.While, ast.For,
                                                ast.AsyncFor))
            if isinstance(child, ast.Assign) and not loop:
                for t in child.targets:
                    if _is_ws_attr(t):
                        out.setdefault(t.attr, child.lineno)
            walk(child, loop)

    walk(fn, False)
    return out


def _wire_supplied(fn, param_map):
    """{attr: lineno} for services this target hands to wire_workstation_core."""
    fname = "wire_workstation_core"
    order = [a.arg for a in _func(ROOT / "runtime" / "console.py", fname).args.args]
    out = {}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        called = (n.func.attr if isinstance(n.func, ast.Attribute)
                  else getattr(n.func, "id", None))
        if called != fname:
            continue
        supplied = {order[i] for i in range(len(n.args)) if i < len(order)}
        supplied |= {k.arg for k in n.keywords if k.arg}
        for attr, param in param_map.items():
            if param in supplied:
                out.setdefault(attr, n.lineno)
    return out


def injections(target):
    """{service: lineno} -- everything `target` attaches to the Workstation."""
    rel, fname = TARGETS[target]
    fn = _func(ROOT / rel, fname)
    got = _boot_assignments(fn)
    got.update(_wire_supplied(fn, _wire_param_map()))
    for name in NOT_A_SERVICE:
        got.pop(name, None)
    return got


# -- the tests ----------------------------------------------------------------


def test_the_extractor_still_sees_the_wiring():
    """A parse that finds nothing would make every assertion below vacuous --
    and this file reads four sources by path, any of which can be moved."""
    pmap = _wire_param_map()
    for expect in ("make_api", "wifi", "make_audio", "lua_runtime", "keyboard"):
        assert expect in pmap, "wire_workstation_core no longer wires %s" % expect
    for target in TARGETS:
        found = injections(target)
        assert len(found) >= 8, "%s: extraction found only %s" % (
            target, sorted(found))


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_the_table_covers_every_service_for_every_target(target):
    """No blanks. A service with no row on some target is precisely the state
    the web console shipped in: undecided, and therefore absent."""
    missing = sorted(set(SERVICES) - set(WIRING[target]))
    extra = sorted(set(WIRING[target]) - set(SERVICES))
    assert not missing, "%s has no row for: %s" % (target, missing)
    assert not extra, "%s declares unknown services (add to SERVICES): %s" % (
        target, extra)


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_every_absence_carries_a_reason(target):
    """A row is either INJECTED or an ARGUMENT. `None`/`False`/`""` would let a
    row be silenced without saying anything, which is the same silence the
    missing injection had."""
    for service, value in sorted(WIRING[target].items()):
        if value is INJECTED:
            continue
        assert isinstance(value, str) and value.strip(), (
            "%s/%s: an absence must carry a reason, got %r"
            % (target, service, value))


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_the_target_injects_exactly_what_the_table_says(target):
    """THE PIN. Declared-present must be wired, and wired must be declared.

    A service added to one target and forgotten on another fails here, on the
    forgotten one, naming it -- instead of shipping a console that quietly
    cannot do the thing.
    """
    found = injections(target)
    declared = {s for s, v in WIRING[target].items() if v is INJECTED}
    missing = sorted(declared - set(found))
    assert not missing, (
        "%s declares these services but its wiring never attaches them: %s\n"
        "  (wired in %s -> %s)"
        % (target, missing, TARGETS[target][0], TARGETS[target][1]))
    undeclared = sorted(set(found) - set(SERVICES))
    assert not undeclared, (
        "%s attaches services the table does not know: %s\n"
        "  Add each to SERVICES and give EVERY target a row -- either INJECTED "
        "or the reason it does not have it. If it is not a service (a "
        "preference, per-frame state), say so in NOT_A_SERVICE." % (
            target, undeclared))


def test_no_absence_reason_has_quietly_gone_stale():
    """The ratchet, mirroring test_staging_closure's.

    An entry is a live asymmetry, not a pass. The moment a target starts
    injecting something its row excuses, the excuse -- and its paragraph of
    reasoning -- must be deleted rather than outliving the gap it described.
    """
    stale = []
    for target in sorted(TARGETS):
        found = injections(target)
        for service, value in sorted(WIRING[target].items()):
            if value is not INJECTED and service in found:
                stale.append("%s/%s (now wired at %s:%d)" % (
                    target, service, TARGETS[target][0], found[service]))
    assert not stale, (
        "these targets now inject a service their row excuses -- delete the "
        "excuse and set it to INJECTED:\n  %s" % "\n  ".join(stale))


def test_nothing_is_excused_as_not_a_service_without_a_reason():
    """NOT_A_SERVICE removes a name from the whole comparison, so it is the one
    place a real service could be hidden. Every entry states why it is state or
    preference rather than a backend."""
    for name, why in sorted(NOT_A_SERVICE.items()):
        assert isinstance(why, str) and why.strip(), name
        assert name not in SERVICES, (
            "%s is declared both a service and not one" % name)


def test_every_service_says_what_it_is():
    for service, what in sorted(SERVICES.items()):
        assert isinstance(what, str) and what.strip(), service


def test_the_webhost_row_is_the_one_this_file_was_written_for():
    """The concrete regression, kept as its own assertion so a refactor of the
    machinery above cannot lose it: BOTH boards serve the web console (6084b2d),
    and neither non-board target claims to."""
    for board in ("tdeck", "p4"):
        assert WIRING[board]["webhost"] is INJECTED
        assert "webhost" in injections(board), (
            "%s stopped injecting the web console -- the Settings row is gated "
            "on `getattr(ws, 'webhost', None) is not None`, so this board now "
            "silently does not offer it" % board)
