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
import collections
import functools
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
    "c6_updater": "the C6 radio co-processor's updater behind Settings -> "
                  "UPGRADE C6 RADIO (#7/#58): downloads the shimmed slave "
                  "image and streams it over SDIO",
    "webhost": "the #192 board-served web console (Settings -> WEB CONSOLE)",
    "reboot_hook": "the sysmenu Reboot row's real reset",
    "net": "the #65 multiplayer transport behind net.* in a cart",
    "link": "the #7 ESP-NOW radio: discovery, pairing and the two-console lockstep link",
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
        "c6_updater": "its radio is the S3's own silicon -- there is no co-processor to update. The row becomes INJECTED the day a board grows a companion radio chip",
        "webhost": INJECTED,
        "reboot_hook": INJECTED,
        "net": INJECTED,
        "link": INJECTED,
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
        "c6_updater": INJECTED,
        "webhost": INJECTED,
        "reboot_hook": INJECTED,
        "net": INJECTED,
        "link": INJECTED,
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
        "c6_updater": "same as the T-Deck: this S3's radio is on-die, there "
                      "is no co-processor to flash",
        "webhost": INJECTED,
        "reboot_hook": INJECTED,
        "net": INJECTED,
        "link": INJECTED,
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
        "c6_updater": "the host has no radio hardware at all; the C6 updater "
                      "is SDIO plumbing to a co-processor that only the P4 "
                      "carries, and Settings hides its row the same way",
        "webhost": "the host IS the machine the browser runs on -- serving the "
                   "wasm console to itself has no user. `tools/simulate_desktop"
                   ".py` and firmware/web_runner cover that ground",
        "reboot_hook": "machine.reset() has no host meaning; the shared console "
                       "falls back to go_home() for the sysmenu Reboot row",
        "net": INJECTED,
        "link": "no radio on a laptop. The sim gets its second player from a router\n"
                "slot instead (players.PlayerRouter.add_player), which is the same\n"
                "seam a radio fills -- so a two-player cart is testable here with\n"
                "no hardware at all",
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
        "c6_updater": "no radio, no SDIO, no co-processor -- the same absence "
                      "as the updater row, one level down",
        "webhost": "this build is what a webhost SERVES. A page hosting itself "
                   "is the same circle the host row describes",
        "reboot_hook": "a reload is the browser's reset, and the page owns it",
        "net": "no #65 transport in the browser yet -- the host's LoopbackNet is "
               "a sim fake for a solo desktop and would mean nothing here",
        "link": "a wasm sandbox has no radio, and never will. The browser's "
                "route to a second player is a controller over the page, not "
                "ESP-NOW",
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


