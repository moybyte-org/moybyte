"""`tools/*.py` answer `--help` with no board, and speak the dev channel's words.

WHY THIS EXISTS. Most of these tools are reachable only from prose -- the
board READMEs, `.claude/rules/`, an issue comment. Nothing imports them and no
suite runs them, so a rename in `tools/p4_autotest.py`'s driver, or a command
dropped from `runtime/dev_channel.py`'s table, breaks them silently and the
report arrives from a human already sitting at a board with a cable in hand.

Two nets, and both are DERIVED FROM SOURCE rather than listed here, the way
`tests/test_board_ports.py` derives its facts:

  * every entry-point tool answers `--help` in a subprocess, fast, with every
    `MOYBYTE_*_PORT` scrubbed from the environment -- so a tool that would OPEN
    a port to answer that fails here instead of on somebody's desk, which is
    exactly what `tools/device_port.py` did (`--help` probed all five boards,
    and closing an attach_only handle resets an S3-class board);
  * every serial verb a tool sends through the `P4Board` driver is a word
    `DevChannel.run` actually dispatches, read out of `dev_channel.py` itself.

Discovery, not an allowlist, for the reason `tools/board_config.py` gives about
staging: an allowlist asks "did somebody remember to add this?", whose wrong
answer is silent. A tool that must NOT be asked is named below with its reason.

This suite never opens a serial port and never needs a board or a display.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# A tool answering its usage has nothing to compute. Generous enough not to
# flake on a loaded CI box, short enough to catch a tool that went to hardware:
# the device_port.py hang this suite was written for ran past twenty seconds.
HELP_TIMEOUT_S = 10.0

# Not asked for `--help`, each with the reason it cannot be.
UNASKED = {
    "p8_lua_port.py":
        "VENDORED (`make vendor-p8-import`) -- fix it upstream in moy-spec.",
    "esptool_no_modem.py":
        "imports esptool + pyserial at module level, which are the `device` "
        "extra: hardware only, and not what `make setup` installs.",
}

# `--help` that exits 2 rather than 0: a hand-rolled positional CLI printing
# its usage, which is a usage ERROR here. It still has to answer at once and
# without touching hardware, so these are asked, just not for a 0.
USAGE_EXIT_2 = {"board_config.py", "gen_gsl_fw.py", "import_p8.py"}

# The one tool that drives a board without offering `--board`: it IS the
# driver, and its standalone tour is the Waveshare P4's own (`--port`).
NO_BOARD_FLAG = {"p4_autotest.py"}

# How a tool puts a line on the wire (`tools/p4_autotest.py`'s P4Board), and
# the two that wrap one: `pyval`/`pyexec` both send the `py` command.
SEND_METHODS = ("cmd", "_write_line")
PY_METHODS = ("pyval", "pyexec")


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def _entry_tools():
    """Every `tools/*.py` with a `__main__` entry, minus the unasked."""
    out = []
    for path in sorted(TOOLS.glob("*.py")):
        if path.name in UNASKED:
            continue
        if '__name__ == "__main__"' in path.read_text():
            out.append(path)
    return out


def _tree(path):
    return ast.parse(path.read_text(), filename=str(path))


ENTRY_TOOLS = _entry_tools()
ENTRY_IDS = [p.name for p in ENTRY_TOOLS]


# ---------------------------------------------------------------------------
# the serial vocabulary, read off dev_channel.py
# ---------------------------------------------------------------------------


def _dev_channel_commands():
    """The words `DevChannel.run` dispatches, from its own source.

    The dispatcher is an if-ladder over `cmd`, plus two openings it does not
    spell inline: a SETTINGS_TOGGLES entry may declare a serial word (#217's
    `steady`), and a board may register its own handlers in `extra` (the
    Guition S3's `bt`).
    """
    run = None
    for node in ast.walk(_tree(ROOT / "runtime" / "dev_channel.py")):
        if isinstance(node, ast.ClassDef) and node.name == "DevChannel":
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == "run":
                    run = fn
    assert run is not None, "DevChannel.run is gone -- this suite reads it"

    cmds = set()
    for node in ast.walk(run):
        if not (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name) and node.left.id == "cmd"):
            continue
        for op, other in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(other, ast.Constant):
                cmds.add(other.value)
            elif isinstance(op, ast.In) and isinstance(other, (ast.Tuple,
                                                               ast.List,
                                                               ast.Set)):
                cmds.update(e.value for e in other.elts
                            if isinstance(e, ast.Constant))

    # A settings toggle's own serial word is the 6th field of its entry.
    for node in ast.walk(_tree(ROOT / "runtime" / "settings_layer.py")):
        if not (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "SETTINGS_TOGGLES"
                        for t in node.targets)):
            continue
        for row in node.value.elts:
            fields = row.elts
            if len(fields) >= 6 and isinstance(fields[5], ast.Constant):
                if fields[5].value:
                    cmds.add(fields[5].value)

    # Board-only handlers, from the `extra=` each board's runtime passes.
    for runtime in sorted(ROOT.glob("firmware/*/modules/moy_runtime.py")):
        for node in ast.walk(_tree(runtime)):
            if (isinstance(node, ast.keyword) and node.arg == "extra"
                    and isinstance(node.value, ast.Dict)):
                cmds.update(k.value for k in node.value.keys
                            if isinstance(k, ast.Constant))
    return cmds


DEV_COMMANDS = _dev_channel_commands()


def _literal_head(node):
    """The leading string literal of a command argument, or None.

    A verb survives formatting -- `"swipe %d %d" % ...`, an f-string, a
    `.format()` -- because the WORD is always in the literal head. Anything
    with no literal head at all (a variable forwarded from a helper) is not
    guessed at.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return _literal_head(node.left)
    if isinstance(node, ast.JoinedStr) and node.values:
        return _literal_head(node.values[0])
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        return _literal_head(node.func.value)
    return None


def _verbs_sent(path):
    """The serial verbs a tool sends, as {verb: the line it is sent from}."""
    found = {}
    for node in ast.walk(_tree(path)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr in PY_METHODS:
            found.setdefault("py", node.lineno)
            continue
        if node.func.attr in SEND_METHODS and node.args:
            head = _literal_head(node.args[0])
            if head and head.split():
                found.setdefault(head.split()[0], node.lineno)
    return found


BOARD_TOOLS = [p for p in ENTRY_TOOLS if _verbs_sent(p)]
BOARD_IDS = [p.name for p in BOARD_TOOLS]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_the_reasons_name_files_that_exist():
    """An excuse outlives its tool otherwise, and quietly exempts nothing."""
    for name in set(UNASKED) | USAGE_EXIT_2 | NO_BOARD_FLAG:
        assert (TOOLS / name).exists(), "%s is named here and is gone" % name


def test_discovery_found_the_board_tools():
    """The nets below are derived, so an empty derivation must not pass.

    Every one of these is reachable only from prose, which is the whole reason
    this file exists; if a parse change stops finding them the suite has to
    say so rather than assert nothing about nothing.
    """
    expected = {
        "p4_alloc.py", "p4_attrib.py", "p4_bench.py", "p4_cart_bench.py",
        "p4_chrome_freeze.py", "p4_clicks.py", "p4_conformance.py",
        "p4_hitch.py", "p4_perf.py", "p4_scroll_ab.py", "p4_surface_sweep.py",
        "p4_autotest.py", "push_cart.py",
    }
    missing = sorted(expected - set(BOARD_IDS))
    assert not missing, "no serial verbs found in %s" % ", ".join(missing)
    for name in ("device_port.py", "board_flash.py"):
        assert name in ENTRY_IDS, "%s is asked for its help" % name


def test_the_dev_channel_vocabulary_parsed():
    """Same guard on the other derivation: an empty command set passes all."""
    anchors = {"py", "state", "diag", "run", "tap", "swipe", "drag", "recv",
               "open", "mem", "power", "steady"}
    missing = sorted(anchors - DEV_COMMANDS)
    assert not missing, ("DevChannel.run no longer dispatches %s -- if that is "
                         "the change, the tools speaking it need the same edit"
                         % ", ".join(missing))


def _help(tool, cwd):
    """`tool --help`, from `cwd`, with no port named in the environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOYBYTE_")}
    env.pop("DISPLAY", None)
    return subprocess.run([sys.executable, str(tool), "--help"], cwd=str(cwd),
                          env=env, capture_output=True, text=True,
                          timeout=HELP_TIMEOUT_S)


@pytest.mark.parametrize("tool", ENTRY_TOOLS, ids=ENTRY_IDS)
def test_help_answers_with_no_board(tool, tmp_path):
    """`--help` runs to completion, fast, with no port named anywhere.

    Asked from a TEMPORARY directory, because the failure this catches is not
    only a hang: several of these tools read their first positional as an
    output path, so an unhandled `--help` writes the generated artifact to a
    file called `--help` -- `tools/gen_device_carts.py` put 681KB of frozen
    cart data in the repo root that way. From here a regression lands in a
    tmpdir pytest throws away.
    """
    try:
        done = _help(tool, tmp_path)
    except subprocess.TimeoutExpired:
        pytest.fail("%s --help did not answer in %gs -- it is doing work, and "
                    "a board tool doing work here is opening a port"
                    % (tool.name, HELP_TIMEOUT_S))
    want = 2 if tool.name in USAGE_EXIT_2 else 0
    assert done.returncode == want, (
        "%s --help exited %d (expected %d)\n%s"
        % (tool.name, done.returncode, want, (done.stderr or done.stdout)[-800:]))
    stray = sorted(p.name for p in tmp_path.iterdir())
    assert not stray, ("%s --help wrote %s -- it is reading --help as a path"
                       % (tool.name, ", ".join(stray)))


@pytest.mark.parametrize("tool", BOARD_TOOLS, ids=BOARD_IDS)
def test_a_board_tool_asks_which_board(tool, tmp_path):
    """A tool that puts a line on the wire has to be told which board.

    The line state is opposite across the boards, so a tool that guesses
    chip-resets every board but the Waveshare P4 (`tools/p4_autotest.py`'s
    `add_board_args`). Read off the HELP TEXT, because that is what a person
    reads and what a rename would leave behind.
    """
    if tool.name in NO_BOARD_FLAG:
        pytest.skip("it is the driver itself; its tour takes --port")
    done = _help(tool, tmp_path)
    assert "--board" in done.stdout, (
        "%s sends serial verbs but its help offers no --board" % tool.name)


@pytest.mark.parametrize("tool", BOARD_TOOLS, ids=BOARD_IDS)
def test_serial_verbs_are_words_the_dev_channel_knows(tool):
    """Every verb the tool sends is dispatched by `DevChannel.run`.

    An unknown one costs a trip to a board to learn: the console answers
    `REMOTE ? <line>` and carries on, so the tool reads a missing reply as a
    timeout rather than as a word nobody implements.
    """
    unknown = {verb: line for verb, line in _verbs_sent(tool).items()
               if verb not in DEV_COMMANDS}
    assert not unknown, (
        "%s sends %s, which runtime/dev_channel.py does not dispatch"
        % (tool.name, ", ".join("%r (line %d)" % (v, n)
                                for v, n in sorted(unknown.items()))))