def _find(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _func(path, name):
    fn = _find(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
               name)
    if fn is None:
        raise AssertionError("%s has no %s()" % (path, name))
    return fn


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


# -- the DRIVEN axis: attached is not the same as running ---------------------
#
# THE BUGS THIS HALF EXISTS TO MAKE IMPOSSIBLE (#26, fixed in ef7c915).
#
# The T-Deck's `ble_keyboard` row above read INJECTED and was true: the board
# built the driver and hung it on `ws`. Two of the three faults behind "the BLE
# keyboard does nothing on the T-Deck" were downstream of that line. Nothing
# ever called `start()`, so the Settings panel opened onto a radio that had
# never scanned; and `_poll_inputs` never called `poll()`, so even a paired
# keyboard could not have produced a keypress. Both shipped. The table said
# INJECTED and the table was right, which is exactly why it caught neither.
#
# So a service whose object has a LIFECYCLE declares it, per target, in the same
# shape: either the wiring makes the call, or it says who does, or it says why
# nobody does. An object nobody drives is a feature that is present and inert.

DRIVEN = {
    "keyboard": ("start", "poll"),
    "ble_keyboard": ("start", "poll"),
    # start() belongs to the Settings row -- injecting the webhost only makes
    # the row appear, and the row is what brings the radio up. The poll is
    # wiring: a bound socket nobody accepts on is a network fault.
    "webhost": ("poll",),
    # The radio is armed by the RUN of a multiplayer cart, not by the boot: a
    # link nobody polls hears no beacons and pairs with nobody, and a link
    # nobody starts is exactly the "present and inert" shape this table exists
    # to catch. The poll is wiring; the start is the Player's, per cart.
    "link": ("start", "poll"),
}

_VERBS = {v for verbs in DRIVEN.values() for v in verbs}

HERE = True     # the target's own wiring function makes the call

# The call is made by a function the wiring CALLS. Both halves are checked, so
# neither "the board stopped calling the helper" nor "the helper stopped making
# the call" can pass.
Via = collections.namedtuple("Via", "path func")

# The call is made on DEMAND by a user action the wiring never reaches. Only
# the delegate half can be checked from here, so the reason is required and the
# ratchet below deletes it the day the target starts making the call itself.
Lazy = collections.namedtuple("Lazy", "path func why")

LIFECYCLE = {
    "tdeck": {
        ("keyboard", "start"): "TDeckKeyboard has no start(): the C3 is on I2C0 "
                               "and answers from __init__, so poll() is the "
                               "whole lifecycle",
        ("keyboard", "poll"): HERE,
        ("ble_keyboard", "start"): Lazy(
            "runtime/settings_layer.py", "open_bluetooth",
            "auto_start=False on purpose -- scanning is what makes BLE "
            "expensive and this board's keyboard already works, so the radio "
            "comes up when a kid opens the picker. The touch-only boards start "
            "theirs at boot because a paired keyboard is their only way out of "
            "a cart"),
        ("ble_keyboard", "poll"): HERE,
        ("webhost", "poll"): Via("runtime/device_boot.py", "poll_webhost"),
        ("link", "start"): Via("runtime/player.py", "start"),
        ("link", "poll"): HERE,
    },
    "p4": {
        ("keyboard", "start"): HERE,
        ("keyboard", "poll"): HERE,
        ("webhost", "poll"): Via("runtime/device_boot.py", "poll_webhost"),
        ("link", "start"): Via("runtime/player.py", "start"),
        ("link", "poll"): HERE,
    },
    "guition": {
        ("keyboard", "start"): HERE,
        ("keyboard", "poll"): HERE,
        ("webhost", "poll"): Via("runtime/device_boot.py", "poll_webhost"),
        ("link", "start"): Via("runtime/player.py", "start"),
        ("link", "poll"): HERE,
    },
}


# -- extraction ---------------------------------------------------------------


class _Handles:
    """Which names in a module hold which Workstation service.

    Follows the shapes the wiring actually uses -- `x = ws.svc`,
    `getattr(ws, "svc", None)`, `for x in (<handles>)` and `x = helper()` where
    helper returns one -- to a fixed point, so the service settings_layer
    reaches through `self._bt_service()` resolves like a direct attribute.
    Scopes are deliberately ignored: a name is a name, which can only ever make
    this MORE willing to find a call, and a found call is the thing being
    demanded.
    """

    def __init__(self, path, seed=None):
        self.tree = ast.parse(path.read_text(encoding="utf-8"),
                              filename=str(path))
        self.names = {k: set(v) for k, v in (seed or {}).items()}
        self.rets = {}
        for _ in range(5):
            if not self._pass():
                break

    def _pass(self):
        grew = False
        for n in ast.walk(self.tree):
            if (isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)):
                grew |= self._add(self.names, n.targets[0].id, self.of(n.value))
            elif (isinstance(n, (ast.For, ast.AsyncFor))
                  and isinstance(n.target, ast.Name)):
                it = n.iter
                elts = (it.elts if isinstance(it, (ast.Tuple, ast.List))
                        else [it])
                got = set()
                for e in elts:
                    got |= self.of(e)
                grew |= self._add(self.names, n.target.id, got)
            elif isinstance(n, ast.FunctionDef):
                got = set()
                for r in ast.walk(n):
                    if isinstance(r, ast.Return) and r.value is not None:
                        got |= self.of(r.value)
                grew |= self._add(self.rets, n.name, got)
        return grew

    @staticmethod
    def _add(table, key, vals):
        cur = table.setdefault(key, set())
        if not vals - cur:
            return False
        cur |= vals
        return True

    def of(self, node):
        """The services `node` can evaluate to."""
        if isinstance(node, ast.Attribute) and node.attr in SERVICES:
            return {node.attr}
        if isinstance(node, ast.Name):
            return set(self.names.get(node.id, ()))
        if isinstance(node, ast.Call):
            f = node.func
            if getattr(f, "id", None) == "getattr" and len(node.args) >= 2:
                a = node.args[1]
                if isinstance(a, ast.Constant) and a.value in SERVICES:
                    return {a.value}
            return set(self.rets.get(_called(node), ()))
        return set()

    def verbs(self, funcname):
        """{(service, verb): per_frame} for `<handle>.<verb>()` under funcname.

        per_frame is True when the call sits in a loop or inside a nested def
        -- a frame hook, which is what `poll_inputs`/`tail` are handed to
        device_boot.FrameLoop as. That distinction is the point: a poll() on a
        board's boot path runs once and reads, in every static sense, exactly
        like one that runs every frame.
        """
        root = _find(self.tree, funcname)
        assert root is not None, "no %s() to read" % funcname
        out = {}

        def walk(node, per_frame):
            sub = per_frame or isinstance(node, (ast.While, ast.For,
                                                 ast.AsyncFor)) or (
                node is not root and isinstance(node, (ast.FunctionDef,
                                                       ast.Lambda)))
            for c in ast.iter_child_nodes(node):
                if (isinstance(c, ast.Call)
                        and isinstance(c.func, ast.Attribute)
                        and c.func.attr in _VERBS):
                    for svc in self.of(c.func.value):
                        key = (svc, c.func.attr)
                        out[key] = out.get(key, False) or sub
                walk(c, sub)

        walk(root, False)
        return out

    def calls(self, funcname):
        """Every function name called anywhere under funcname."""
        root = _find(self.tree, funcname)
        assert root is not None, "no %s() to read" % funcname
        return {_called(n) for n in ast.walk(root) if isinstance(n, ast.Call)}


def _called(node):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _wire_seed(fn, param_map):
    """{local name: {service}} for the handles a target hands to
    wire_workstation_core.

    `keyboard=keyboard` is the only thing that says the P4's BleHidKeyboard
    local IS ws.keyboard, and without it every lifecycle call on those boards
    reads as a call on an unrelated object."""
    inv = {}
    for attr, param in param_map.items():
        inv.setdefault(param, attr)
    order = [a.arg for a in _func(ROOT / "runtime" / "console.py",
                                  "wire_workstation_core").args.args]
    seed = {}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call) or _called(n) != "wire_workstation_core":
            continue
        pairs = [(order[i], a) for i, a in enumerate(n.args) if i < len(order)]
        pairs += [(k.arg, k.value) for k in n.keywords if k.arg]
        for param, val in pairs:
            if isinstance(val, ast.Name) and param in inv:
                seed.setdefault(val.id, set()).add(inv[param])
    return seed


@functools.lru_cache(maxsize=None)
def _target_handles(target):
    rel, fname = TARGETS[target]
    path = ROOT / rel
    return _Handles(path, _wire_seed(_func(path, fname), _wire_param_map()))


@functools.lru_cache(maxsize=None)
def _module_handles(rel):
    return _Handles(ROOT / rel)


def _required_cells(target):
    """(service, verb) pairs this target owes a declaration for: every verb of
    every DRIVEN service its row above says it injects."""
    return {(s, v) for s in DRIVEN if WIRING[target].get(s) is INJECTED
            for v in DRIVEN[s]}


def _driven_here(target, service, verb):
    """(found, per_frame) for the target's own wiring function."""
    got = _target_handles(target).verbs(TARGETS[target][1])
    return ((service, verb) in got, got.get((service, verb), False))


# -- the tests ----------------------------------------------------------------


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_every_driven_service_declares_its_lifecycle(target):
    """No blanks on this axis either. A board that starts injecting a keyboard
    fails here until it has said where start() and poll() happen."""
    have = set(LIFECYCLE.get(target, {}))
    need = _required_cells(target)
    assert not need - have, "%s injects but does not declare: %s" % (
        target, sorted(need - have))
    assert not have - need, (
        "%s declares a lifecycle for something it does not inject (or for a "
        "verb DRIVEN does not list): %s" % (target, sorted(have - need)))


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_every_lifecycle_absence_carries_a_reason(target):
    for (service, verb), value in sorted(LIFECYCLE.get(target, {}).items()):
        if value is HERE or isinstance(value, Via):
            continue
        why = value.why if isinstance(value, Lazy) else value
        assert isinstance(why, str) and why.strip(), (
            "%s/%s/%s: an absence must carry a reason, got %r"
            % (target, service, verb, value))


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_the_target_drives_exactly_what_the_lifecycle_says(target):
    """THE PIN. An injected service with a lifecycle is inert until something
    calls it, and both of ef7c915's silent bugs were exactly that line missing.
    """
    for (service, verb), value in sorted(LIFECYCLE.get(target, {}).items()):
        found, per_frame = _driven_here(target, service, verb)
        if value is HERE:
            assert found, (
                "%s declares it calls %s.%s() itself and does not -- the "
                "service is attached and inert (wiring: %s -> %s)"
                % (target, service, verb, TARGETS[target][0],
                   TARGETS[target][1]))
            assert verb != "poll" or per_frame, (
                "%s calls %s.poll() on its BOOT PATH, not from a frame hook -- "
                "it runs once and then never again" % (target, service))
            continue
        if isinstance(value, (Via, Lazy)):
            assert (service, verb) in _module_handles(value.path).verbs(
                value.func), (
                "%s says %s.%s() happens in %s::%s, and that function does not "
                "make the call" % (target, service, verb, value.path,
                                   value.func))
        if isinstance(value, Via):
            assert value.func in _target_handles(target).calls(
                TARGETS[target][1]), (
                "%s delegates %s.%s() to %s and never calls it"
                % (target, service, verb, value.func))


def test_no_lifecycle_excuse_has_gone_stale():
    """The ratchet. A delegated or excused call that the target now makes
    itself must be re-declared HERE, not left wearing an argument that has
    stopped being true."""
    stale = []
    for target in sorted(TARGETS):
        for (service, verb), value in sorted(LIFECYCLE.get(target, {}).items()):
            if value is HERE or isinstance(value, Via):
                continue
            if _driven_here(target, service, verb)[0]:
                stale.append("%s/%s/%s" % (target, service, verb))
    assert not stale, (
        "these targets now make a call their row delegates or excuses -- set "
        "the cell to HERE and delete the excuse:\n  %s" % "\n  ".join(stale))


def test_the_ble_keyboard_rows_are_the_ones_this_half_was_written_for():
    """The concrete regressions, kept as their own assertion so a refactor of
    the machinery cannot lose them: the T-Deck polls its BLE keyboard EVERY
    FRAME, and something really does start it."""
    found, per_frame = _driven_here("tdeck", "ble_keyboard", "poll")
    assert found and per_frame, (
        "the T-Deck stopped polling ws.ble_keyboard from its frame hook -- a "
        "paired keyboard cannot produce a keypress (#26)")
    start = LIFECYCLE["tdeck"][("ble_keyboard", "start")]
    assert ("ble_keyboard", "start") in _module_handles(start.path).verbs(
        start.func), (
        "nothing calls ble_keyboard.start() on the T-Deck -- the Bluetooth "
        "panel opens onto a radio that has never scanned (#26)")
